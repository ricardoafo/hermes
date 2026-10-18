"""Read the evidence files ``hermes certify`` and ``hermes funnel`` judge.

Three files: the gate evidence (``--evidence``), a window's performance record
(``--performance``) and the funnel evidence (``--funnel-evidence``). They live
in the application layer so the generic gate (:mod:`signed_gate`) can read a
batch candidate's files the same way the CLIs do. Absent keys mean not
computed.

Reference: openspec/changes/355-certification-gates/change.md;
openspec/changes/v2-certify-gate/change.md; issue #1058.
"""

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from hermes.application.calibration.backtest_funnel import (
    CostStress,
    FunnelEvidence,
    FunnelEvidenceError,
)
from hermes.application.calibration.certification_gates import (
    GateEvidence,
    LiveEvidence,
    ShadowEvidence,
    WindowMetrics,
)
from hermes.application.calibration.window_metrics import (
    PerformanceRecord,
    TrialStats,
    compute_window_metrics,
)
from hermes.domain.statistics.performance import TradeResult

__all__ = [
    "EvidenceError",
    "apply_performance",
    "load_evidence",
    "load_funnel_evidence",
    "load_performance",
]

_METRIC_KEYS = (
    "dsr",
    "bootstrap_ci_lower",
    "trade_count",
    "max_drawdown",
    "recovery_days",
    "profit_factor",
    "ulcer_index",
    "fees_to_gross_pnl",
    "win_rate",
)

_EVIDENCE_KEYS = frozenset(
    {
        "strategy_id",
        "start_date",
        "daily_equity",
        "trade_count",
        "cost_stress",
        "trials",
        "neighbour_trial_ids",
        "running_strategies",
    }
)


class EvidenceError(ValueError):
    """The evidence file is malformed."""


