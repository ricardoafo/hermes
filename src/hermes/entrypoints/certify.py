"""``hermes certify`` — evaluate the next certification gate for a strategy.

Loads the strategy's certification record, applies the reverts (parameter
change, drift breach, live kill), evaluates the gate out of the current state,
prints one line per check with its evidence, and records the outcome. Only a
passed gate advances the state; a failed one-shot OOS test is recorded too,
because the shot is spent either way (pre-registration §7).

Toward SHADOW_CERTIFIED it also reports the shadow round trips the ledger
holds; under 30 the execution check is flagged weak (ADR-021 §3). The flag
changes no pass or fail rule.

``--sign-off NAME`` asks for the live-approval phrase on an interactive
terminal and refuses when stdin is not one, so a script cannot approve live
trading unattended (issue #949).

``--shadow-parity FILE`` reads the M-09 and M-10 statuses ``hermes parity
window --out FILE`` wrote for the gate out of ``BACKTEST_CERTIFIED`` (ADR-021
Done rule 3, issue #965). The file must start at day 0 of the latest counted
run (the record beside ``--db``) and cover the shadow length; the recorded
evidence hash covers it.

``swing-mvp1-v2`` is judged by pre-registration v2: its backtest gate is DSR
≥ 0.95, v1's §9 secondary rows and the ADR-021 §5 funnel, read from
``--funnel-evidence`` (change v2-certify-gate).

``--batch FILE`` judges a candidate of a signed research batch with the same
gate, every threshold read from the batch (ADR-026, issue #1058). Its DSR is
deflated by every trial in the experiment's trial log (``--trial-log``,
default the batch's). Evidence that ends on or before ``train_end`` is a
Train-only run: ``validation_vs_train`` prints ``NA`` and nothing is recorded,
because the gate out of UNTRUSTED is judged on Train + Validation.

Exit codes: 0 gate passed and recorded, 1 gate failed or refused, 2 usage,
3 config, evidence or store error.

Reference: openspec/archive/355-certification-gates/change.md;
openspec/archive/v2-certify-gate/change.md;
openspec/archive/965-shadow-parity-window/change.md; ADR-014.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Final, TextIO

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.adapters.console.shadow_records import RunsDirectory, ShadowRecordError, runs_dir_for
from hermes.adapters.files import LocalFiles
from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.application.calibration.backtest_funnel import (
    FunnelEvidence,
    FunnelEvidenceError,
    FunnelThresholds,
    validate_evidence,
)
from hermes.application.calibration.certification_gates import (
    LIVE_APPROVAL_PHRASE,
    GateEvidence,
    GateReport,
    OperatorSignOff,
    ShadowEvidence,
    apply_guardrails,
    evaluate_next_gate,
    parameter_fingerprint,
    record_outcome,
    v1_parameter_fingerprint,
)
from hermes.application.calibration.certification_gates_v2 import (
    V2_STRATEGY_ID,
    V2BacktestRules,
    evaluate_v2_gate,
    parse_v2_backtest_rules,
)
from hermes.application.calibration.evidence_files import (
    EvidenceError,
    apply_performance,
    load_evidence,
    load_funnel_evidence,
)
from hermes.application.calibration.research_batch import (
    BatchError,
    Candidate,
    ResearchBatch,
    load_batch,
)
from hermes.application.calibration.shadow_parity import MetricStatus
from hermes.application.calibration.signed_gate import (
    CandidateEvidence,
    candidate_gate_evidence,
    check_result,
    evaluate_signed_gate,
    evidence_split,
    require_v1_secondary,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.calibration.certification import CertificationRecord, CertificationState
from hermes.domain.calibration.trial_record import summarise_trials
from hermes.domain.errors import LedgerError, TrialLogError
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints._cli import add_config_dir_arg, add_db_arg
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.funnel import read_front_matter
from hermes.infrastructure.config import HermesConfig
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import load_config
from hermes.infrastructure.project_root import load_pre_registration
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

_FILES: Final = LocalFiles()

_USAGE = (
    "Usage: hermes certify <strategy_id> [--evidence FILE] [--store-dir DIR]\n"
    "                      [--config-dir DIR] [--db PATH] [--sign-off NAME]\n"
    "                      [--shadow-parity FILE]\n"
    "                      [--funnel-evidence FILE]  (swing-mvp1-v2 only)\n"
    "       hermes certify <strategy_id> --batch FILE --evidence FILE --performance FILE\n"
    "                      --funnel-evidence FILE [--trial-log FILE] [--store-dir DIR]\n"
    "                      [--config-dir DIR]\n"
    "\n"
    "Evaluates the next certification gate (pre-registration §7) and prints every\n"
    "check with its evidence. A passed gate is recorded; a failed one is not, except\n"
    "the one-shot OOS test, which is recorded either way.\n"
    "--shadow-parity reads the M-09/M-10 statuses `hermes parity window --out` wrote.\n"
    f"--sign-off asks you to type {LIVE_APPROVAL_PHRASE} on an interactive terminal.\n"
    "Exit codes: 0 passed, 1 failed or refused, 2 usage, 3 config/evidence/store.\n"
)


WEAK_ROUND_TRIPS = 30
"""ADR-021 §3: fewer shadow round trips than this flag the execution check as weak."""

_Gate = Callable[[CertificationRecord, GateEvidence, datetime], GateReport]
"""Evaluates the next gate from the guarded record, the evidence and the time."""


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
    parsed, code = _parse(argv, stream, err)
    if parsed is None:
        return code
    if parsed.batch is not None:
        return _certify_batch(parsed, stream, err)
    return _certify(parsed, stream, err, stdin or sys.stdin)


def _certify(parsed: argparse.Namespace, stream: TextIO, err: TextIO, stdin: TextIO) -> int:
    """Evaluate v1's or v2's next gate, as signed in its pre-registration."""
    if parsed.sign_off is not None and not stdin.isatty():
        err.write(
            f"ERROR: --sign-off needs an interactive terminal to type {LIVE_APPROVAL_PHRASE}; "
            "stdin is not one, so nothing was approved.\n"
        )
        return 2
    loaded = _load(parsed, err)
    if isinstance(loaded, int):
        return loaded
    fingerprint, evidence, pre_reg = loaded
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
        round_trips=partial(_round_trips, parsed.db, parsed.strategy_id),
        gate=gate,
    )


def _load(
    parsed: argparse.Namespace, err: TextIO
) -> tuple[str, GateEvidence, PreRegistration] | int:
    """Return the fingerprint, the evidence and the pre-registration, or exit code 3."""
    try:
        config = load_config(parsed.config_dir)
        fingerprint = _fingerprint_for(config, parsed.strategy_id)
        evidence = (
            load_evidence(parsed.evidence, files=_FILES) if parsed.evidence else GateEvidence()
        )
        if parsed.performance:
            evidence = apply_performance(evidence, parsed.performance, files=_FILES)
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
    if parsed.shadow_parity:
        try:
            evidence = apply_shadow_parity(
                evidence,
                parsed.shadow_parity,
                parsed.strategy_id,
                day_zero=_day_zero(parsed.db, parsed.strategy_id),
                min_days=pre_reg.certification_schedule().shadow_min_days,
            )
        except (OSError, EvidenceError, ShadowRecordError) as exc:
            err.write(f"ERROR: evidence: {exc}\n")
            return 3
    return fingerprint, evidence, pre_reg


def _round_trips(db: Path, strategy_id: str) -> int | None:
    """Return the shadow round trips ``db`` holds, or ``None`` without a ledger there.

    Raises:
        LedgerError: The ledger cannot be read as one.
        OSError: The file cannot be opened.
    """
    if not db.is_file():
        return None
    with SqliteStatusReader.open(db) as reader:
        return reader.round_trips(StrategyId(strategy_id))


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
    funnel = validate_evidence(
        load_funnel_evidence(path, strategy_id=V2_STRATEGY_ID, files=_FILES), thresholds
    )
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
    add_config_dir_arg(parser)
    add_db_arg(parser)
    parser.add_argument("--sign-off", default=None)
    parser.add_argument("--funnel-evidence", type=Path)
    parser.add_argument("--shadow-parity", type=Path)
    parser.add_argument("--batch", type=Path)
    parser.add_argument("--trial-log", type=Path)
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
    return _validate(parsed, err)


def _validate(parsed: argparse.Namespace, err: TextIO) -> tuple[argparse.Namespace | None, int]:
    """Refuse flags that do not fit the strategy or each other."""
    if parsed.batch is not None:
        return _parse_batch_flags(parsed, err)
    if parsed.trial_log is not None:
        err.write("ERROR: --trial-log applies to --batch only.\n" + _USAGE)
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


def _parse_batch_flags(
    parsed: argparse.Namespace, err: TextIO
) -> tuple[argparse.Namespace | None, int]:
    missing = [
        flag
        for flag, value in (
            ("--evidence", parsed.evidence),
            ("--performance", parsed.performance),
            ("--funnel-evidence", parsed.funnel_evidence),
        )
        if value is None
    ]
    if missing:
        err.write(f"ERROR: --batch needs {', '.join(missing)}.\n" + _USAGE)
        return None, 2
    if parsed.sign_off is not None or parsed.shadow_parity is not None:
        err.write("ERROR: --sign-off and --shadow-parity do not apply to --batch.\n" + _USAGE)
        return None, 2
    return parsed, 0


_BatchInputs = tuple[ResearchBatch, Candidate, GateEvidence, FunnelEvidence]


def _batch_inputs(parsed: argparse.Namespace, stream: TextIO, err: TextIO) -> _BatchInputs | int:
    """The signed batch, its candidate and the candidate's evidence, or an exit code."""
    strategy_id: str = parsed.strategy_id
    try:
        batch = load_batch(parsed.batch, files=_FILES)
    except BatchError as exc:
        err.write(f"ERROR: batch: {exc}\n")
        return 3
    candidate = batch.candidate(strategy_id)
    if batch.status != "SIGNED" or candidate is None:
        reason = (
            f"batch {batch.batch_id} is {batch.status}; only a SIGNED batch is judged"
            if candidate is not None
            else f"batch {batch.batch_id} does not name {strategy_id!r}"
        )
        stream.write(f"# hermes certify {strategy_id}: batch {batch.batch_id}\nREFUSED: {reason}\n")
        return 1
    trial_log = JsonlTrialLog(parsed.trial_log or Path(batch.trial_log))
    paths = CandidateEvidence(parsed.performance, parsed.evidence, parsed.funnel_evidence)
    try:
        require_v1_secondary(batch.section_text(candidate))
        trials = summarise_trials(trial_log.records())
        evidence, funnel = candidate_gate_evidence(strategy_id, paths, batch, trials, files=_FILES)
    except (OSError, ValueError, TrialLogError) as exc:
        err.write(f"ERROR: {strategy_id} in batch {batch.batch_id}: {exc}\n")
        return 3
    return batch, candidate, evidence, funnel


