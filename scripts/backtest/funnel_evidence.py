"""The funnel replays of pre-registration v2 §8.5 and the ``hermes funnel`` evidence body.

Every backtest run of v2 also replays six neighbours, one parameter moved at a
time (Donchian 15 and 25, ADX gate 20 and 30, chandelier multiple 2.5 and
3.5), and the base parameters at twice the fees and slippage (§6.2). Each
replay is logged as its own trial. The evidence file holds the base run's
daily equity, the stress run's, and the daily returns of the base run and its
neighbours, the CSCV matrix of §8.5.

The replays override parameters of whatever strategy the driver loads and
scale whatever execution model it builds, so they do not depend on which
strategy runs.

Reference: openspec/changes/v2-funnel-evidence/change.md;
openspec/changes/v2-backtest-funnel/change.md Design §2.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any, Final

from hermes.application.execution.prereg_book import ExecutionModel
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.statistics.performance import daily_returns
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "COST_STRESS_MULTIPLIER",
    "FUNNEL_REPLAYS",
    "NEIGHBOUR_TRIAL_IDS",
    "FunnelReplay",
    "funnel_evidence",
]

COST_STRESS_MULTIPLIER: Final = 2
"""Pre-registration v2 §6.2: every fee and slippage times 2."""

FeesAt = Callable[[datetime, Pair], FeeSchedule]

_ONE_DAY: Final = timedelta(days=1)
_BASE_ID: Final = "base"


@dataclass(frozen=True, slots=True)
class FunnelReplay:
    """One replay of the funnel grid.

    Attributes:
        trial_id: The id of its series in the evidence and of its trial line.
        overrides: Strategy parameters it replaces; empty for the base and
            the cost stress.
        cost_multiplier: Factor on every execution bps and fee bps.
    """

    trial_id: str
    overrides: Mapping[str, Any] = field(default_factory=dict)
    cost_multiplier: int = 1

    def parameters(self, base: Mapping[str, Any]) -> dict[str, Any]:
        """Return the base strategy parameters with this replay's overrides.

        Args:
            base: The parameters of the loaded strategy.

        Returns:
            A new mapping; ``base`` is not changed.

        Raises:
            ValueError: An override names a parameter ``base`` does not have,
                or sets it to the base value (change D-2).
        """
        for name, value in self.overrides.items():
            if name not in base:
                raise ValueError(f"neighbour {self.trial_id}: the strategy has no {name}")
            if base[name] == value:
                raise ValueError(f"neighbour {self.trial_id} equals the base {name}={base[name]}")
        return {**base, **self.overrides}

    def model(self, base: ExecutionModel) -> ExecutionModel:
        """Return ``base`` with every ``*_bps`` cost times ``cost_multiplier``.

        Args:
            base: The execution model the driver built.

        Returns:
            ``base`` itself when the multiplier is 1.
        """
        if self.cost_multiplier == 1:
            return base
        scaled = {
            f.name: getattr(base, f.name) * self.cost_multiplier
            for f in fields(base)
            if f.name.endswith("_bps")
        }
        return replace(base, **scaled)

    def fees(self, fees_at: FeesAt) -> FeesAt:
        """Return a fee lookup whose every tier's bps are times ``cost_multiplier``.

        Args:
            fees_at: The fee lookup of the loaded history.

        Returns:
            ``fees_at`` itself when the multiplier is 1.
        """
        if self.cost_multiplier == 1:
            return fees_at
        multiplier = self.cost_multiplier

        def stressed(at: datetime, pair: Pair) -> FeeSchedule:
            schedule = fees_at(at, pair)
            tiers = tuple(
                replace(t, maker_bps=t.maker_bps * multiplier, taker_bps=t.taker_bps * multiplier)
                for t in schedule.tiers
            )
            return replace(schedule, tiers=tiers)

        return stressed

    def trial_fields(self) -> dict[str, Any]:
        """Return what this replay adds to its trial line's parameters (change D-4)."""
        if self.trial_id == _BASE_ID:
            return {}
        extra: dict[str, Any] = {"funnel.replay": self.trial_id}
        if self.cost_multiplier != 1:
            extra["execution.cost_multiplier"] = self.cost_multiplier
        return extra


