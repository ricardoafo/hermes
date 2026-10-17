"""The ADR-021 §5 backtest funnel: the eight conventions that bind strategies from v2 on.

:func:`evaluate_funnel` is a pure function of the run's evidence and the
thresholds its signed pre-registration states. It returns one
:class:`~hermes.application.calibration.certification_gates.GateCheck` per
criterion, so the operator sees every pass and fail. Nothing here is
hard-coded: :func:`parse_funnel_front_matter` refuses a pre-registration that
does not state every threshold.

The §9 rows and the DSR stay with ``hermes certify``; this module does not
change them or v1's gate.

Reference: ADR-021 §5; ADR-012 D-06, D-11; ADR-023 §4, §7;
openspec/changes/v2-backtest-funnel/change.md.
"""

import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from typing import Literal

from hermes.application.calibration.certification_gates import GateCheck
from hermes.domain.statistics.overfitting import probability_of_backtest_overfitting
from hermes.domain.statistics.performance import daily_returns
from hermes.domain.statistics.period_returns import (
    PeriodReturn,
    annualised_return,
    calendar_year_returns,
    stretch_return,
)
from hermes.domain.statistics.sharpe import sharpe_ratio, sharpe_t_statistic

__all__ = [
    "FUNNEL_CHECK_NAMES",
    "CostStress",
    "FunnelEvidence",
    "FunnelEvidenceError",
    "FunnelThresholdError",
    "FunnelThresholds",
    "Regime",
    "evaluate_funnel",
    "parse_funnel_front_matter",
    "validate_evidence",
]

FUNNEL_CHECK_NAMES = (
    "pbo",
    "t_stat",
    "trade_count",
    "cost_stress",
    "positive_years",
    "bull_return",
    "bear_return",
    "validation_vs_train",
    "neighbours",
    "correlation",
)
"""Check names in output order; single tokens so a summary can keep name and verdict only."""

_NOT_COMPUTED = "not computed"
_MAX_CSCV_BLOCKS = 20
"""C(20, 10) = 184,756 halves; more would take minutes per run (change Risks)."""
_MIN_POINTS = 2

RegimeKind = Literal["bull", "bear"]


class FunnelThresholdError(ValueError):
    """The pre-registration does not state a usable funnel threshold."""


class FunnelEvidenceError(ValueError):
    """The funnel evidence is malformed or reaches the OOS window."""


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Regime:
    """A pre-registered market stretch, inclusive of both days."""

    kind: RegimeKind
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class FunnelThresholds:
    """Every number the funnel compares against, as the pre-registration states it.

    Key suffixes give the comparison: ``_max`` <=, ``_min`` >=, ``_above`` >,
    ``_below`` < (change D-3).
    """

    train_end: date
    oos_start: date
    cscv_blocks: int
    pbo_max: float
    t_stat_above: float
    trade_count_min: int
    cost_stress_multiplier: float
    positive_year_share_above: float
    validation_to_train_return_min: float
    neighbour_sharpe_ratio_min: float
    return_correlation_below: float
    regimes: tuple[Regime, ...]


_THRESHOLD_KEYS = tuple(f.name for f in fields(FunnelThresholds))


def _date(key: str, value: object) -> date:
    if isinstance(value, datetime):
        raise FunnelThresholdError(f"{key} must be a date, got a date and time {value!r}")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise FunnelThresholdError(f"{key} must be a date, got {value!r}")


