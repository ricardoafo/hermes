"""Unit tests for the certification gates (pre-registration §7, #355).

Each test names the pre-registration rule it protects. Gates are pure, so the
tests drive them with hand-built records and evidence and a fixed clock.
"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.calibration.certification_gates import (
    LIVE_APPROVAL_PHRASE,
    GateEvidence,
    LiveEvidence,
    OperatorSignOff,
    ShadowEvidence,
    WindowMetrics,
    apply_guardrails,
    evaluate_next_gate,
    parameter_fingerprint,
    record_outcome,
    require_live_approval,
    section9_checks,
    v1_parameter_fingerprint,
)
from hermes.application.calibration.shadow_parity import MetricStatus
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.calibration.certification import CertificationRecord, CertificationState
from hermes.infrastructure.project_root import load_pre_registration

_S = CertificationState
_SID = "swing-donchian-4h"
_FP = "f" * 64
_BEFORE_OOS = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
_OOS_DAY = datetime(2027, 1, 1, tzinfo=UTC)

_PASSING = WindowMetrics(
    dsr=0.8,
    bootstrap_ci_lower=0.1,
    trade_count=40,
    max_drawdown=0.10,
    recovery_days=30.0,
    profit_factor=1.5,
    ulcer_index=6.0,
    fees_to_gross_pnl=0.2,
    win_rate=0.4,
)
_SHADOW = ShadowEvidence(
    days=90,
    drift_p50_bps=10.0,
    kill_fired=False,
    drift_breached=False,
    m09=MetricStatus.OK,
    m10=MetricStatus.OK,
)
_ALL_PASS = GateEvidence(
    backtest=_PASSING,
    calibration_fill_count=40,
    shadow=_SHADOW,
    oos=_PASSING,
    live=LiveEvidence(days=90, kill_fired=False),
    sign_off=OperatorSignOff("Ricardo", LIVE_APPROVAL_PHRASE, _OOS_DAY),
    sha256="e" * 64,
)


@pytest.fixture(scope="module")
def pre_reg() -> PreRegistration:
    """Return the real signed pre-registration."""
    here = Path(__file__).resolve().parent
    root = next(p for p in [here, *here.parents] if (p / "pyproject.toml").exists())
    return load_pre_registration(root)


def _record(
    state: CertificationState,
    *,
    fingerprint: str = _FP,
    oos_evaluated_at: datetime | None = None,
) -> CertificationRecord:
    return CertificationRecord(
        _SID, state, parameter_fingerprint=fingerprint, oos_evaluated_at=oos_evaluated_at
    )


# ---------------------------------------------------------------------------
# §9 rows
# ---------------------------------------------------------------------------


class TestSection9Checks:
    """The shared §9 evaluator applies every row with its signed comparison."""

    def test_passing_window_passes_all_nine_rows(self, pre_reg: PreRegistration) -> None:
        """A window inside every §9 bound passes all nine rows."""
        checks = section9_checks(_PASSING, pre_reg.h1_thresholds())
        assert [c.name for c in checks] == [
            "dsr",
            "bootstrap_ci",
            "trade_count",
            "max_drawdown",
            "recovery_days",
            "profit_factor",
            "ulcer_index",
            "fees_to_gross_pnl",
            "win_rate",
        ]
        assert all(c.passed for c in checks)

    # SEV-1: missing statistics must never pass a real-capital gate (#354 fail-closed)
    def test_missing_metric_fails_closed(self, pre_reg: PreRegistration) -> None:
        """An uncomputed metric fails its row and says why (D-3)."""
        checks = section9_checks(replace(_PASSING, dsr=None), pre_reg.h1_thresholds())
        dsr = checks[0]
        assert (dsr.passed, dsr.observed) == (False, "not computed (#354)")

    @pytest.mark.parametrize(
        ("metrics", "name"),
        [
            (replace(_PASSING, dsr=0.7), "dsr"),
            (replace(_PASSING, bootstrap_ci_lower=0.0), "bootstrap_ci"),
            (replace(_PASSING, trade_count=29), "trade_count"),
            (replace(_PASSING, max_drawdown=0.15), "max_drawdown"),
            (replace(_PASSING, recovery_days=90.0), "recovery_days"),
            (replace(_PASSING, profit_factor=1.3), "profit_factor"),
            (replace(_PASSING, ulcer_index=10.0), "ulcer_index"),
            (replace(_PASSING, fees_to_gross_pnl=0.30), "fees_to_gross_pnl"),
            (replace(_PASSING, win_rate=0.2999), "win_rate"),
        ],
    )
    def test_each_row_fails_at_its_boundary(
        self, pre_reg: PreRegistration, metrics: WindowMetrics, name: str
    ) -> None:
        """Each §9 bound is strict where the table says < or >, so the boundary fails."""
        checks = section9_checks(metrics, pre_reg.h1_thresholds())
        assert [c.name for c in checks if not c.passed] == [name]

    def test_win_rate_boundary_passes(self, pre_reg: PreRegistration) -> None:
        """Win rate is the one ≥ row in §9: exactly 30% passes."""
        checks = section9_checks(replace(_PASSING, win_rate=0.30), pre_reg.h1_thresholds())
        assert all(c.passed for c in checks)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


class TestBacktestGate:
    """UNTRUSTED → BACKTEST_CERTIFIED: §9 on Train+Val, minus OOS-only rows, plus a fill (§7)."""

    def test_passes_on_full_evidence(self, pre_reg: PreRegistration) -> None:
        """All §9 rows and one fill certify the backtest."""
        report = evaluate_next_gate(_record(_S.UNTRUSTED), _ALL_PASS, now=_OOS_DAY, pre_reg=pre_reg)
        assert (report.to_state, report.passed) == (_S.BACKTEST_CERTIFIED, True)

    # SEV-1: no evidence must never certify (the bug #355 fixes: one fill used to suffice)
    def test_no_evidence_fails_every_row(self, pre_reg: PreRegistration) -> None:
        """Without metrics, the seven Train+Val §9 rows and the fill check fail."""
        report = evaluate_next_gate(
            _record(_S.UNTRUSTED), GateEvidence(), now=_OOS_DAY, pre_reg=pre_reg
        )
        assert not report.passed
        assert len(report.failed_checks) == 8

    def test_trade_count_and_ci_are_oos_only(self, pre_reg: PreRegistration) -> None:
        """Ruling 2026-10-03: few trades and a CI below zero do not block the backtest."""
        evidence = replace(
            _ALL_PASS, backtest=replace(_PASSING, trade_count=5, bootstrap_ci_lower=-0.1)
        )
        report = evaluate_next_gate(_record(_S.UNTRUSTED), evidence, now=_OOS_DAY, pre_reg=pre_reg)
        names = [c.name for c in report.checks]
        assert (report.passed, "trade_count" in names, "bootstrap_ci" in names) == (
            True,
            False,
            False,
        )

    def test_zero_fills_fails(self, pre_reg: PreRegistration) -> None:
        """Metrics without a calibration fill still fail."""
        evidence = replace(_ALL_PASS, calibration_fill_count=0)
        report = evaluate_next_gate(_record(_S.UNTRUSTED), evidence, now=_OOS_DAY, pre_reg=pre_reg)
        assert report.failed_checks == ("calibration_fills",)


class TestShadowGate:
    """BACKTEST_CERTIFIED → SHADOW_CERTIFIED (§7, §10; ADR-021 Done rule 3).

    90 days, drift p50 < 30 bps, no kill, and M-09 and M-10 passed (#965).
    """

    @pytest.mark.parametrize(
        ("shadow", "failed"),
        [
            (replace(_SHADOW, days=89), "shadow_days"),
            (replace(_SHADOW, drift_p50_bps=30.0), "shadow_drift_p50_bps"),
            (replace(_SHADOW, kill_fired=True), "shadow_kill"),
            (replace(_SHADOW, kill_fired=None), "shadow_kill"),
            (replace(_SHADOW, m09=None), "shadow_m09"),
            (replace(_SHADOW, m09=MetricStatus.BLOCK), "shadow_m09"),
            (replace(_SHADOW, m09=MetricStatus.INSUFFICIENT), "shadow_m09"),
            (replace(_SHADOW, m10=None), "shadow_m10"),
            (replace(_SHADOW, m10=MetricStatus.BLOCK), "shadow_m10"),
            (replace(_SHADOW, m10=MetricStatus.INSUFFICIENT), "shadow_m10"),
        ],
    )
    def test_each_rule_blocks(
        self, pre_reg: PreRegistration, shadow: ShadowEvidence, failed: str
    ) -> None:
        """Each rule blocks alone, M-09 and M-10 on anything but pass (#965).

        89 days, 30 bps, a fired or unknown kill, and an M-09 or M-10 that is
        missing, block or insufficient.
        """
        evidence = replace(_ALL_PASS, shadow=shadow)
        report = evaluate_next_gate(
            _record(_S.BACKTEST_CERTIFIED), evidence, now=_OOS_DAY, pre_reg=pre_reg
        )
        assert report.failed_checks == (failed,)

    def test_passes_at_ninety_days(self, pre_reg: PreRegistration) -> None:
        """Exactly 90 days with low drift and no kill certifies the shadow."""
        report = evaluate_next_gate(
            _record(_S.BACKTEST_CERTIFIED), _ALL_PASS, now=_OOS_DAY, pre_reg=pre_reg
        )
        assert (report.to_state, report.passed) == (_S.SHADOW_CERTIFIED, True)


class TestOosGate:
    """SHADOW_CERTIFIED → OOS_CERTIFIED: one shot, not before 2027-01-01 (§4, §7)."""

    # SEV-1: the OOS window must not be tested before all its data exists (§4)
    def test_refused_before_2027(self, pre_reg: PreRegistration) -> None:
        """One second before 2027-01-01 the gate refuses and consumes nothing."""
        record = _record(_S.SHADOW_CERTIFIED)
        report = evaluate_next_gate(record, _ALL_PASS, now=_BEFORE_OOS, pre_reg=pre_reg)
        assert "2027-01-01" in report.refused_reason
        assert record_outcome(record, report, fingerprint=_FP, now=_BEFORE_OOS) is record

    # SEV-1: the OOS test runs exactly once per pre-registration (§7)
    def test_refused_after_a_previous_run(self, pre_reg: PreRegistration) -> None:
        """A recorded OOS evaluation blocks any second run."""
        record = _record(_S.SHADOW_CERTIFIED, oos_evaluated_at=_OOS_DAY)
        report = evaluate_next_gate(record, _ALL_PASS, now=_OOS_DAY, pre_reg=pre_reg)
        assert "already ran" in report.refused_reason
        assert not report.consumes_oos

    # SEV-1: incomplete evidence must not burn the only shot
    def test_incomplete_evidence_is_refused_without_consuming(
        self, pre_reg: PreRegistration
    ) -> None:
        """A missing OOS metric refuses the gate and leaves the record untouched."""
        record = _record(_S.SHADOW_CERTIFIED)
        evidence = replace(_ALL_PASS, oos=replace(_PASSING, win_rate=None))
        report = evaluate_next_gate(record, evidence, now=_OOS_DAY, pre_reg=pre_reg)
        assert report.refused_reason
        assert record_outcome(record, report, fingerprint=_FP, now=_OOS_DAY) is record

    def test_cannot_be_reached_before_shadow_certified(self, pre_reg: PreRegistration) -> None:
        """From BACKTEST_CERTIFIED the next gate is the shadow gate, never OOS."""
        report = evaluate_next_gate(
            _record(_S.BACKTEST_CERTIFIED), _ALL_PASS, now=_OOS_DAY, pre_reg=pre_reg
        )
        assert report.to_state == _S.SHADOW_CERTIFIED
        assert not report.consumes_oos

    def test_pass_records_oos_certified_and_spends_the_shot(self, pre_reg: PreRegistration) -> None:
        """H1 accepted moves to OOS_CERTIFIED and records the evaluation time."""
        record = _record(_S.SHADOW_CERTIFIED)
        report = evaluate_next_gate(record, _ALL_PASS, now=_OOS_DAY, pre_reg=pre_reg)
        after = record_outcome(record, report, fingerprint=_FP, now=_OOS_DAY)
        assert (after.state, after.oos_evaluated_at) == (_S.OOS_CERTIFIED, _OOS_DAY)

    # SEV-1: a failed OOS test is permanent; H0 not rejected means no live capital (§1)
    def test_fail_returns_to_untrusted_and_spends_the_shot(self, pre_reg: PreRegistration) -> None:
        """H0 not rejected moves to UNTRUSTED and keeps the evaluation time."""
        record = _record(_S.SHADOW_CERTIFIED)
        evidence = replace(_ALL_PASS, oos=replace(_PASSING, dsr=0.5))
        report = evaluate_next_gate(record, evidence, now=_OOS_DAY, pre_reg=pre_reg)
        after = record_outcome(record, report, fingerprint=_FP, now=_OOS_DAY)
        assert (after.state, after.oos_evaluated_at) == (_S.UNTRUSTED, _OOS_DAY)
        assert "dsr" in after.transitions[-1].reason


class TestLiveApprovalGate:
    """OOS_CERTIFIED → LIVE_APPROVED needs an explicit operator sign-off (§7, ADR-014)."""

    # SEV-1: real capital needs the operator's explicit sign-off
    @pytest.mark.parametrize(
        "sign_off",
        [
            None,
            OperatorSignOff("", LIVE_APPROVAL_PHRASE, _OOS_DAY),
            OperatorSignOff("Ricardo", "yes", _OOS_DAY),
            OperatorSignOff("Ricardo", "live_approved", _OOS_DAY),
        ],
    )
    def test_without_exact_sign_off_fails(
        self, pre_reg: PreRegistration, sign_off: OperatorSignOff | None
    ) -> None:
        """No sign-off, no name, or anything but the exact phrase fails."""
        evidence = replace(_ALL_PASS, sign_off=sign_off)
        report = evaluate_next_gate(
            _record(_S.OOS_CERTIFIED), evidence, now=_OOS_DAY, pre_reg=pre_reg
        )
        assert not report.passed

    def test_exact_sign_off_passes(self, pre_reg: PreRegistration) -> None:
        """A named operator typing the exact phrase approves live trading."""
        report = evaluate_next_gate(
            _record(_S.OOS_CERTIFIED), _ALL_PASS, now=_OOS_DAY, pre_reg=pre_reg
        )
        assert (report.to_state, report.passed) == (_S.LIVE_APPROVED, True)


class TestLiveGate:
    """LIVE_APPROVED → CERTIFIED: 90 live days with no kill (§7)."""

    def test_eighty_nine_days_fails(self, pre_reg: PreRegistration) -> None:
        """89 live days is short of the window."""
        evidence = replace(_ALL_PASS, live=LiveEvidence(days=89, kill_fired=False))
        report = evaluate_next_gate(
            _record(_S.LIVE_APPROVED), evidence, now=_OOS_DAY, pre_reg=pre_reg
        )
        assert report.failed_checks == ("live_days",)

    def test_ninety_days_passes(self, pre_reg: PreRegistration) -> None:
        """90 live days with no kill certifies."""
        report = evaluate_next_gate(
            _record(_S.LIVE_APPROVED), _ALL_PASS, now=_OOS_DAY, pre_reg=pre_reg
        )
        assert (report.to_state, report.passed) == (_S.CERTIFIED, True)


class TestTerminalStates:
    """DEAD and CERTIFIED have no gate."""

    @pytest.mark.parametrize("state", [_S.DEAD, _S.CERTIFIED])
    def test_refused(self, pre_reg: PreRegistration, state: CertificationState) -> None:
        """Evaluating from a terminal state is refused."""
        report = evaluate_next_gate(_record(state), _ALL_PASS, now=_OOS_DAY, pre_reg=pre_reg)
        assert (report.to_state, bool(report.refused_reason)) == (None, True)


# ---------------------------------------------------------------------------
# Outcomes and guardrails
# ---------------------------------------------------------------------------


class TestRecordOutcome:
    """Only a passed gate advances; the record keeps an audit trail."""

    def test_pass_advances_and_stores_fingerprint_and_evidence_hash(
        self, pre_reg: PreRegistration
    ) -> None:
        """The new state, fingerprint and evidence hash are recorded."""
        record = CertificationRecord(_SID)
        report = evaluate_next_gate(record, _ALL_PASS, now=_OOS_DAY, pre_reg=pre_reg)
        after = record_outcome(
            record, report, fingerprint=_FP, now=_OOS_DAY, evidence_sha256="e" * 64
        )
        assert after.state == _S.BACKTEST_CERTIFIED
        assert after.parameter_fingerprint == _FP
        assert after.transitions[-1].evidence_sha256 == "e" * 64

    def test_failure_changes_nothing(self, pre_reg: PreRegistration) -> None:
        """A failed non-OOS gate returns the same record."""
        record = CertificationRecord(_SID)
        report = evaluate_next_gate(record, GateEvidence(), now=_OOS_DAY, pre_reg=pre_reg)
        assert record_outcome(record, report, fingerprint=_FP, now=_OOS_DAY) is record

    def test_report_for_another_state_is_rejected(self, pre_reg: PreRegistration) -> None:
        """A report cannot be applied to a record in a different state."""
        report = evaluate_next_gate(
            CertificationRecord(_SID), _ALL_PASS, now=_OOS_DAY, pre_reg=pre_reg
        )
        with pytest.raises(ValueError, match="gate report"):
            record_outcome(_record(_S.SHADOW_CERTIFIED), report, fingerprint=_FP, now=_OOS_DAY)


class TestGuardrails:
    """Reverts that fire whatever gate is next (D-8 guardrail, §7, §10)."""

    # SEV-1: D-8 guardrail — any parameter change sends the strategy back to UNTRUSTED
    @pytest.mark.parametrize(
        "state",
        [
            _S.BACKTEST_CERTIFIED,
            _S.SHADOW_CERTIFIED,
            _S.OOS_CERTIFIED,
            _S.LIVE_APPROVED,
            _S.CERTIFIED,
        ],
    )
    def test_changed_parameters_revert_to_untrusted(self, state: CertificationState) -> None:
        """A different fingerprint from any certified state returns to UNTRUSTED."""
        record = _record(state, oos_evaluated_at=_OOS_DAY)
        after = apply_guardrails(record, fingerprint="0" * 64, evidence=_ALL_PASS, now=_OOS_DAY)
        assert (after.state, after.parameter_fingerprint) == (_S.UNTRUSTED, None)
        assert after.oos_evaluated_at == _OOS_DAY

    def test_unchanged_parameters_keep_the_record(self) -> None:
        """Same fingerprint and clean evidence leave the record as it is."""
        record = _record(_S.SHADOW_CERTIFIED)
        assert apply_guardrails(record, fingerprint=_FP, evidence=_ALL_PASS, now=_OOS_DAY) is record

    def test_dead_stays_dead(self) -> None:
        """DEAD is absorbing even when parameters change."""
        record = _record(_S.DEAD)
        assert (
            apply_guardrails(record, fingerprint="0" * 64, evidence=_ALL_PASS, now=_OOS_DAY)
            is record
        )

    @pytest.mark.parametrize("state", [_S.BACKTEST_CERTIFIED, _S.SHADOW_CERTIFIED])
    def test_shadow_drift_breach_reverts(self, state: CertificationState) -> None:
        """A drift breach in shadow or after it returns to UNTRUSTED (§7, ADR-021, #796)."""
        evidence = replace(_ALL_PASS, shadow=ShadowEvidence(drift_breached=True))
        after = apply_guardrails(_record(state), fingerprint=_FP, evidence=evidence, now=_OOS_DAY)
        assert after.state == _S.UNTRUSTED

    # SEV-1: S6 a fired shadow kill must end the strategy, not let it reach live (#796)
    @pytest.mark.parametrize("state", [_S.BACKTEST_CERTIFIED, _S.SHADOW_CERTIFIED])
    def test_shadow_kill_is_dead(self, state: CertificationState) -> None:
        """A fired §10 shadow kill moves the shadow states to DEAD, even with a drift breach."""
        evidence = replace(_ALL_PASS, shadow=ShadowEvidence(kill_fired=True, drift_breached=True))
        after = apply_guardrails(_record(state), fingerprint=_FP, evidence=evidence, now=_OOS_DAY)
        assert after.state == _S.DEAD

    @pytest.mark.parametrize("state", [_S.OOS_CERTIFIED, _S.LIVE_APPROVED, _S.CERTIFIED])
    def test_shadow_evidence_does_not_move_later_states(self, state: CertificationState) -> None:
        """Past shadow, a stale shadow kill or drift flag leaves the record as it is."""
        evidence = replace(_ALL_PASS, shadow=ShadowEvidence(kill_fired=True, drift_breached=True))
        record = _record(state, oos_evaluated_at=_OOS_DAY)
        assert apply_guardrails(record, fingerprint=_FP, evidence=evidence, now=_OOS_DAY) is record

    # SEV-1: a live kill trigger halts the strategy for good (§10 live phase)
    @pytest.mark.parametrize("state", [_S.LIVE_APPROVED, _S.CERTIFIED])
    def test_live_kill_is_dead(self, state: CertificationState) -> None:
        """A fired live kill moves LIVE_APPROVED or CERTIFIED to DEAD."""
        evidence = replace(_ALL_PASS, live=LiveEvidence(days=10, kill_fired=True))
        after = apply_guardrails(_record(state), fingerprint=_FP, evidence=evidence, now=_OOS_DAY)
        assert after.state == _S.DEAD


class TestFingerprint:
    """The fingerprint covers what v1 trades with (D-5)."""

    def test_key_order_does_not_matter(self) -> None:
        """Canonical JSON makes the digest independent of insertion order."""
        assert parameter_fingerprint({"a": "1", "b": "2"}) == parameter_fingerprint(
            {"b": "2", "a": "1"}
        )

    @given(value=st.text(min_size=1, max_size=8).filter(lambda v: v != "1"))
    def test_any_value_change_changes_the_digest(self, value: str) -> None:
        """Changing one parameter value always changes the fingerprint."""
        assert parameter_fingerprint({"a": "1"}) != parameter_fingerprint({"a": value})

    def test_capital_and_universe_are_covered(self) -> None:
        """Capital budget (§2) and universe (§3) both feed the v1 fingerprint."""
        base = v1_parameter_fingerprint(capital_budget=Decimal("5000"), universe=["BTC/USDT"])
        assert base != v1_parameter_fingerprint(
            capital_budget=Decimal("6000"), universe=["BTC/USDT"]
        )
        assert base != v1_parameter_fingerprint(
            capital_budget=Decimal("5000"), universe=["ETH/USDT"]
        )

    def test_capital_formatting_does_not_matter(self) -> None:
        """5000 and 5000.00 are the same budget."""
        assert v1_parameter_fingerprint(
            capital_budget=Decimal("5000"), universe=["BTC/USDT"]
        ) == v1_parameter_fingerprint(capital_budget=Decimal("5000.00"), universe=["BTC/USDT"])


class TestRequireLiveApproval:
    """Real capital needs LIVE_APPROVED or CERTIFIED with unchanged parameters."""

    # SEV-1: no mainnet path without live approval (ADR-014 decision 2)
    @pytest.mark.parametrize(
        ("record", "reason"),
        [
            (None, "no certification record"),
            (_record(_S.OOS_CERTIFIED), "not LIVE_APPROVED"),
            (_record(_S.LIVE_APPROVED, fingerprint="0" * 64), "parameters changed"),
        ],
    )
    def test_refusals(self, record: CertificationRecord | None, reason: str) -> None:
        """Missing record, wrong state and changed parameters are each refused."""
        assert reason in require_live_approval(record, _FP)

    @pytest.mark.parametrize("state", [_S.LIVE_APPROVED, _S.CERTIFIED])
    def test_approved_states_are_allowed(self, state: CertificationState) -> None:
        """LIVE_APPROVED and CERTIFIED with the same fingerprint are allowed."""
        assert require_live_approval(_record(state), _FP) == ""