def load_evidence(path: Path) -> GateEvidence:
    """Parse an evidence JSON file. Absent keys mean not computed.

    Args:
        path: The evidence file.

    Returns:
        The parsed :class:`GateEvidence` with the file's SHA-256.

    Raises:
        EvidenceError: The file is not valid evidence.
        OSError: The file cannot be read.
    """
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise EvidenceError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvidenceError(f"{path} must hold a JSON object")
    unknown = set(payload) - {"backtest", "shadow", "oos", "live"}
    if unknown:
        raise EvidenceError(f"unknown sections: {sorted(unknown)}")

    backtest_section = _section(payload, "backtest")
    shadow_section = _section(payload, "shadow")
    live_section = _section(payload, "live")
    oos_section = _section(payload, "oos")
    return GateEvidence(
        backtest=_metrics(backtest_section, extra={"calibration_fill_count"}),
        calibration_fill_count=_int(backtest_section, "calibration_fill_count"),
        shadow=None
        if shadow_section is None
        else ShadowEvidence(
            days=_int(shadow_section, "days"),
            drift_p50_bps=_float(shadow_section, "drift_p50_bps"),
            kill_fired=_bool(shadow_section, "kill_fired"),
            drift_breached=_bool(shadow_section, "drift_breached"),
        ),
        oos=_metrics(oos_section, extra=set()),
        live=None
        if live_section is None
        else LiveEvidence(
            days=_int(live_section, "days"), kill_fired=_bool(live_section, "kill_fired")
        ),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


_WINDOWS = ("train_val", "oos")


def apply_performance(evidence: GateEvidence, path: Path) -> GateEvidence:
    """Compute a window's §9 metrics from a performance file and add them to ``evidence``.

    The file names its window: ``train_val`` fills the backtest metrics,
    ``oos`` fills the OOS metrics. The evidence hash then covers both files.

    Args:
        evidence: Evidence loaded from ``--evidence`` (or empty).
        path: The performance file.

    Returns:
        ``evidence`` with that window's metrics replaced by computed ones.

    Raises:
        EvidenceError: The file is not a valid performance record.
        OSError: The file cannot be read.
    """
    raw = path.read_bytes()
    window, record = _performance_record(_json_object(path, raw))
    try:
        metrics = compute_window_metrics(record)
    except ValueError as exc:
        raise EvidenceError(f"performance: {exc}") from exc
    digest = hashlib.sha256(
        ((evidence.sha256 or "") + hashlib.sha256(raw).hexdigest()).encode("ascii")
    ).hexdigest()
    if window == "oos":
        return replace(evidence, oos=metrics, sha256=digest)
    return replace(evidence, backtest=metrics, sha256=digest)


def load_performance(path: Path) -> tuple[str, PerformanceRecord]:
    """Parse a performance file without computing its metrics.

    Args:
        path: The performance file.

    Returns:
        The window it names (``train_val`` or ``oos``) and its record.

    Raises:
        EvidenceError: The file is not a valid performance record.
        OSError: The file cannot be read.
    """
    return _performance_record(_json_object(path, path.read_bytes()))


def _json_object(path: Path, raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise EvidenceError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvidenceError(f"{path} must hold a JSON object")
    return payload


def _performance_record(payload: dict[str, Any]) -> tuple[str, PerformanceRecord]:
    unknown = set(payload) - {"window", "daily_equity", "trades", "trials", "bootstrap_seed"}
    if unknown:
        raise EvidenceError(f"unknown performance keys: {sorted(unknown)}")
    window = payload.get("window")
    if window not in _WINDOWS:
        raise EvidenceError(f"window must be one of {list(_WINDOWS)}, got {window!r}")
    equity = payload.get("daily_equity")
    if not isinstance(equity, list):
        raise EvidenceError("daily_equity must be a list of numbers")
    trades = payload.get("trades")
    if not isinstance(trades, list) or not all(isinstance(t, dict) for t in trades):
        raise EvidenceError("trades must be a list of objects")
    seed = _int(payload, "bootstrap_seed")
    if seed is None:
        raise EvidenceError("bootstrap_seed is required so the interval is reproducible")
    trials = _section(payload, "trials")
    record = PerformanceRecord(
        daily_equity=tuple(_number(v, "daily_equity") for v in equity),
        trades=tuple(_trade(t) for t in trades),
        trials=None if trials is None else _trials(trials),
        bootstrap_seed=seed,
    )
    return str(window), record


def _number(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvidenceError(f"{key} values must be numbers, got {value!r}")
    return float(value)


def _trade(item: dict[str, Any]) -> TradeResult:
    if set(item) != {"gross_pnl", "fees"}:
        raise EvidenceError(f"each trade needs exactly gross_pnl and fees, got {sorted(item)}")
    return TradeResult(_number(item["gross_pnl"], "gross_pnl"), _number(item["fees"], "fees"))


def _trials(section: dict[str, Any]) -> TrialStats:
    count = _int(section, "count")
    variance = _float(section, "sharpe_variance")
    if count is None or count < 1 or variance is None or variance < 0.0:
        raise EvidenceError("trials needs count >= 1 and sharpe_variance >= 0")
    return TrialStats(count, variance)


def _section(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    section = payload.get(name)
    if section is None:
        return None
    if not isinstance(section, dict):
        raise EvidenceError(f"section {name!r} must be an object")
    return section


def _metrics(section: dict[str, Any] | None, *, extra: set[str]) -> WindowMetrics | None:
    if section is None:
        return None
    unknown = set(section) - set(_METRIC_KEYS) - extra
    if unknown:
        raise EvidenceError(f"unknown metrics: {sorted(unknown)}")
    return WindowMetrics(
        dsr=_float(section, "dsr"),
        bootstrap_ci_lower=_float(section, "bootstrap_ci_lower"),
        trade_count=_int(section, "trade_count"),
        max_drawdown=_float(section, "max_drawdown"),
        recovery_days=_float(section, "recovery_days"),
        profit_factor=_float(section, "profit_factor"),
        ulcer_index=_float(section, "ulcer_index"),
        fees_to_gross_pnl=_float(section, "fees_to_gross_pnl"),
        win_rate=_float(section, "win_rate"),
    )


def _float(section: dict[str, Any] | None, key: str) -> float | None:
    if section is None or section.get(key) is None:
        return None
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvidenceError(f"{key} must be a number, got {value!r}")
    return float(value)


def _int(section: dict[str, Any] | None, key: str) -> int | None:
    if section is None or section.get(key) is None:
        return None
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceError(f"{key} must be an integer, got {value!r}")
    return int(value)


def _bool(section: dict[str, Any] | None, key: str) -> bool | None:
    if section is None or section.get(key) is None:
        return None
    value = section[key]
    if not isinstance(value, bool):
        raise EvidenceError(f"{key} must be true or false, got {value!r}")
    return bool(value)


def load_funnel_evidence(path: Path, *, strategy_id: str) -> FunnelEvidence:
    """Parse a funnel evidence file (change Design §2). Absent sections mean not computed.

    Args:
        path: The evidence JSON file.
        strategy_id: The strategy the run must be for.

    Returns:
        The parsed evidence. Call ``validate_evidence`` before evaluating it.

    Raises:
        FunnelEvidenceError: The file is not valid funnel evidence.
        OSError: The file cannot be read.
    """
    try:
        payload = json.loads(path.read_bytes())
    except ValueError as exc:
        raise FunnelEvidenceError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FunnelEvidenceError(f"{path} must hold a JSON object")
    unknown = sorted(set(payload) - _EVIDENCE_KEYS)
    if unknown:
        raise FunnelEvidenceError(f"unknown evidence keys: {unknown}")
    if payload.get("strategy_id") != strategy_id:
        raise FunnelEvidenceError(
            f"evidence is for {payload.get('strategy_id')!r}, not {strategy_id!r}"
        )
    start = payload.get("start_date")
    try:
        start_date = date.fromisoformat(start) if isinstance(start, str) else None
    except ValueError:
        start_date = None
    if start_date is None:
        raise FunnelEvidenceError(f"start_date must be an ISO date, got {start!r}")
    trades = payload.get("trade_count")
    if trades is not None and (isinstance(trades, bool) or not isinstance(trades, int)):
        raise FunnelEvidenceError(f"trade_count must be an integer, got {trades!r}")
    neighbours = payload.get("neighbour_trial_ids")
    if neighbours is not None and (
        not isinstance(neighbours, list) or not all(isinstance(n, str) for n in neighbours)
    ):
        raise FunnelEvidenceError("neighbour_trial_ids must be a list of strings")
    return FunnelEvidence(
        strategy_id=strategy_id,
        start_date=start_date,
        daily_equity=_numbers(payload.get("daily_equity"), "daily_equity"),
        trade_count=trades,
        cost_stress=_cost_stress(payload.get("cost_stress")),
        trials=_series_map(payload.get("trials"), "trials"),
        neighbour_trial_ids=None if neighbours is None else tuple(neighbours),
        running_strategies=_series_map(payload.get("running_strategies"), "running_strategies"),
    )


def _numbers(value: object, key: str) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise FunnelEvidenceError(f"{key} must be a list of numbers")
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float) or not math.isfinite(item):
            raise FunnelEvidenceError(f"{key} values must be numbers, got {item!r}")
    return tuple(float(item) for item in value)


def _cost_stress(value: object) -> CostStress | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"multiplier", "daily_equity"}:
        raise FunnelEvidenceError("cost_stress needs exactly multiplier and daily_equity")
    multiplier = value["multiplier"]
    if isinstance(multiplier, bool) or not isinstance(multiplier, int | float):
        raise FunnelEvidenceError(f"cost_stress.multiplier must be a number, got {multiplier!r}")
    return CostStress(
        float(multiplier), _numbers(value["daily_equity"], "cost_stress.daily_equity")
    )


def _series_map(value: object, key: str) -> Mapping[str, tuple[float, ...]] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise FunnelEvidenceError(f"{key} must map names to lists of returns")
    return {str(name): _numbers(series, f"{key}.{name}") for name, series in value.items()}
