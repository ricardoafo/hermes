"""Certification gates — the pre-registration §7 entry criteria, enforced in code.

Each forward transition of the certification lifecycle has one gate. A gate is a
pure function of the stored :class:`CertificationRecord`, the submitted
:class:`GateEvidence` and the clock; it returns a :class:`GateReport` with one
:class:`GateCheck` per rule so the operator sees every pass and fail, not just
the first. Every threshold comes from
:class:`~hermes.application.strategy_protocol.preregistration.PreRegistration`.

Gates:

- ``UNTRUSTED → BACKTEST_CERTIFIED``: the §9 rows on the Train+Val metrics
  except the OOS-only trade count and bootstrap CI, plus at least one
  calibration fill.
- ``BACKTEST_CERTIFIED → SHADOW_CERTIFIED``: 90+ shadow days, sim-vs-live drift
  p50 below 30 bps, no §10 shadow kill fired.
- ``SHADOW_CERTIFIED → OOS_CERTIFIED``: one-shot H1 test (§1, §9) on the OOS
  window, not before 2027-01-01 (§4). Refused, consuming nothing, when evidence
  is incomplete or the shot was already taken.
- ``OOS_CERTIFIED → LIVE_APPROVED``: explicit operator sign-off.
- ``LIVE_APPROVED → CERTIFIED``: 90+ live days with no §10 live kill fired.

Missing statistics fail closed: a metric that has not been computed fails its
check (#354 computes them). :func:`apply_guardrails` implements the reverts:
any parameter change returns the strategy to UNTRUSTED (the D-8 forward-testing
guardrail), a shadow drift breach does the same (§7), and a live kill is DEAD.

Reference: pre-registration-v1.md §1, §4, §7, §9, §10; ADR-014;
openspec/changes/355-certification-gates/change.md.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from decimal import Decimal

from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.strategy_protocol.preregistration import H1Thresholds, PreRegistration
from hermes.domain.strategy.swing.adapter import swing_parameters

__all__ = [
    "LIVE_APPROVAL_PHRASE",
    "CertificationRecord",
    "GateCheck",
    "GateEvidence",
    "GateReport",
    "LiveEvidence",
    "OperatorSignOff",
    "ShadowEvidence",
    "Transition",
    "WindowMetrics",
    "apply_guardrails",
    "evaluate_next_gate",
    "parameter_fingerprint",
    "record_outcome",
    "require_live_approval",
    "section9_checks",
    "v1_parameter_fingerprint",
]

LIVE_APPROVAL_PHRASE = "LIVE_APPROVED"
"""The exact text the operator types to confirm live approval."""

_NOT_COMPUTED = "not computed (#354)"
_LIVE_STATES = frozenset({CertificationState.LIVE_APPROVED, CertificationState.CERTIFIED})


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowMetrics:
    """The §9 metrics for one data window. ``None`` means not computed.

    Fractions are plain ratios (0.12 = 12%).
    """

    dsr: float | None = None
    bootstrap_ci_lower: float | None = None
    trade_count: int | None = None
    max_drawdown: float | None = None
    recovery_days: float | None = None
    profit_factor: float | None = None
    ulcer_index: float | None = None
    fees_to_gross_pnl: float | None = None
    win_rate: float | None = None

    def is_complete(self) -> bool:
        """Return True when every §9 metric has a value."""
        return all(getattr(self, f.name) is not None for f in fields(self))


@dataclass(frozen=True, slots=True)
class ShadowEvidence:
    """Live-shadow measurements for the shadow gate and the drift revert."""

    days: int | None = None
    drift_p50_bps: float | None = None
    kill_fired: bool | None = None
    drift_breached: bool | None = None


@dataclass(frozen=True, slots=True)
class LiveEvidence:
    """Live-capital measurements for the final gate and the live kill."""

    days: int | None = None
    kill_fired: bool | None = None


@dataclass(frozen=True, slots=True)
class OperatorSignOff:
    """The operator's explicit live approval (pre-reg §7, ADR-014 decision 2)."""

    name: str
    confirmation: str
    signed_at: datetime


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """Everything a gate may read. Absent sections fail the gates that need them."""

    backtest: WindowMetrics | None = None
    calibration_fill_count: int | None = None
    shadow: ShadowEvidence | None = None
    oos: WindowMetrics | None = None
    live: LiveEvidence | None = None
    sign_off: OperatorSignOff | None = None
    sha256: str | None = None


