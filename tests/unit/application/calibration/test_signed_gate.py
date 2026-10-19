"""Tests for the generic gate (ADR-026 §1, §4; issue #1058; contract §3).

The judge tests write a synthetic batch and a candidate's three evidence files
(``tests/support/candidate_evidence.py``): Train is 2021, Validation is 2022.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hermes.adapters.files import LocalFiles
from hermes.application.calibration.backtest_funnel import FUNNEL_CHECK_NAMES
from hermes.application.calibration.certification_gates_v2 import (
    V2BacktestRules,
    evaluate_v2_gate,
)
from hermes.application.calibration.evidence_files import EvidenceError
from hermes.application.calibration.signed_gate import (
    CandidateJudge,
    CheckResult,
    CheckStatus,
    GateResult,
    Split,
    candidate_gate_evidence,
    evaluate_signed_gate,
    judge_candidate,
    require_v1_secondary,
)
from hermes.domain.calibration.certification import CertificationRecord, CertificationState
from hermes.domain.calibration.trial_record import TrialSummary
from hermes.infrastructure.project_root import load_pre_registration
from tests.support.candidate_evidence import (
    BODY,
    SECONDARY_V1,
    STRATEGY_ID,
    TRAIN_DAYS,
    write_candidate,
)
from tests.support.candidate_evidence import batch as candidate_batch
from tests.support.paths import REPO_ROOT

_FILES = LocalFiles()
_V1 = load_pre_registration(REPO_ROOT)

_NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
_FEW_TRIALS = TrialSummary(count=2, validation_count=0, sharpe_variance=0.0001)
_V1_ROWS = (
    "dsr",
    "max_drawdown",
    "recovery_days",
    "profit_factor",
    "ulcer_index",
    "fees_to_gross_pnl",
    "win_rate",
    "calibration_fills",
)
"""v1's backtest rows in order (certification_gates._backtest_gate), written out by hand."""


def _result(statuses: list[CheckStatus], *, passed: bool) -> GateResult:
    checks = tuple(CheckResult(f"c{i}", s, "") for i, s in enumerate(statuses))
    return GateResult("swing-alpha", "train", checks, dsr=None, passed=passed)


@pytest.mark.parametrize("statuses", [["PASS"], ["PASS", "NA"], ["NA", "PASS", "PASS"]])
def test_a_verdict_passes_when_no_check_fails(statuses: list[CheckStatus]) -> None:
    """NA counts as no objection: validation_vs_train is NA on a Train-only split."""
    assert _result(statuses, passed=True).passed is True


@pytest.mark.parametrize("statuses", [["FAIL"], ["PASS", "FAIL"], ["NA", "FAIL"]])
def test_a_passed_verdict_with_a_failed_check_is_refused(statuses: list[CheckStatus]) -> None:
    """One FAIL fails the gate; a result that claims otherwise is a bug, not a verdict."""
    with pytest.raises(ValueError, match="passed=True"):
        _result(statuses, passed=True)


def test_a_failed_verdict_with_no_failed_check_is_refused() -> None:
    """A gate cannot fail a candidate without naming the check that failed."""
    with pytest.raises(ValueError, match="passed=False"):
        _result(["PASS", "NA"], passed=False)


def test_a_verdict_with_no_checks_cannot_pass() -> None:
    """A gate that ran nothing has passed nothing."""
    with pytest.raises(ValueError, match="passed=True"):
        _result([], passed=True)


# ---------------------------------------------------------------------------
# judge_candidate (issue #1058, contract §3)
# ---------------------------------------------------------------------------


def _judge(
    tmp_path: Path,
    split: Split,
    *,
    trials: TrialSummary = _FEW_TRIALS,
    **written: Any,
) -> GateResult:
    files = write_candidate(tmp_path, **written).files
    return judge_candidate(
        STRATEGY_ID, split, files, candidate_batch(), trials, v1=_V1, files=_FILES
    )


def _statuses(result: GateResult) -> dict[str, CheckStatus]:
    return {c.check: c.status for c in result.checks}


