"""Unit tests for v2's backtest gate (pre-registration-v2.md §7, §9, §10; #1008).

The gate is pure, so the tests drive it with hand-built evidence and a fixed
clock. Every pre-registration text except the one table test is synthetic.
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from hermes.application.calibration.backtest_funnel import (
    FUNNEL_CHECK_NAMES,
    CostStress,
    FunnelEvidence,
    FunnelThresholdError,
    FunnelThresholds,
    Regime,
)
from hermes.application.calibration.certification_gates import (
    CertificationRecord,
    GateEvidence,
    GateReport,
    WindowMetrics,
)
from hermes.application.calibration.certification_gates_v2 import (
    V2_STRATEGY_ID,
    V2BacktestRules,
    V2RulesError,
    evaluate_v2_gate,
    parse_v2_backtest_rules,
)
from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.entrypoints.funnel import read_front_matter
from tests.support.paths import REPO_ROOT

_S = CertificationState
_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
_DAYS = 730
"""31 Dec 2020 to 31 Dec 2022: Train is 2021, Validation is 2022."""

_FUNNEL = FunnelThresholds(
    train_end=date(2021, 12, 31),
    oos_start=date(2025, 9, 1),
    cscv_blocks=4,
    pbo_max=0.05,
    t_stat_above=3.0,
    trade_count_min=100,
    cost_stress_multiplier=2.0,
    positive_year_share_above=0.5,
    validation_to_train_return_min=0.5,
    neighbour_sharpe_ratio_min=0.5,
    return_correlation_below=0.7,
    regimes=(
        Regime("bull", date(2021, 2, 1), date(2021, 5, 1)),
        Regime("bear", date(2022, 3, 1), date(2022, 6, 1)),
    ),
)
_RULES = V2BacktestRules(dsr_min=0.95, funnel=_FUNNEL)

_V1_ROWS = [
    "dsr",
    "max_drawdown",
    "recovery_days",
    "profit_factor",
    "ulcer_index",
    "fees_to_gross_pnl",
    "win_rate",
    "calibration_fills",
]
"""v1's backtest rows in order (certification_gates._backtest_gate), written out by hand."""


def _metrics(dsr: float | None, max_drawdown: float = 0.10) -> WindowMetrics:
    return WindowMetrics(
        dsr=dsr,
        bootstrap_ci_lower=0.1,
        trade_count=140,
        max_drawdown=max_drawdown,
        recovery_days=30.0,
        profit_factor=1.5,
        ulcer_index=6.0,
        fees_to_gross_pnl=0.2,
        win_rate=0.4,
    )


def _returns(drift: float) -> tuple[float, ...]:
    """Alternating returns ``drift ± 0.001``: positive mean, dispersion in every block."""
    return tuple(drift + (0.001 if i % 2 else -0.001) for i in range(_DAYS))


def _equity(returns: tuple[float, ...]) -> tuple[float, ...]:
    points = [5000.0]
    for r in returns:
        points.append(points[-1] * (1.0 + r))
    return tuple(points)


def _passing_funnel() -> FunnelEvidence:
    base = _returns(0.002)
    return FunnelEvidence(
        strategy_id=V2_STRATEGY_ID,
        start_date=date(2020, 12, 31),
        daily_equity=_equity(base),
        trade_count=140,
        cost_stress=CostStress(2.0, _equity(_returns(0.001))),
        trials={
            "t1": base,
            "t2": tuple(r + 0.0001 for r in base),
            "t3": tuple(r - 0.0002 for r in base),
            "n1": _returns(0.0015),
        },
        neighbour_trial_ids=("n1",),
        running_strategies={},
    )


@pytest.fixture(scope="module")
def v1() -> PreRegistration:
    """Return the real signed v1 pre-registration, whose §9 secondaries v2 keeps."""
    return PreRegistration(project_root=REPO_ROOT)


