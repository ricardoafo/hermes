"""``hermes certify`` — evaluate the next certification gate for a strategy.

Loads the strategy's certification record, applies the reverts (parameter
change, drift breach, live kill), evaluates the gate out of the current state,
prints one line per check with its evidence, and records the outcome. Only a
passed gate advances the state; a failed one-shot OOS test is recorded too,
because the shot is spent either way (pre-registration §7).

``--sign-off NAME`` asks for the live-approval phrase on an interactive
terminal and refuses when stdin is not one, so a script cannot approve live
trading unattended (issue #949).

``--shadow-parity FILE`` reads the M-09 and M-10 statuses ``hermes parity
window --out FILE`` wrote for the gate out of ``BACKTEST_CERTIFIED`` (ADR-021
Done rule 3, issue #965); the recorded evidence hash covers it.

``swing-mvp1-v2`` is judged by pre-registration v2: its backtest gate is DSR
≥ 0.95, v1's §9 secondary rows and the ADR-021 §5 funnel, read from
``--funnel-evidence`` (change v2-certify-gate).

Exit codes: 0 gate passed and recorded, 1 gate failed or refused, 2 usage,
3 config, evidence or store error.

Reference: openspec/changes/355-certification-gates/change.md;
openspec/changes/v2-certify-gate/change.md;
openspec/changes/965-shadow-parity-window/change.md; ADR-014.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.application.calibration.backtest_funnel import (
    FunnelEvidence,
    FunnelEvidenceError,
    FunnelThresholds,
    validate_evidence,
)
from hermes.application.calibration.certification_gates import (
    LIVE_APPROVAL_PHRASE,
    CertificationRecord,
    GateEvidence,
    GateReport,
    LiveEvidence,
    OperatorSignOff,
    ShadowEvidence,
    WindowMetrics,
    apply_guardrails,
    evaluate_next_gate,
    record_outcome,
    v1_parameter_fingerprint,
)
from hermes.application.calibration.certification_gates_v2 import (
    V2_STRATEGY_ID,
    V2BacktestRules,
    evaluate_v2_gate,
    parse_v2_backtest_rules,
)
from hermes.application.calibration.shadow_parity import MetricStatus
from hermes.application.calibration.window_metrics import (
    PerformanceRecord,
    TrialStats,
    compute_window_metrics,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.statistics.performance import TradeResult
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.funnel import load_funnel_evidence, read_front_matter
from hermes.infrastructure.config import HermesConfig
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.time import now_utc
from hermes.ports.certification import CertificationStoreError, CertificationStorePort

__all__ = [
    "EvidenceError",
    "apply_performance",
    "apply_shadow_parity",
    "load_evidence",
    "main",
    "run",
]

_USAGE = (
    "Usage: hermes certify <strategy_id> [--evidence FILE] [--store-dir DIR]\n"
    "                      [--config-dir DIR] [--sign-off NAME]\n"
    "                      [--shadow-parity FILE]\n"
    "                      [--funnel-evidence FILE]  (swing-mvp1-v2 only)\n"
    "\n"
    "Evaluates the next certification gate (pre-registration §7) and prints every\n"
    "check with its evidence. A passed gate is recorded; a failed one is not, except\n"
    "the one-shot OOS test, which is recorded either way.\n"
    "--shadow-parity reads the M-09/M-10 statuses `hermes parity window --out` wrote.\n"
    f"--sign-off asks you to type {LIVE_APPROVAL_PHRASE} on an interactive terminal.\n"
    "Exit codes: 0 passed, 1 failed or refused, 2 usage, 3 config/evidence/store.\n"
)

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


_Gate = Callable[[CertificationRecord, GateEvidence, datetime], GateReport]
"""Evaluates the next gate from the guarded record, the evidence and the time."""


class EvidenceError(ValueError):
    """The evidence file is malformed."""


def main(
    argv: list[str],
    *,
    stream: TextIO,
    err: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    """Parse ``argv`` and run ``hermes certify``.

    Args:
        argv: Arguments after the ``certify`` token.
        stream: Output stream; the live-approval prompt is written here.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.
        stdin: Where ``--sign-off`` reads the typed phrase; defaults to
            ``sys.stdin`` and must be an interactive terminal.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    stdin = stdin or sys.stdin
    parsed, code = _parse(argv, stream, err)
    if parsed is not None and parsed.sign_off is not None and not stdin.isatty():
        err.write(
            f"ERROR: --sign-off needs an interactive terminal to type {LIVE_APPROVAL_PHRASE}; "
            "stdin is not one, so nothing was approved.\n"
        )
        parsed, code = None, 2
    if parsed is None:
        return code

    try:
        config = load_config(parsed.config_dir)
        fingerprint = _fingerprint_for(config, parsed.strategy_id)
        evidence = load_evidence(parsed.evidence) if parsed.evidence else GateEvidence()
        if parsed.performance:
            evidence = apply_performance(evidence, parsed.performance)
        if parsed.shadow_parity:
            evidence = apply_shadow_parity(evidence, parsed.shadow_parity, parsed.strategy_id)
    except (ConfigError, serve_module.StartupError) as exc:
        err.write(f"ERROR: config: {exc}\n")
        return 3
    except (OSError, EvidenceError) as exc:
        err.write(f"ERROR: evidence: {exc}\n")
        return 3
    try:
        pre_reg = serve_module.pre_registration_for(parsed.config_dir)
    except (OSError, ValueError) as exc:
        err.write(f"ERROR: pre-registration: {exc}\n")
        return 3
    gate: _Gate | None = None
    if parsed.strategy_id == V2_STRATEGY_ID:
        prepared = _prepare_v2(pre_reg, evidence, parsed.funnel_evidence, err)
        if isinstance(prepared, int):
            return prepared
        gate, evidence = prepared

    now = now_utc()
    if parsed.sign_off is not None:
        stream.write(
            f"Live approval for {parsed.strategy_id} by {parsed.sign_off}. "
            f"Type {LIVE_APPROVAL_PHRASE} to confirm: "
        )
        stream.flush()
        typed = stdin.readline().rstrip("\r\n")
        evidence = replace(evidence, sign_off=OperatorSignOff(parsed.sign_off, typed, now))

    store_dir = parsed.store_dir or serve_module.certification_dir_for(serve_module.DEFAULT_DB_PATH)
    return run(
        parsed.strategy_id,
        evidence,
        store=JsonCertificationStore(store_dir),
        fingerprint=fingerprint,
        stream=stream,
        err=err,
        clock=lambda: now,
        pre_reg=pre_reg,
        gate=gate,
    )