def test_validation_vs_train_is_na_on_a_train_run(tmp_path: Path) -> None:
    """Issue #1037: a Train-only run has no Validation stretch, so the check is NA, not FAIL."""
    result = _judge(tmp_path, "train", days=TRAIN_DAYS)

    assert (_statuses(result)["validation_vs_train"], result.passed) == ("NA", True)


def test_validation_vs_train_binds_on_a_validation_run(tmp_path: Path) -> None:
    """On Train + Validation the signed rule applies: a weak Validation stretch fails it."""
    files = write_candidate(tmp_path).files
    payload = json.loads(files.funnel_evidence.read_text())
    train_points = TRAIN_DAYS + 1
    flat = [payload["daily_equity"][train_points - 1]] * (
        len(payload["daily_equity"]) - train_points
    )
    payload["daily_equity"] = payload["daily_equity"][:train_points] + flat
    files.funnel_evidence.write_text(json.dumps(payload))

    result = judge_candidate(
        STRATEGY_ID, "validation", files, candidate_batch(), _FEW_TRIALS, v1=_V1, files=_FILES
    )

    assert (_statuses(result)["validation_vs_train"], result.passed) == ("FAIL", False)


def test_a_strong_candidate_passes_every_check_on_validation(tmp_path: Path) -> None:
    """The gate's rows: v1's backtest rows, then the funnel, every one PASS on strong evidence."""
    result = _judge(tmp_path, "validation")

    assert [(c.check, c.status) for c in result.checks] == [
        (name, "PASS") for name in (*_V1_ROWS, *FUNNEL_CHECK_NAMES)
    ]


def test_the_dsr_is_deflated_by_the_trials_passed_in(tmp_path: Path) -> None:
    """ADR-026 §4: the batch-wide N, not the run's own trials block, deflates the DSR."""
    files = write_candidate(tmp_path, days=TRAIN_DAYS, drift=0.0002).files
    few = judge_candidate(
        STRATEGY_ID, "train", files, candidate_batch(), _FEW_TRIALS, v1=_V1, files=_FILES
    )
    many = judge_candidate(
        STRATEGY_ID,
        "train",
        files,
        candidate_batch(),
        TrialSummary(1000, 0, 0.01),
        v1=_V1,
        files=_FILES,
    )

    assert (few.passed, _statuses(many)["dsr"]) == (True, "FAIL")


def test_no_sharpe_variance_fails_the_dsr_row(tmp_path: Path) -> None:
    """Fewer than two Sharpes give no variance: the DSR is not computed and fails closed."""
    result = _judge(tmp_path, "train", days=TRAIN_DAYS, trials=TrialSummary(1, 0, None))

    assert (result.dsr, _statuses(result)["dsr"]) == (None, "FAIL")


def test_the_dsr_floor_is_the_batch_s(tmp_path: Path) -> None:
    """Thresholds come from the batch: the dsr row requires the signed dsr_min."""
    result = _judge(tmp_path, "validation")

    (dsr,) = (c for c in result.checks if c.check == "dsr")
    assert dsr.detail.endswith("required >= 0.95")


def test_a_candidate_the_batch_does_not_name_is_refused(tmp_path: Path) -> None:
    """Only a candidate a signed batch names is judged."""
    files = write_candidate(tmp_path).files

    with pytest.raises(ValueError, match="does not name"):
        judge_candidate(
            "swing-x", "validation", files, candidate_batch(), _FEW_TRIALS, v1=_V1, files=_FILES
        )


def test_train_evidence_judged_as_validation_is_refused(tmp_path: Path) -> None:
    """A Train-only run cannot be passed off as Train + Validation."""
    files = write_candidate(tmp_path, days=TRAIN_DAYS).files

    with pytest.raises(ValueError, match="not a validation run"):
        judge_candidate(
            STRATEGY_ID, "validation", files, candidate_batch(), _FEW_TRIALS, v1=_V1, files=_FILES
        )


def test_validation_evidence_judged_as_train_is_refused(tmp_path: Path) -> None:
    """A run that read Validation is not a Train-only run, so its NA would hide a check."""
    files = write_candidate(tmp_path).files

    with pytest.raises(ValueError, match="not a train run"):
        judge_candidate(
            STRATEGY_ID, "train", files, candidate_batch(), _FEW_TRIALS, v1=_V1, files=_FILES
        )