def _gate(
    v1: PreRegistration,
    dsr: float | None,
    *,
    funnel: FunnelEvidence | None = None,
    state: CertificationState = _S.UNTRUSTED,
    max_drawdown: float = 0.10,
) -> GateReport:
    record = CertificationRecord(V2_STRATEGY_ID, state)
    evidence = GateEvidence(backtest=_metrics(dsr, max_drawdown), calibration_fill_count=40)
    return evaluate_v2_gate(record, evidence, now=_NOW, v1=v1, rules=_RULES, funnel=funnel)


def _row(report: GateReport, name: str) -> tuple[bool, str, str]:
    (check,) = (c for c in report.checks if c.name == name)
    return check.passed, check.observed, check.requirement


class TestDsrBoundary:
    """v2 §10 kills a backtest with DSR < 0.95, so DSR ≥ 0.95 passes the row."""

    def test_dsr_094_fails(self, v1: PreRegistration) -> None:
        """0.94 is below the floor: the dsr row fails and the gate fails (#1008 acceptance)."""
        report = _gate(v1, 0.94, funnel=_passing_funnel())
        assert (_row(report, "dsr"), report.passed) == ((False, "0.94", ">= 0.95"), False)

    def test_dsr_095_passes(self, v1: PreRegistration) -> None:
        """0.95 is at the floor: ≥, not >, so the row and the whole gate pass (#1008 acceptance)."""
        report = _gate(v1, 0.95, funnel=_passing_funnel())
        assert (_row(report, "dsr"), report.passed) == ((True, "0.95", ">= 0.95"), True)

    def test_dsr_08_passes_v1_but_fails_v2(self, v1: PreRegistration) -> None:
        """v1's floor (> 0.7) no longer applies: 0.8 fails v2's dsr row."""
        report = _gate(v1, 0.8, funnel=_passing_funnel())
        assert _row(report, "dsr")[0] is False

    def test_missing_dsr_fails_closed(self, v1: PreRegistration) -> None:
        """A DSR that was not computed fails its row, as v1's does."""
        report = _gate(v1, None, funnel=_passing_funnel())
        assert _row(report, "dsr") == (False, "not computed (#354)", ">= 0.95")

    @given(
        dsr=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        floor=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )
    @example(dsr=0.95, floor=0.95)
    @example(dsr=0.9499999999999998, floor=0.95)
    def test_row_passes_exactly_when_dsr_reaches_the_floor(
        self, v1: PreRegistration, dsr: float, floor: float
    ) -> None:
        """For any DSR and floor, the row passes iff dsr >= floor (v2 §0 row 4)."""
        record = CertificationRecord(V2_STRATEGY_ID, _S.UNTRUSTED)
        evidence = GateEvidence(backtest=_metrics(dsr), calibration_fill_count=40)
        rules = V2BacktestRules(dsr_min=floor, funnel=_FUNNEL)
        report = evaluate_v2_gate(record, evidence, now=_NOW, v1=v1, rules=rules, funnel=None)
        assert _row(report, "dsr")[0] is (dsr >= floor)