def _neighbour(name: str, value: int | Decimal) -> FunnelReplay:
    return FunnelReplay(trial_id=f"{name}={value}", overrides={name: value})


_COST_STRESS: Final = FunnelReplay(
    trial_id=f"cost_stress={COST_STRESS_MULTIPLIER}", cost_multiplier=COST_STRESS_MULTIPLIER
)

FUNNEL_REPLAYS: Final[tuple[FunnelReplay, ...]] = (
    FunnelReplay(trial_id=_BASE_ID),
    _neighbour("donchian_period", 15),
    _neighbour("donchian_period", 25),
    _neighbour("adx_threshold", Decimal("20")),
    _neighbour("adx_threshold", Decimal("30")),
    _neighbour("chandelier_atr_mult", Decimal("2.5")),
    _neighbour("chandelier_atr_mult", Decimal("3.5")),
    _COST_STRESS,
)
"""The base run, the six §8.5 neighbours and the cost stress, in that order."""

NEIGHBOUR_TRIAL_IDS: Final[tuple[str, ...]] = tuple(
    r.trial_id for r in FUNNEL_REPLAYS if r.overrides
)


def _day(close_time: datetime) -> datetime:
    """Return the start of the UTC day a 1D bar closing at ``close_time`` covers."""
    covered = close_time - timedelta(microseconds=1)
    return covered.replace(hour=0, minute=0, second=0, microsecond=0)


def _equity(curve: Sequence[tuple[datetime, Decimal]]) -> list[float]:
    return [float(equity) for _, equity in curve]


def funnel_evidence(
    strategy_id: str,
    curves: Mapping[str, Sequence[tuple[datetime, Decimal]]],
    *,
    trade_count: int,
) -> dict[str, Any]:
    """Return the ``hermes funnel`` evidence body (``v2-backtest-funnel`` Design §2).

    Args:
        strategy_id: The strategy the run was for.
        curves: Each replay's ``(close_time, equity)`` daily points, by trial id;
            every id of ``FUNNEL_REPLAYS`` is required.
        trade_count: The base run's round trips.

    Returns:
        The JSON object, with ``running_strategies`` empty.

    Raises:
        ValueError: A replay is missing, the base curve is empty, a curve
            closes on other days than the base's, or the days are not
            consecutive.
    """
    missing = [r.trial_id for r in FUNNEL_REPLAYS if r.trial_id not in curves]
    if missing:
        raise ValueError(f"no daily equity for replays {missing}")
    base = curves[_BASE_ID]
    if not base:
        raise ValueError("the base run has no daily equity point")
    closes = [at for at, _ in base]
    for replay in FUNNEL_REPLAYS:
        if [at for at, _ in curves[replay.trial_id]] != closes:
            raise ValueError(f"replay {replay.trial_id} closes on other days than the base run")
    days = [_day(at) for at in closes]
    for before, after in pairwise(days):
        if after - before != _ONE_DAY:
            raise ValueError(
                f"daily equity is not one point per consecutive day: {before.date()} then "
                f"{after.date()}"
            )
    trial_ids = (_BASE_ID, *NEIGHBOUR_TRIAL_IDS)
    return {
        "strategy_id": strategy_id,
        "start_date": days[0].date().isoformat(),
        "daily_equity": _equity(base),
        "trade_count": trade_count,
        "cost_stress": {
            "multiplier": float(COST_STRESS_MULTIPLIER),
            "daily_equity": _equity(curves[_COST_STRESS.trial_id]),
        },
        "trials": {tid: daily_returns(_equity(curves[tid])) for tid in trial_ids},
        "neighbour_trial_ids": list(NEIGHBOUR_TRIAL_IDS),
        "running_strategies": {},
    }