def _certify_batch(parsed: argparse.Namespace, stream: TextIO, err: TextIO) -> int:
    """Judge one batch candidate; record only a Train + Validation verdict."""
    inputs = _batch_inputs(parsed, stream, err)
    if isinstance(inputs, int):
        return inputs
    batch, candidate, evidence, funnel = inputs
    try:
        pre_reg = serve_module.pre_registration_for(parsed.config_dir)
    except (OSError, ValueError) as exc:
        err.write(f"ERROR: pre-registration: {exc}\n")
        return 3

    def gate(record: CertificationRecord, submitted: GateEvidence, now: datetime) -> GateReport:
        return evaluate_signed_gate(
            record,
            submitted,
            now=now,
            v1=pre_reg,
            dsr_min=batch.funnel.dsr_min,
            thresholds=batch.funnel.thresholds,
            funnel=funnel,
            refusal=(
                f"the gate out of {record.state} is not implemented for batch candidates; "
                "ADR-026 signs the backtest gate only."
            ),
        )

    now = now_utc()
    if evidence_split(funnel, batch.funnel.thresholds) == "train":
        report = gate(CertificationRecord(candidate.strategy_id), evidence, now)
        return _print_train_verdict(report, batch, stream)
    try:
        fingerprint = _batch_fingerprint(load_config(parsed.config_dir), batch, candidate)
    except (ConfigError, serve_module.StartupError) as exc:
        err.write(f"ERROR: config: {exc}\n")
        return 3
    store_dir = parsed.store_dir or serve_module.certification_dir_for(serve_module.DEFAULT_DB_PATH)
    return run(
        candidate.strategy_id,
        evidence,
        store=JsonCertificationStore(store_dir),
        fingerprint=fingerprint,
        stream=stream,
        err=err,
        clock=lambda: now,
        pre_reg=pre_reg,
        gate=gate,
    )