class TestRows:
    """v2's backtest gate is v1's rows with the v2 DSR, plus the funnel (v2 §7, §10)."""

    def test_rows_are_v1_rows_then_the_funnel(self, v1: PreRegistration) -> None:
        """v1's backtest rows in order, then the ten funnel checks."""
        report = _gate(v1, 0.95, funnel=_passing_funnel())
        assert [c.name for c in report.checks] == [*_V1_ROWS, *FUNNEL_CHECK_NAMES]

    def test_secondary_rows_keep_v1_thresholds(self, v1: PreRegistration) -> None:
        """v2 §9 keeps v1's max drawdown < 15%: exactly 15% fails it."""
        report = _gate(v1, 0.95, funnel=_passing_funnel(), max_drawdown=0.15)
        assert _row(report, "max_drawdown") == (False, "0.15", "< 0.15")

    def test_missing_funnel_evidence_fails_one_funnel_row(self, v1: PreRegistration) -> None:
        """No funnel evidence: one failing `funnel` row, so BACKTEST_CERTIFIED cannot be reached."""
        report = _gate(v1, 0.95)
        assert (report.failed_checks, report.passed) == (("funnel",), False)

    def test_failing_funnel_check_fails_the_gate(self, v1: PreRegistration) -> None:
        """A funnel check that fails (99 trades < 100) fails the gate with that row named."""
        funnel = _passing_funnel()
        short = FunnelEvidence(
            strategy_id=funnel.strategy_id,
            start_date=funnel.start_date,
            daily_equity=funnel.daily_equity,
            trade_count=99,
            cost_stress=funnel.cost_stress,
            trials=funnel.trials,
            neighbour_trial_ids=funnel.neighbour_trial_ids,
            running_strategies=funnel.running_strategies,
        )
        report = _gate(v1, 0.95, funnel=short)
        assert report.failed_checks == ("trade_count",)

    def test_targets_backtest_certified(self, v1: PreRegistration) -> None:
        """The gate out of UNTRUSTED leads to BACKTEST_CERTIFIED, as v1's does (v2 §7)."""
        report = _gate(v1, 0.95, funnel=_passing_funnel())
        assert (report.from_state, report.to_state) == (_S.UNTRUSTED, _S.BACKTEST_CERTIFIED)


class TestLaterStates:
    """Only the backtest gate is implemented for v2; later ones are refused (change Scope, Out)."""

    @pytest.mark.parametrize(
        "state",
        [_S.BACKTEST_CERTIFIED, _S.SHADOW_CERTIFIED, _S.OOS_CERTIFIED, _S.LIVE_APPROVED],
    )
    def test_gates_past_untrusted_are_refused(
        self, v1: PreRegistration, state: CertificationState
    ) -> None:
        """v2 §7 changes the shadow rule, so v1's later gates must not judge v2."""
        report = _gate(v1, 0.95, funnel=_passing_funnel(), state=state)
        assert (report.to_state, report.checks, report.passed) == (None, (), False)
        assert report.refused_reason == (
            f"the gate out of {state} is not implemented for {V2_STRATEGY_ID}; "
            "pre-registration v2 §7 changes the shadow rule, so v1's gate does not apply."
        )

    def test_dead_is_refused_as_in_v1(self, v1: PreRegistration) -> None:
        """DEAD stays absorbing for v2 (v2 §10 post-kill protocol, unchanged from v1)."""
        report = _gate(v1, 0.95, state=_S.DEAD)
        assert report.refused_reason == (
            "DEAD is absorbing; a new pre-registration is required (§10)."
        )


_DOCUMENT = """\
---
front matter is passed separately
---

## 9. Metrics and thresholds

### Primary (must pass for LIVE_APPROVED)

Unchanged from v1: DSR on OOS > 0.7.

### Secondary (all must pass)

Unchanged from v1: max drawdown < 15%.

---

## 10. Kill criteria

### Backtest phase (do not proceed to shadow)

On Train + Validation, any of:

- DSR < {dsr};
- any §9 secondary metric fails.

### Shadow phase and live phase

- DSR < 0.3;
"""


def _front_matter(**changes: object) -> dict[str, object]:
    funnel: Mapping[str, object] = {
        "train_end": "2024-06-30",
        "oos_start": "2025-09-01",
        "cscv_blocks": 16,
        "pbo_max": 0.04,
        "t_stat_above": 3.0,
        "trade_count_min": 100,
        "cost_stress_multiplier": 2.0,
        "positive_year_share_above": 0.5,
        "validation_to_train_return_min": 0.5,
        "neighbour_sharpe_ratio_min": 0.5,
        "return_correlation_below": 0.7,
        "regimes": [
            {"kind": "bull", "start": "2020-10-01", "end": "2021-11-10"},
            {"kind": "bear", "start": "2021-11-11", "end": "2022-11-21"},
        ],
    }
    base: dict[str, object] = {
        "experiment_id": "swing-mvp1-v2",
        "strategy_id": "swing-mvp1-v2",
        "status": "SIGNED",
        "backtest_funnel": funnel,
    }
    return {**base, **changes}