# ---------------------------------------------------------------------------
# Reports and the record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateCheck:
    """One rule of a gate with its evidence."""

    name: str
    requirement: str
    observed: str
    passed: bool


@dataclass(frozen=True, slots=True)
class GateReport:
    """Outcome of evaluating the next gate for one strategy.

    ``refused_reason`` is non-empty when the gate was not evaluated at all
    (wrong state, too early, shot already taken, incomplete OOS evidence).
    ``consumes_oos`` is True only when the one-shot OOS test was evaluated.
    """

    strategy_id: str
    from_state: CertificationState
    to_state: CertificationState | None
    checks: tuple[GateCheck, ...] = ()
    refused_reason: str = ""
    consumes_oos: bool = False

    @property
    def passed(self) -> bool:
        """True iff the gate was evaluated and every check passed."""
        return (
            not self.refused_reason
            and bool(self.checks)
            and all(check.passed for check in self.checks)
        )

    @property
    def failed_checks(self) -> tuple[str, ...]:
        """Names of the checks that failed, in evaluation order."""
        return tuple(check.name for check in self.checks if not check.passed)


@dataclass(frozen=True, slots=True)
class Transition:
    """One recorded state change."""

    from_state: CertificationState
    to_state: CertificationState
    at: datetime
    reason: str
    evidence_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class CertificationRecord:
    """Persisted certification state for one strategy.

    ``parameter_fingerprint`` is the fingerprint the last passed gate saw.
    ``oos_evaluated_at`` is set once the one-shot OOS test has run and is never
    cleared for this pre-registration.
    """

    strategy_id: str
    state: CertificationState = CertificationState.UNTRUSTED
    parameter_fingerprint: str | None = None
    oos_evaluated_at: datetime | None = None
    transitions: tuple[Transition, ...] = field(default=())

    def moved_to(
        self,
        state: CertificationState,
        *,
        at: datetime,
        reason: str,
        fingerprint: str | None,
        evidence_sha256: str | None = None,
    ) -> CertificationRecord:
        """Return a copy in ``state`` with the transition appended."""
        transition = Transition(self.state, state, at, reason, evidence_sha256)
        return replace(
            self,
            state=state,
            parameter_fingerprint=fingerprint,
            transitions=(*self.transitions, transition),
        )


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def parameter_fingerprint(parameters: Mapping[str, str]) -> str:
    """Return the SHA-256 of ``parameters`` as canonical JSON.

    Args:
        parameters: Parameter name to value, all strings.

    Returns:
        Lower-case hex digest. Key order does not matter.
    """
    canonical = json.dumps(dict(parameters), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def v1_parameter_fingerprint(*, capital_budget: Decimal, universe: Sequence[str]) -> str:
    """Fingerprint what v1 trades with: §5 parameters, §2 capital and §3 universe.

    Args:
        capital_budget: The strategy's capital budget from the roster.
        universe: The followed pair symbols.

    Returns:
        The :func:`parameter_fingerprint` of the combined mapping.
    """
    parameters = {f"swing.{name}": value for name, value in swing_parameters().items()}
    parameters["capital_budget"] = str(capital_budget.normalize())
    parameters["universe"] = ",".join(sorted(universe))
    return parameter_fingerprint(parameters)


# ---------------------------------------------------------------------------
# §9 checks
# ---------------------------------------------------------------------------


def _check(name: str, requirement: str, value: float | None, ok: bool) -> GateCheck:
    observed = _NOT_COMPUTED if value is None else f"{value:g}"
    return GateCheck(name, requirement, observed, value is not None and ok)


def section9_checks(metrics: WindowMetrics, thresholds: H1Thresholds) -> tuple[GateCheck, ...]:
    """Evaluate every §9 row (three primary, six secondary) on one window.

    A metric that has not been computed fails its row.

    Args:
        metrics: The window's metrics.
        thresholds: The signed §9 thresholds.

    Returns:
        Nine checks, primary rows first, in the §9 table order.
    """
    t = thresholds
    m = metrics
    return (
        _check("dsr", f"> {t.dsr_min:g}", m.dsr, m.dsr is not None and m.dsr > t.dsr_min),
        _check(
            "bootstrap_ci",
            f"95% CI lower bound > {t.bootstrap_ci_lower_min:g}",
            m.bootstrap_ci_lower,
            m.bootstrap_ci_lower is not None and m.bootstrap_ci_lower > t.bootstrap_ci_lower_min,
        ),
        _check(
            "trade_count",
            f">= {t.oos_trade_count_min}",
            None if m.trade_count is None else float(m.trade_count),
            m.trade_count is not None and m.trade_count >= t.oos_trade_count_min,
        ),
        _check(
            "max_drawdown",
            f"< {t.max_drawdown_max:g}",
            m.max_drawdown,
            m.max_drawdown is not None and m.max_drawdown < t.max_drawdown_max,
        ),
        _check(
            "recovery_days",
            f"< {t.recovery_days_max:g} days from any drawdown >= 8%",
            m.recovery_days,
            m.recovery_days is not None and m.recovery_days < t.recovery_days_max,
        ),
        _check(
            "profit_factor",
            f"> {t.profit_factor_min:g}",
            m.profit_factor,
            m.profit_factor is not None and m.profit_factor > t.profit_factor_min,
        ),
        _check(
            "ulcer_index",
            f"< {t.ulcer_index_max:g}",
            m.ulcer_index,
            m.ulcer_index is not None and m.ulcer_index < t.ulcer_index_max,
        ),
        _check(
            "fees_to_gross_pnl",
            f"< {t.fees_to_gross_pnl_max:g}",
            m.fees_to_gross_pnl,
            m.fees_to_gross_pnl is not None and m.fees_to_gross_pnl < t.fees_to_gross_pnl_max,
        ),
        _check(
            "win_rate",
            f">= {t.win_rate_min:g}",
            m.win_rate,
            m.win_rate is not None and m.win_rate >= t.win_rate_min,
        ),
    )


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def evaluate_next_gate(
    record: CertificationRecord,
    evidence: GateEvidence,
    *,
    now: datetime,
    pre_reg: PreRegistration,
) -> GateReport:
    """Evaluate the gate out of the record's current state.

    Pure: reads nothing but its arguments and changes nothing. Persist the
    outcome with :func:`record_outcome`.

    Args:
        record: The strategy's stored record, after :func:`apply_guardrails`.
        evidence: The submitted evidence.
        now: Current UTC time.
        pre_reg: Source of every threshold.

    Returns:
        The :class:`GateReport` for the next transition.
    """
    state = record.state
    sid = record.strategy_id
    if state == CertificationState.DEAD:
        return GateReport(
            sid,
            state,
            None,
            refused_reason="DEAD is absorbing; a new pre-registration is required (§10).",
        )
    if state == CertificationState.CERTIFIED:
        return GateReport(
            sid, state, None, refused_reason="CERTIFIED is the last state; nothing to evaluate."
        )
    if state == CertificationState.SHADOW_CERTIFIED:
        return _oos_gate(record, evidence, now, pre_reg)
    if state == CertificationState.OOS_CERTIFIED:
        return _live_approval_gate(sid, evidence)
    return _EVIDENCE_GATES[state](sid, evidence, pre_reg)


_OOS_ONLY_ROWS = frozenset({"trade_count", "bootstrap_ci"})
"""§9 rows that apply to the OOS test only (Code Owner ruling, 2026-10-03)."""


def _backtest_gate(sid: str, evidence: GateEvidence, pre_reg: PreRegistration) -> GateReport:
    metrics = evidence.backtest or WindowMetrics()
    fills = evidence.calibration_fill_count
    rows = section9_checks(metrics, pre_reg.h1_thresholds())
    checks = (
        *(row for row in rows if row.name not in _OOS_ONLY_ROWS),
        _check(
            "calibration_fills", ">= 1", None if fills is None else float(fills), (fills or 0) >= 1
        ),
    )
    return GateReport(
        sid, CertificationState.UNTRUSTED, CertificationState.BACKTEST_CERTIFIED, checks
    )


def _shadow_gate(sid: str, evidence: GateEvidence, pre_reg: PreRegistration) -> GateReport:
    schedule = pre_reg.certification_schedule()
    shadow = evidence.shadow or ShadowEvidence()
    days = shadow.days
    drift = shadow.drift_p50_bps
    kill = shadow.kill_fired
    checks = (
        _check(
            "shadow_days",
            f">= {schedule.shadow_min_days}",
            None if days is None else float(days),
            days is not None and days >= schedule.shadow_min_days,
        ),
        _check(
            "shadow_drift_p50_bps",
            f"< {schedule.shadow_drift_p50_max_bps:g}",
            drift,
            drift is not None and drift < schedule.shadow_drift_p50_max_bps,
        ),
        GateCheck(
            "shadow_kill",
            "no §10 shadow kill fired",
            _NOT_COMPUTED if kill is None else ("fired" if kill else "none fired"),
            kill is False,
        ),
    )
    return GateReport(
        sid, CertificationState.BACKTEST_CERTIFIED, CertificationState.SHADOW_CERTIFIED, checks
    )


def _oos_gate(
    record: CertificationRecord, evidence: GateEvidence, now: datetime, pre_reg: PreRegistration
) -> GateReport:
    sid = record.strategy_id
    frm = CertificationState.SHADOW_CERTIFIED
    to = CertificationState.OOS_CERTIFIED
    not_before = pre_reg.certification_schedule().certification_not_before
    if now < not_before:
        return GateReport(
            sid,
            frm,
            to,
            refused_reason=(
                f"OOS certification cannot begin before {not_before.date().isoformat()} (§4)."
            ),
        )
    if record.oos_evaluated_at is not None:
        return GateReport(
            sid,
            frm,
            to,
            refused_reason=(
                "The one-shot OOS test already ran at "
                f"{record.oos_evaluated_at.isoformat()}; a new pre-registration is required (§7)."
            ),
        )
    if evidence.oos is None or not evidence.oos.is_complete():
        return GateReport(
            sid,
            frm,
            to,
            refused_reason=(
                "OOS evidence is incomplete; the one-shot test runs only on a full §9 metric set."
            ),
        )
    checks = section9_checks(evidence.oos, pre_reg.h1_thresholds())
    return GateReport(sid, frm, to, checks, consumes_oos=True)


def _live_approval_gate(sid: str, evidence: GateEvidence) -> GateReport:
    sign_off = evidence.sign_off
    name = sign_off.name.strip() if sign_off is not None else ""
    confirmation = sign_off.confirmation if sign_off is not None else ""
    checks = (
        GateCheck("operator_sign_off", "operator name given", name or "missing", bool(name)),
        GateCheck(
            "operator_confirmation",
            f"typed exactly {LIVE_APPROVAL_PHRASE!r}",
            repr(confirmation) if confirmation else "missing",
            confirmation == LIVE_APPROVAL_PHRASE,
        ),
    )
    return GateReport(
        sid, CertificationState.OOS_CERTIFIED, CertificationState.LIVE_APPROVED, checks
    )


def _live_gate(sid: str, evidence: GateEvidence, pre_reg: PreRegistration) -> GateReport:
    schedule = pre_reg.certification_schedule()
    live = evidence.live or LiveEvidence()
    days = live.days
    kill = live.kill_fired
    checks = (
        _check(
            "live_days",
            f">= {schedule.live_min_days}",
            None if days is None else float(days),
            days is not None and days >= schedule.live_min_days,
        ),
        GateCheck(
            "live_kill",
            "no §10 live kill fired",
            _NOT_COMPUTED if kill is None else ("fired" if kill else "none fired"),
            kill is False,
        ),
    )
    return GateReport(sid, CertificationState.LIVE_APPROVED, CertificationState.CERTIFIED, checks)


_EVIDENCE_GATES = {
    CertificationState.UNTRUSTED: _backtest_gate,
    CertificationState.BACKTEST_CERTIFIED: _shadow_gate,
    CertificationState.LIVE_APPROVED: _live_gate,
}


# ---------------------------------------------------------------------------
# Applying outcomes
# ---------------------------------------------------------------------------


def apply_guardrails(
    record: CertificationRecord,
    *,
    fingerprint: str,
    evidence: GateEvidence,
    now: datetime,
) -> CertificationRecord:
    """Apply the reverts that fire regardless of which gate is next.

    - Parameters changed since the last passed gate: back to UNTRUSTED (D-8).
    - Live kill fired while LIVE_APPROVED or CERTIFIED: DEAD (§10 live phase).
    - Shadow drift breach while SHADOW_CERTIFIED: back to UNTRUSTED (§7).

    Args:
        record: The stored record.
        fingerprint: Fingerprint of the parameters running now.
        evidence: The submitted evidence.
        now: Current UTC time.

    Returns:
        The record, unchanged or moved.
    """
    state = record.state
    if state in (CertificationState.DEAD, CertificationState.UNTRUSTED):
        return record
    if record.parameter_fingerprint != fingerprint:
        return record.moved_to(
            CertificationState.UNTRUSTED,
            at=now,
            reason="parameters changed since certification (D-8 guardrail, §11)",
            fingerprint=None,
            evidence_sha256=evidence.sha256,
        )
    if state in _LIVE_STATES and evidence.live is not None and evidence.live.kill_fired:
        return record.moved_to(
            CertificationState.DEAD,
            at=now,
            reason="§10 live kill trigger fired",
            fingerprint=record.parameter_fingerprint,
            evidence_sha256=evidence.sha256,
        )
    if (
        state == CertificationState.SHADOW_CERTIFIED
        and evidence.shadow is not None
        and evidence.shadow.drift_breached
    ):
        return record.moved_to(
            CertificationState.UNTRUSTED,
            at=now,
            reason="sim-vs-live drift breach (§7)",
            fingerprint=None,
            evidence_sha256=evidence.sha256,
        )
    return record


def record_outcome(
    record: CertificationRecord,
    report: GateReport,
    *,
    fingerprint: str,
    now: datetime,
    evidence_sha256: str | None = None,
) -> CertificationRecord:
    """Return the record after ``report``. Refusals and failures change nothing,
    except a failed one-shot OOS test, which is permanent.

    Args:
        record: The record the report was evaluated against.
        report: Output of :func:`evaluate_next_gate`.
        fingerprint: Fingerprint of the parameters the evidence was produced with.
        now: Current UTC time.
        evidence_sha256: Hash of the evidence document, kept for audit.

    Returns:
        The updated record.

    Raises:
        ValueError: When ``report`` was not evaluated against ``record``'s state.
    """
    if report.from_state != record.state or report.strategy_id != record.strategy_id:
        raise ValueError(
            f"gate report is for {report.strategy_id} in {report.from_state}, "
            f"record is {record.strategy_id} in {record.state}"
        )
    if report.refused_reason or report.to_state is None:
        return record
    if report.consumes_oos:
        consumed = replace(record, oos_evaluated_at=now)
        if report.passed:
            return consumed.moved_to(
                CertificationState.OOS_CERTIFIED,
                at=now,
                reason="H1 accepted on OOS (§1, §9)",
                fingerprint=fingerprint,
                evidence_sha256=evidence_sha256,
            )
        return consumed.moved_to(
            CertificationState.UNTRUSTED,
            at=now,
            reason="H0 not rejected on OOS (§1): " + ", ".join(report.failed_checks),
            fingerprint=None,
            evidence_sha256=evidence_sha256,
        )
    if not report.passed:
        return record
    return record.moved_to(
        report.to_state,
        at=now,
        reason="gate passed",
        fingerprint=fingerprint,
        evidence_sha256=evidence_sha256,
    )


def require_live_approval(record: CertificationRecord | None, fingerprint: str) -> str:
    """Return why real-capital trading is refused, or ``""`` when it is allowed.

    Args:
        record: The strategy's stored record, or ``None`` when there is none.
        fingerprint: Fingerprint of the parameters that would trade.

    Returns:
        Empty string iff the record is LIVE_APPROVED or CERTIFIED with an
        unchanged fingerprint.
    """
    if record is None:
        return "no certification record"
    if record.state not in _LIVE_STATES:
        return f"certification state is {record.state}, not LIVE_APPROVED"
    if record.parameter_fingerprint != fingerprint:
        return "parameters changed since live approval (D-8 guardrail)"
    return ""