def _number(key: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise FunnelThresholdError(f"{key} must be a number, got {value!r}")
    return float(value)


def _bounded(key: str, value: object, ok: Callable[[float], bool], rule: str) -> float:
    number = _number(key, value)
    if not ok(number):
        raise FunnelThresholdError(f"{key} must be {rule}, got {number:g}")
    return number


def _regimes(value: object, oos_start: date) -> tuple[Regime, ...]:
    key = "backtest_funnel.regimes"
    if not isinstance(value, list):
        raise FunnelThresholdError(f"{key} must be a list of stretches")
    out: list[Regime] = []
    for index, item in enumerate(value):
        where = f"{key}[{index}]"
        if not isinstance(item, Mapping) or set(item) != {"kind", "start", "end"}:
            raise FunnelThresholdError(f"{where} needs exactly kind, start and end")
        kind = item["kind"]
        if kind not in ("bull", "bear"):
            raise FunnelThresholdError(f"{where} kind must be bull or bear, got {kind!r}")
        start = _date(f"{where}.start", item["start"])
        end = _date(f"{where}.end", item["end"])
        if start > end:
            raise FunnelThresholdError(f"{where} has its start after its end")
        if end >= oos_start:
            raise FunnelThresholdError(f"{where} reaches oos_start {oos_start}")
        out.append(Regime("bull" if kind == "bull" else "bear", start, end))
    for kind in ("bull", "bear"):
        if not any(r.kind == kind for r in out):
            raise FunnelThresholdError(f"{key} needs at least one {kind} stretch")
    return tuple(out)


def parse_funnel_front_matter(front_matter: Mapping[str, object]) -> FunnelThresholds:
    """Return the funnel thresholds a signed pre-registration's front matter states.

    Args:
        front_matter: The parsed YAML front matter of the pre-registration.

    Returns:
        The typed thresholds.

    Raises:
        FunnelThresholdError: The document is not signed, has no
            ``backtest_funnel`` block, or a key is missing, unknown or out of
            range. The message names the key.
    """
    experiment = front_matter.get("experiment_id")
    if not isinstance(experiment, str) or not experiment:
        raise FunnelThresholdError("pre-registration has no experiment_id")
    status = front_matter.get("status")
    if status != "SIGNED":
        raise FunnelThresholdError(f"pre-registration status is {status!r}, not SIGNED")
    block = front_matter.get("backtest_funnel")
    if block is None:
        raise FunnelThresholdError(f"pre-registration {experiment} has no backtest_funnel block")
    if not isinstance(block, Mapping):
        raise FunnelThresholdError("backtest_funnel must be a mapping")
    unknown = sorted(str(k) for k in set(block) - set(_THRESHOLD_KEYS))
    if unknown:
        raise FunnelThresholdError(f"backtest_funnel has unknown keys: {unknown}")
    for key in _THRESHOLD_KEYS:
        if key not in block:
            raise FunnelThresholdError(f"backtest_funnel.{key} is missing")

    def label(name: str) -> str:
        return f"backtest_funnel.{name}"

    train_end = _date(label("train_end"), block["train_end"])
    oos_start = _date(label("oos_start"), block["oos_start"])
    if train_end >= oos_start:
        raise FunnelThresholdError("backtest_funnel.train_end must be before oos_start")
    blocks = block["cscv_blocks"]
    if (
        isinstance(blocks, bool)
        or not isinstance(blocks, int)
        or blocks % 2
        or not _MIN_POINTS <= blocks <= _MAX_CSCV_BLOCKS
    ):
        raise FunnelThresholdError(
            f"{label('cscv_blocks')} must be an even integer from 2 to {_MAX_CSCV_BLOCKS}, "
            f"got {blocks!r}"
        )
    trades = block["trade_count_min"]
    if isinstance(trades, bool) or not isinstance(trades, int) or trades < 1:
        raise FunnelThresholdError(
            f"{label('trade_count_min')} must be a positive integer, got {trades!r}"
        )
    return FunnelThresholds(
        train_end=train_end,
        oos_start=oos_start,
        cscv_blocks=blocks,
        pbo_max=_bounded(
            label("pbo_max"), block["pbo_max"], lambda v: 0.0 <= v <= 1.0, "from 0 to 1"
        ),
        t_stat_above=_number(label("t_stat_above"), block["t_stat_above"]),
        trade_count_min=trades,
        cost_stress_multiplier=_bounded(
            label("cost_stress_multiplier"),
            block["cost_stress_multiplier"],
            lambda v: v >= 1.0,
            "at least 1",
        ),
        positive_year_share_above=_bounded(
            label("positive_year_share_above"),
            block["positive_year_share_above"],
            lambda v: 0.0 <= v < 1.0,
            "from 0 to below 1",
        ),
        validation_to_train_return_min=_number(
            label("validation_to_train_return_min"), block["validation_to_train_return_min"]
        ),
        neighbour_sharpe_ratio_min=_number(
            label("neighbour_sharpe_ratio_min"), block["neighbour_sharpe_ratio_min"]
        ),
        return_correlation_below=_bounded(
            label("return_correlation_below"),
            block["return_correlation_below"],
            lambda v: -1.0 < v <= 1.0,
            "above -1 and at most 1",
        ),
        regimes=_regimes(block["regimes"], oos_start),
    )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostStress:
    """The same run replayed with fees and slippage multiplied."""

    multiplier: float
    daily_equity: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class FunnelEvidence:
    """What one Train + Validation run produced for the funnel. ``None`` means not computed.

    ``daily_equity[i]`` is the equity at the end of ``start_date + i`` days.
    Trial, neighbour and running-strategy series hold one return per day
    after the first, over the same days.
    """

    strategy_id: str
    start_date: date
    daily_equity: tuple[float, ...]
    trade_count: int | None = None
    cost_stress: CostStress | None = None
    trials: Mapping[str, tuple[float, ...]] | None = None
    neighbour_trial_ids: tuple[str, ...] | None = None
    running_strategies: Mapping[str, tuple[float, ...]] | None = None

    @property
    def end_date(self) -> date:
        """The day of the last equity point."""
        return self.start_date + timedelta(days=len(self.daily_equity) - 1)


def validate_evidence(evidence: FunnelEvidence, thresholds: FunnelThresholds) -> FunnelEvidence:
    """Return ``evidence`` when every series covers the same days before the OOS start.

    Args:
        evidence: The parsed evidence.
        thresholds: The pre-registration's thresholds, for ``oos_start``.

    Returns:
        ``evidence``, unchanged.

    Raises:
        FunnelEvidenceError: Too few or non-positive equity points, a series
            of another length, a neighbour that is not a trial, or a last day
            on or after the OOS start (change D-7).
    """
    points = len(evidence.daily_equity)
    if points < _MIN_POINTS:
        raise FunnelEvidenceError(f"daily_equity needs at least {_MIN_POINTS} points")
    if any(v <= 0.0 for v in evidence.daily_equity):
        raise FunnelEvidenceError("daily_equity values must be positive")
    if evidence.end_date >= thresholds.oos_start:
        raise FunnelEvidenceError(
            f"evidence ends on {evidence.end_date}, which reaches the OOS start "
            f"{thresholds.oos_start}"
        )
    stress = evidence.cost_stress
    if stress is not None:
        if len(stress.daily_equity) != points:
            raise FunnelEvidenceError(
                f"cost_stress.daily_equity has {len(stress.daily_equity)} points, expected {points}"
            )
        if any(v <= 0.0 for v in stress.daily_equity):
            raise FunnelEvidenceError("cost_stress.daily_equity values must be positive")
    for section, series in (
        ("trials", evidence.trials),
        ("running_strategies", evidence.running_strategies),
    ):
        for name, values in (series or {}).items():
            if len(values) != points - 1:
                raise FunnelEvidenceError(
                    f"{section}.{name} has {len(values)} returns, expected {points - 1}"
                )
    for neighbour in evidence.neighbour_trial_ids or ():
        if neighbour not in (evidence.trials or {}):
            raise FunnelEvidenceError(f"neighbour {neighbour} is not in trials")
    return evidence


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _not_computed(name: str, requirement: str) -> GateCheck:
    return GateCheck(name, requirement, _NOT_COMPUTED, passed=False)


def _pbo(evidence: FunnelEvidence, t: FunnelThresholds) -> GateCheck:
    requirement = f"<= {t.pbo_max:g} (CSCV, {t.cscv_blocks} blocks)"
    if evidence.trials is None:
        return _not_computed("pbo", requirement)
    try:
        pbo = probability_of_backtest_overfitting(
            list(evidence.trials.values()), blocks=t.cscv_blocks
        )
    except ValueError:
        return _not_computed("pbo", requirement)
    return GateCheck("pbo", requirement, f"{pbo:g}", pbo <= t.pbo_max)


def _t_stat(returns: Sequence[float], t: FunnelThresholds) -> GateCheck:
    requirement = f"> {t.t_stat_above:g}"
    value = sharpe_t_statistic(returns)
    if value is None:
        return _not_computed("t_stat", requirement)
    return GateCheck("t_stat", requirement, f"{value:g}", value > t.t_stat_above)


def _trade_count(evidence: FunnelEvidence, t: FunnelThresholds) -> GateCheck:
    requirement = f">= {t.trade_count_min}"
    count = evidence.trade_count
    if count is None:
        return _not_computed("trade_count", requirement)
    return GateCheck("trade_count", requirement, str(count), count >= t.trade_count_min)


def _cost_stress(evidence: FunnelEvidence, t: FunnelThresholds) -> GateCheck:
    requirement = f"return > 0 at >= {t.cost_stress_multiplier:g}x costs"
    stress = evidence.cost_stress
    if stress is None or len(stress.daily_equity) < _MIN_POINTS:
        return _not_computed("cost_stress", requirement)
    if stress.multiplier < t.cost_stress_multiplier:
        return GateCheck("cost_stress", requirement, f"run at {stress.multiplier:g}x", False)
    total = stress.daily_equity[-1] / stress.daily_equity[0] - 1.0
    return GateCheck("cost_stress", requirement, f"{total:g}", total > 0.0)


def _positive_years(evidence: FunnelEvidence, t: FunnelThresholds) -> GateCheck:
    requirement = f"share > {t.positive_year_share_above:g}"
    years = calendar_year_returns(evidence.start_date, evidence.daily_equity)
    if not years:
        return _not_computed("positive_years", requirement)
    positive = sum(1 for r in years.values() if r > 0.0)
    share = positive / len(years)
    return GateCheck(
        "positive_years",
        requirement,
        f"{share:g} ({positive} of {len(years)})",
        share > t.positive_year_share_above,
    )


def _regime(evidence: FunnelEvidence, t: FunnelThresholds, kind: RegimeKind) -> GateCheck:
    name = f"{kind}_return"
    stretches = [r for r in t.regimes if r.kind == kind]
    requirement = f"> 0 over {len(stretches)} {kind} stretches"
    growth = 1.0
    try:
        for stretch in stretches:
            period = stretch_return(
                evidence.start_date, evidence.daily_equity, stretch.start, stretch.end
            )
            growth *= 1.0 + period.total
    except ValueError:
        return _not_computed(name, requirement)
    if not stretches:
        return _not_computed(name, requirement)
    total = growth - 1.0
    return GateCheck(name, requirement, f"{total:g}", total > 0.0)


def _validation_vs_train(evidence: FunnelEvidence, t: FunnelThresholds) -> GateCheck:
    requirement = f">= {t.validation_to_train_return_min:g} x Train (annualised)"
    start, equity = evidence.start_date, evidence.daily_equity
    try:
        train: PeriodReturn = stretch_return(start, equity, start, t.train_end)
        validation = stretch_return(
            start, equity, t.train_end + timedelta(days=1), evidence.end_date
        )
    except ValueError:
        return _not_computed("validation_vs_train", requirement)
    train_rate = annualised_return(train)
    validation_rate = annualised_return(validation)
    if train_rate <= 0.0:
        return GateCheck("validation_vs_train", requirement, f"Train {train_rate:g}", False)
    ratio = validation_rate / train_rate
    return GateCheck(
        "validation_vs_train",
        requirement,
        f"{ratio:g}",
        validation_rate >= t.validation_to_train_return_min * train_rate,
    )


def _neighbours(
    evidence: FunnelEvidence, returns: Sequence[float], t: FunnelThresholds
) -> GateCheck:
    requirement = f"Sharpe >= {t.neighbour_sharpe_ratio_min:g} x base, every neighbour"
    ids = evidence.neighbour_trial_ids
    trials = evidence.trials
    if not ids or trials is None or any(i not in trials for i in ids):
        return _not_computed("neighbours", requirement)
    base = sharpe_ratio(returns)
    if base is None or base <= 0.0:
        return GateCheck("neighbours", requirement, "base Sharpe <= 0", False)
    ratios: list[float] = []
    for neighbour in ids:
        sharpe = sharpe_ratio(trials[neighbour])
        if sharpe is None:
            return GateCheck(
                "neighbours", requirement, f"neighbour {neighbour} {_NOT_COMPUTED}", False
            )
        ratios.append(sharpe / base)
    worst = min(ratios)
    return GateCheck("neighbours", requirement, f"{worst:g}", worst >= t.neighbour_sharpe_ratio_min)


def _correlation(
    evidence: FunnelEvidence, returns: Sequence[float], t: FunnelThresholds
) -> GateCheck:
    requirement = f"< {t.return_correlation_below:g} with every running strategy"
    running = evidence.running_strategies
    if running is None:
        return _not_computed("correlation", requirement)
    if not running:
        return GateCheck("correlation", requirement, "none running", True)
    try:
        highest = max(statistics.correlation(returns, series) for series in running.values())
    except statistics.StatisticsError:
        return _not_computed("correlation", requirement)
    return GateCheck(
        "correlation", requirement, f"{highest:g}", highest < t.return_correlation_below
    )


def evaluate_funnel(
    evidence: FunnelEvidence, thresholds: FunnelThresholds
) -> tuple[GateCheck, ...]:
    """Evaluate every ADR-021 §5 funnel criterion on one Train + Validation run.

    Pure. A statistic that cannot be computed fails its check. Call
    :func:`validate_evidence` first; this function does not refuse.

    Args:
        evidence: The run's evidence.
        thresholds: The signed pre-registration's thresholds.

    Returns:
        One check per name in :data:`FUNNEL_CHECK_NAMES`, in that order.
    """
    returns = daily_returns(evidence.daily_equity)
    return (
        _pbo(evidence, thresholds),
        _t_stat(returns, thresholds),
        _trade_count(evidence, thresholds),
        _cost_stress(evidence, thresholds),
        _positive_years(evidence, thresholds),
        _regime(evidence, thresholds, "bull"),
        _regime(evidence, thresholds, "bear"),
        _validation_vs_train(evidence, thresholds),
        _neighbours(evidence, returns, thresholds),
        _correlation(evidence, returns, thresholds),
    )