class TestParse:
    """The v2 rules come from the signed v2 file only (#1008 scope)."""

    def test_dsr_floor_is_read_from_section_10_backtest_phase(self) -> None:
        """The floor is the §10 backtest bullet's number, not the shadow bullet's 0.3."""
        rules = parse_v2_backtest_rules(_DOCUMENT.format(dsr="0.6"), _front_matter())
        assert rules.dsr_min == 0.6

    def test_funnel_thresholds_come_from_the_front_matter(self) -> None:
        """The funnel block is parsed by the funnel's own parser: pbo_max 0.04 as written."""
        rules = parse_v2_backtest_rules(_DOCUMENT.format(dsr="0.95"), _front_matter())
        assert rules.funnel.pbo_max == 0.04

    def test_no_dsr_bullet_is_refused(self) -> None:
        """A §10 backtest phase without a DSR bullet refuses rather than guesses."""
        document = _DOCUMENT.format(dsr="0.95").replace("- DSR < 0.95;\n", "")
        with pytest.raises(V2RulesError, match=r"^§10 backtest phase states 0 DSR floors, not 1$"):
            parse_v2_backtest_rules(document, _front_matter())

    def test_two_dsr_bullets_are_refused(self) -> None:
        """Two DSR bullets in the §10 backtest phase are ambiguous and refused."""
        document = _DOCUMENT.format(dsr="0.95").replace(
            "- DSR < 0.95;\n", "- DSR < 0.95;\n- DSR < 0.9;\n"
        )
        with pytest.raises(V2RulesError, match=r"^§10 backtest phase states 2 DSR floors, not 1$"):
            parse_v2_backtest_rules(document, _front_matter())

    def test_changed_secondaries_are_refused(self) -> None:
        """v1's secondary thresholds are reused only while v2 §9 says they are unchanged."""
        document = _DOCUMENT.format(dsr="0.95").replace(
            "Unchanged from v1: max drawdown < 15%.", "Max drawdown < 12%."
        )
        with pytest.raises(
            V2RulesError, match=r"^§9 secondary metrics no longer say 'Unchanged from v1'$"
        ):
            parse_v2_backtest_rules(document, _front_matter())

    def test_another_experiment_is_refused(self) -> None:
        """Only the swing-mvp1-v2 pre-registration states v2's gate."""
        with pytest.raises(V2RulesError, match=r"^experiment_id is 'swing-mvp1-v3'"):
            parse_v2_backtest_rules(
                _DOCUMENT.format(dsr="0.95"), _front_matter(experiment_id="swing-mvp1-v3")
            )

    def test_unsigned_document_is_refused(self) -> None:
        """A draft is not binding: the funnel parser refuses a status other than SIGNED."""
        with pytest.raises(FunnelThresholdError, match=r"status is 'DRAFT', not SIGNED"):
            parse_v2_backtest_rules(_DOCUMENT.format(dsr="0.95"), _front_matter(status="DRAFT"))


class TestSignedFile:
    """The one table test against the signed text (ai-authored-tests rule 7)."""

    def test_signed_v2_states_a_dsr_floor_of_095(self) -> None:
        """pre-registration-v2.md §10: 'DSR < 0.95' kills the backtest."""
        text = (
            REPO_ROOT / "docs" / "experiments" / "swing-mvp1" / "pre-registration-v2.md"
        ).read_text(encoding="utf-8")
        rules = parse_v2_backtest_rules(text, read_front_matter(text))
        assert (rules.dsr_min, rules.funnel.cscv_blocks, rules.funnel.pbo_max) == (0.95, 16, 0.05)