def _fingerprint_for(config: HermesConfig, strategy_id: str) -> str:
    """v1 fingerprints the enabled entry, as serve does; v2 its own entry, enabled or not."""
    if strategy_id != V2_STRATEGY_ID:
        return serve_module.parameter_fingerprint_for(config)
    roster = next((s for s in config.strategies.strategies if s.name == strategy_id), None)
    if roster is None:
        raise serve_module.StartupError(f"strategies.yaml has no {strategy_id!r} entry")
    return v1_parameter_fingerprint(
        capital_budget=roster.capital_budget, universe=config.market_data.pairs
    )


def _v2_rules(v1: PreRegistration) -> V2BacktestRules:
    """Read v2's rules from the signed v2 file beside the v1 file that was found."""
    text = v1.pre_reg_path().with_name("pre-registration-v2.md").read_text(encoding="utf-8")
    return parse_v2_backtest_rules(text, read_front_matter(text))


def _apply_funnel(
    evidence: GateEvidence, path: Path | None, thresholds: FunnelThresholds
) -> tuple[FunnelEvidence | None, GateEvidence]:
    """Load and validate the funnel evidence; the recorded hash then covers that file too."""
    if path is None:
        return None, evidence
    raw = path.read_bytes()
    funnel = validate_evidence(load_funnel_evidence(path, strategy_id=V2_STRATEGY_ID), thresholds)
    digest = hashlib.sha256(
        ((evidence.sha256 or "") + hashlib.sha256(raw).hexdigest()).encode("ascii")
    ).hexdigest()
    return funnel, replace(evidence, sha256=digest)


def _prepare_v2(
    v1: PreRegistration, evidence: GateEvidence, funnel_path: Path | None, err: TextIO
) -> tuple[_Gate, GateEvidence] | int:
    """Return v2's gate and the evidence with the funnel file hashed in, or exit code 3."""
    try:
        rules = _v2_rules(v1)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        err.write(f"ERROR: pre-registration: {exc}\n")
        return 3
    try:
        funnel, evidence = _apply_funnel(evidence, funnel_path, rules.funnel)
    except (OSError, FunnelEvidenceError) as exc:
        err.write(f"ERROR: evidence: {exc}\n")
        return 3

    def gate(record: CertificationRecord, submitted: GateEvidence, now: datetime) -> GateReport:
        return evaluate_v2_gate(record, submitted, now=now, v1=v1, rules=rules, funnel=funnel)

    return gate, evidence


def _parse(argv: list[str], stream: TextIO, err: TextIO) -> tuple[argparse.Namespace | None, int]:
    """Parse and validate ``argv``; on ``None`` the int is the exit code."""
    parser = argparse.ArgumentParser(prog="hermes certify", add_help=False)
    parser.add_argument("strategy_id", nargs="?")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--performance", type=Path)
    parser.add_argument("--store-dir", type=Path)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--sign-off", default=None)
    parser.add_argument("--funnel-evidence", type=Path)
    parser.add_argument("--shadow-parity", type=Path)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write("ERROR: could not parse `hermes certify` arguments.\n" + _USAGE)
        return None, 2
    if parsed.help:
        stream.write(_USAGE)
        return None, 0
    if not parsed.strategy_id:
        err.write("ERROR: strategy_id is required.\n" + _USAGE)
        return None, 2
    if parsed.strategy_id not in (serve_module.SHADOW_STRATEGY_NAME, V2_STRATEGY_ID):
        err.write(
            f"ERROR: only {serve_module.SHADOW_STRATEGY_NAME!r} (swing-mvp1-v1) and "
            f"{V2_STRATEGY_ID!r} (swing-mvp1-v2) are pre-registered; got {parsed.strategy_id!r}.\n"
        )
        return None, 2
    if parsed.funnel_evidence is not None and parsed.strategy_id != V2_STRATEGY_ID:
        err.write(f"ERROR: --funnel-evidence applies to {V2_STRATEGY_ID!r} only.\n" + _USAGE)
        return None, 2
    return parsed, 0