def _print_train_verdict(report: GateReport, batch: ResearchBatch, stream: TextIO) -> int:
    """Print a Train-only verdict; the gate out of UNTRUSTED needs Validation, so record nothing."""
    checks = [check_result(row, "train") for row in report.checks]
    stream.write(
        f"# hermes certify {report.strategy_id}: batch {batch.batch_id}, judged on Train only\n"
    )
    stream.writelines(f"{check.status:<4}  {check.check:<24} {check.detail}\n" for check in checks)
    if any(check.status == "FAIL" for check in checks):
        stream.write("GATE FAILED\n")
        return 1
    stream.write(
        "TRAIN PASSED: nothing recorded; the backtest gate is judged on Train + Validation\n"
    )
    return 0


def _batch_fingerprint(config: HermesConfig, batch: ResearchBatch, candidate: Candidate) -> str:
    """The candidate's roster entry and the base values its batch signed."""
    roster = next(
        (s for s in config.strategies.strategies if s.name == candidate.strategy_id), None
    )
    if roster is None:
        raise serve_module.StartupError(f"strategies.yaml has no {candidate.strategy_id!r} entry")
    parameters = {
        f"{candidate.strategy_id}.{parameter.name}": str(parameter.base)
        for parameter in candidate.parameters
    }
    parameters["batch.id"] = batch.batch_id
    parameters["capital_budget"] = str(roster.capital_budget.normalize())
    parameters["universe"] = ",".join(sorted(config.market_data.pairs))
    return parameter_fingerprint(parameters)


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
    round_trips: Callable[[], int | None] = lambda: None,
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
        round_trips: Counts the shadow round trips in the ledger (``None``
            without one). Called only toward SHADOW_CERTIFIED, so no other
            gate reads the ledger.
        gate: Evaluates the next gate; defaults to v1's gates read from ``pre_reg``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    now = clock()
    rules = pre_reg or load_pre_registration()
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
    if report.to_state == CertificationState.SHADOW_CERTIFIED:
        _print_round_trips(round_trips, stream)
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