def test_an_oos_performance_file_is_refused(tmp_path: Path) -> None:
    """The backtest gate reads the Train + Validation window only."""
    with pytest.raises(EvidenceError, match="not train_val"):
        _judge(tmp_path, "validation", window="oos")


def test_a_section_that_changes_the_secondaries_is_refused(tmp_path: Path) -> None:
    """A batch signs no §9 numbers: a section that does not keep v1's is not judged."""
    body = BODY.replace(SECONDARY_V1, "Max drawdown < 25%.", 1)
    files = write_candidate(tmp_path, body=body).files

    with pytest.raises(ValueError, match="Unchanged from v1"):
        judge_candidate(
            STRATEGY_ID,
            "validation",
            files,
            candidate_batch(body),
            _FEW_TRIALS,
            v1=_V1,
            files=_FILES,
        )


@pytest.mark.parametrize(
    ("section", "kept"),
    [
        ("## C1\n### 9. Metrics\n#### Secondary\nUnchanged from v1.\n", True),
        (
            "## C1 — swing-alpha\n\n### 9. Metrics and thresholds (C1)\n\n#### Secondary\n\n"
            "Unchanged from v1 (§9 table).\n",
            True,
        ),
        ("## C1\n## 9. Metrics\n### Secondary metrics\n\nUnchanged from v1: all.\n", True),
        ("## C1\n### 9. Metrics\n#### Secondary\nMax drawdown < 20%.\n", False),
        ("## C1\n### 8. Trials\n#### Secondary\nUnchanged from v1.\n", False),
        ("## C1\n### 9. Metrics\n### 10. Kill\n#### Secondary\nUnchanged from v1.\n", False),
        ("## C1\nNo metrics.\n", False),
    ],
)
def test_the_secondary_rule_reads_only_section_9(section: str, *, kept: bool) -> None:
    """The sentence counts only under a Secondary heading inside the §9 heading."""
    try:
        require_v1_secondary(section)
        refused = False
    except ValueError:
        refused = True

    assert refused is not kept


def test_v2_and_the_generic_gate_give_the_same_report(tmp_path: Path) -> None:
    """Issue #1058: v2's gate is the generic gate with v2's thresholds; its output is unchanged."""
    files = write_candidate(tmp_path).files
    gate_evidence, funnel = candidate_gate_evidence(
        STRATEGY_ID, files, candidate_batch(), _FEW_TRIALS, files=_FILES
    )
    batch = candidate_batch()
    v1 = load_pre_registration(REPO_ROOT)
    record = CertificationRecord(STRATEGY_ID)
    rules = V2BacktestRules(dsr_min=batch.funnel.dsr_min, funnel=batch.funnel.thresholds)

    v2 = evaluate_v2_gate(record, gate_evidence, now=_NOW, v1=v1, rules=rules, funnel=funnel)
    generic = evaluate_signed_gate(
        record,
        gate_evidence,
        now=_NOW,
        v1=v1,
        dsr_min=rules.dsr_min,
        thresholds=rules.funnel,
        funnel=funnel,
        refusal="unused",
    )

    assert (v2.checks, v2.to_state, [c.name for c in generic.checks]) == (
        generic.checks,
        CertificationState.BACKTEST_CERTIFIED,
        [*_V1_ROWS, *FUNNEL_CHECK_NAMES],
    )


def test_the_evidence_hash_chains_all_three_files(tmp_path: Path) -> None:
    """A recorded verdict names the exact files it was judged on, as certify chains them."""
    files = write_candidate(tmp_path).files
    first, _ = candidate_gate_evidence(
        STRATEGY_ID, files, candidate_batch(), _FEW_TRIALS, files=_FILES
    )
    files.funnel_evidence.write_text(files.funnel_evidence.read_text() + "\n")
    second, _ = candidate_gate_evidence(
        STRATEGY_ID, files, candidate_batch(), _FEW_TRIALS, files=_FILES
    )

    assert first.sha256 != second.sha256


def test_judge_candidate_is_a_candidate_judge() -> None:
    """The batch judge (#1059) types its dependency as CandidateJudge; this is the one it gets."""
    judge: CandidateJudge = judge_candidate

    assert judge is judge_candidate