def run(
    strategy_id: str,
    evidence: GateEvidence,
    *,
    store: CertificationStorePort,
    fingerprint: str,
    stream: TextIO,
    clock: Callable[[], datetime],
    pre_reg: PreRegistration | None = None,
    err: TextIO | None = None,
    gate: _Gate | None = None,
) -> int:
    """Evaluate and record the next gate for ``strategy_id``.

    Args:
        strategy_id: The strategy to certify.
        evidence: The submitted evidence.
        store: Where the record lives.
        fingerprint: Fingerprint of the parameters running now.
        stream: Output stream.
        clock: Source of the current UTC time.
        pre_reg: Threshold source; defaults to the signed pre-registration.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.
        gate: Evaluates the next gate; defaults to v1's gates read from ``pre_reg``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    now = clock()
    rules = pre_reg or PreRegistration()
    try:
        stored = store.load(strategy_id) or CertificationRecord(strategy_id)
    except CertificationStoreError as exc:
        err.write(f"ERROR: store: {exc}\n")
        return 3

    record = apply_guardrails(stored, fingerprint=fingerprint, evidence=evidence, now=now)
    if record is not stored:
        stream.write(
            f"REVERTED: {stored.state} -> {record.state}: {record.transitions[-1].reason}\n"
        )

    if gate is None:
        report = evaluate_next_gate(record, evidence, now=now, pre_reg=rules)
    else:
        report = gate(record, evidence, now)
    _print_report(report, stream)
    updated = record_outcome(
        record, report, fingerprint=fingerprint, now=now, evidence_sha256=evidence.sha256
    )

    if updated is not stored:
        try:
            store.save(updated)
        except CertificationStoreError as exc:
            err.write(f"ERROR: store: {exc}\n")
            return 3
    if updated.state != record.state:
        stream.write(
            f"RECORDED: {record.state} -> {updated.state}. "
            "Log this transition in the lab notebook (§12).\n"
        )
    return 0 if report.passed else 1


def _print_report(report: GateReport, stream: TextIO) -> None:
    target = report.to_state or "-"
    stream.write(f"# hermes certify {report.strategy_id}: {report.from_state} -> {target}\n")
    if report.refused_reason:
        stream.write(f"REFUSED: {report.refused_reason}\n")
        return
    for check in report.checks:
        mark = "PASS" if check.passed else "FAIL"
        stream.write(
            f"{mark}  {check.name:<24} observed {check.observed:<22} required {check.requirement}\n"
        )
    stream.write("GATE PASSED\n" if report.passed else "GATE FAILED\n")


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
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise EvidenceError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvidenceError(f"{path} must hold a JSON object")
    window, record = _performance_record(payload)
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


_PARITY_KEYS = frozenset({"strategy_id", "since", "until", "m09", "m10"})


def apply_shadow_parity(evidence: GateEvidence, path: Path, strategy_id: str) -> GateEvidence:
    """Add the M-09 and M-10 statuses of a ``hermes parity window`` file to ``evidence``.

    Args:
        evidence: Evidence loaded so far.
        path: The file ``hermes parity window --out`` wrote.
        strategy_id: The strategy being certified; the file must name it.

    Returns:
        ``evidence`` with the shadow statuses set; its hash then covers the file too.

    Raises:
        EvidenceError: The file is not a parity window file of ``strategy_id``.
        OSError: The file cannot be read.
    """
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise EvidenceError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _PARITY_KEYS:
        raise EvidenceError(f"{path} must hold exactly {sorted(_PARITY_KEYS)}")
    if payload["strategy_id"] != strategy_id:
        raise EvidenceError(f"{path} is for {payload['strategy_id']!r}, not {strategy_id!r}")
    m09, m10 = (_status(path, payload, key) for key in ("m09", "m10"))
    digest = hashlib.sha256(
        ((evidence.sha256 or "") + hashlib.sha256(raw).hexdigest()).encode("ascii")
    ).hexdigest()
    shadow = replace(evidence.shadow or ShadowEvidence(), m09=m09, m10=m10)
    return replace(evidence, shadow=shadow, sha256=digest)


def _status(path: Path, payload: dict[str, Any], key: str) -> MetricStatus:
    try:
        return MetricStatus(payload[key])
    except ValueError as exc:
        raise EvidenceError(f"{path}: {key} must be pass, block or insufficient") from exc


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