def _print_round_trips(count: Callable[[], int | None], stream: TextIO) -> None:
    """Report the shadow trade count; flag it weak under 30 (ADR-021 §3, decision 10).

    The count is informational, so a ledger it cannot read is a note, never a failure.
    """
    try:
        round_trips = count()
    except (LedgerError, OSError) as exc:
        stream.write(f"NOTE  shadow round trips: ledger unreadable, not counted ({exc})\n")
        return
    if round_trips is None:
        stream.write("NOTE  shadow round trips: no ledger found, not counted\n")
    elif round_trips < WEAK_ROUND_TRIPS:
        stream.write(
            f"WEAK  shadow round trips: {round_trips}, under {WEAK_ROUND_TRIPS}; the execution "
            "check is weak (ADR-021 §3). No pass or fail rule changes.\n"
        )
    else:
        stream.write(f"NOTE  shadow round trips: {round_trips}\n")


_PARITY_KEYS = frozenset({"strategy_id", "since", "until", "m09", "m10"})


def apply_shadow_parity(
    evidence: GateEvidence,
    path: Path,
    strategy_id: str,
    *,
    day_zero: datetime,
    min_days: int,
) -> GateEvidence:
    """Add the M-09 and M-10 statuses of a ``hermes parity window`` file to ``evidence``.

    The file must judge the counted run itself: from its day 0, over at least
    the shadow length, so a favourable sub-window or an earlier run's file is
    refused (``--since`` is free on ``hermes parity window``).

    Args:
        evidence: Evidence loaded so far.
        path: The file ``hermes parity window --out`` wrote.
        strategy_id: The strategy being certified; the file must name it.
        day_zero: Day 0 of the strategy's latest counted run; the file's
            ``since`` must be exactly this.
        min_days: Shadow days the gate requires; the file's window must be at
            least this long.

    Returns:
        ``evidence`` with the shadow statuses set; its hash then covers the file too.

    Raises:
        EvidenceError: The file is not a parity window file of ``strategy_id``,
            or does not cover the counted run from its day 0 for ``min_days``.
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
    since, until = (_time(path, payload, key) for key in ("since", "until"))
    if since != day_zero:
        raise EvidenceError(
            f"{path} starts at {since.isoformat()}, not at day 0 ({day_zero.isoformat()})"
        )
    if until - since < timedelta(days=min_days):
        raise EvidenceError(f"{path} covers {(until - since).days} days, not {min_days}")
    m09, m10 = (_status(path, payload, key) for key in ("m09", "m10"))
    digest = hashlib.sha256(
        ((evidence.sha256 or "") + hashlib.sha256(raw).hexdigest()).encode("ascii")
    ).hexdigest()
    shadow = replace(evidence.shadow or ShadowEvidence(), m09=m09, m10=m10)
    return replace(evidence, shadow=shadow, sha256=digest)


def _time(path: Path, payload: dict[str, Any], key: str) -> datetime:
    raw = payload[key]
    try:
        at = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        at = None
    if at is None or at.tzinfo is None:
        raise EvidenceError(f"{path}: {key} must be an ISO time with a UTC offset")
    return at


def _day_zero(db: Path, strategy_id: str) -> datetime:
    """Return day 0 of the strategy's latest counted run, recorded beside the ledger."""
    record = RunsDirectory(runs_dir_for(db)).latest_run(strategy_id)
    if record is None:
        raise EvidenceError(f"no day-0 record for {strategy_id} in {runs_dir_for(db)}")
    return record.day_zero_at


def _status(path: Path, payload: dict[str, Any], key: str) -> MetricStatus:
    try:
        return MetricStatus(payload[key])
    except ValueError as exc:
        raise EvidenceError(f"{path}: {key} must be pass, block or insufficient") from exc
