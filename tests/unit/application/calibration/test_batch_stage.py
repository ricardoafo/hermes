"""Tests for judging a research batch at one stage (ADR-026 §3-§5; issue #1059; contract §4, §5).

Candidates are the synthetic batch's ``swing-alpha`` and ``swing-beta`` with the
three evidence files of ``tests/support/candidate_evidence.py``: Train is 2021,
Validation 2022. Selection tests replace the gate with a stub so each DSR is
chosen by hand; the evidence files still give the window and the returns.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.application.calibration.batch_stage import (
    MISSING_EVIDENCE,
    StageOutcome,
    judge_stage,
    stage_summary,
    verdict_record,
)
from hermes.application.calibration.research_batch import ResearchBatch
from hermes.application.calibration.signed_gate import (
    CandidateEvidence,
    CheckResult,
    GateResult,
    Split,
)
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord, TrialSummary
from hermes.domain.market_data.dataset_manifest import DataSplit
from tests.support.candidate_evidence import TRAIN_DAYS, write_candidate
from tests.support.candidate_evidence import batch as candidate_batch

_ALPHA = "swing-alpha"
_BETA = "swing-beta"


def _record(strategy_id: str, split: DataSplit, batch_id: str | None, minute: int) -> TrialRecord:
    parameters = (
        {"lookback": "20"} if batch_id is None else {"lookback": "20", "batch.id": batch_id}
    )
    return TrialRecord(
        recorded_at=datetime(2026, 10, 18, 9, minute, tzinfo=UTC),
        strategy_id=strategy_id,
        parameters=parameters,
        split=split,
        window_start=datetime(2020, 12, 31, tzinfo=UTC),
        window_end=datetime(2021, 12, 31, tzinfo=UTC),
        datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
        metrics={"fills_count": "40"},
        sharpe=0.05 + minute / 1000,
        git_sha="d" * 40,
        git_dirty=False,
    )


_LOG = (
    _record(_ALPHA, DataSplit.TRAIN, "b1", 0),
    _record(_ALPHA, DataSplit.TRAIN, "b1", 1),
)


class _StubJudge:
    """Return a chosen verdict per candidate and remember the trial summary it was given."""

    def __init__(self, verdicts: Mapping[str, tuple[bool, float | None]]) -> None:
        self._verdicts = verdicts
        self.trials: list[TrialSummary] = []

    def __call__(
        self,
        strategy_id: str,
        split: Split,
        evidence: CandidateEvidence,
        batch: ResearchBatch,
        trials: TrialSummary,
    ) -> GateResult:
        self.trials.append(trials)
        passed, dsr = self._verdicts[strategy_id]
        check = CheckResult("dsr", "PASS", "stub") if passed else CheckResult("dsr", "FAIL", "stub")
        return GateResult(
            strategy_id=strategy_id,
            split=split,
            checks=(check,),
            dsr=dsr,
            passed=passed,
        )


def _write(root: Path, *ids: str, days: int = 730) -> Path:
    for strategy_id in ids:
        write_candidate(root, strategy_id=strategy_id, days=days)
    return root / "evidence"


def _statuses(outcome: StageOutcome) -> dict[str, list[tuple[str, str]]]:
    return {v.strategy_id: [(c.check, c.status) for c in v.checks] for v in outcome.verdicts}


class TestTrain:
    """After Train, every candidate whose gate passed goes on (ADR-026 §3)."""

    def test_strong_candidates_both_go_on_in_batch_order(self, tmp_path: Path) -> None:
        """Both pass on Train with validation_vs_train NA, so both go on: alpha, then beta."""
        evidence = _write(tmp_path, _BETA, _ALPHA, days=TRAIN_DAYS)

        outcome = judge_stage(candidate_batch(), "train", evidence, _LOG)

        na = {
            v.strategy_id: dict(_statuses(outcome)[v.strategy_id])["validation_vs_train"]
            for v in outcome.verdicts
        }
        assert (outcome.goes_on, na) == ((_ALPHA, _BETA), {_ALPHA: "NA", _BETA: "NA"})

    def test_a_candidate_without_evidence_fails_missing_evidence(self, tmp_path: Path) -> None:
        """Contract §4: no folder for beta is FAIL beta missing_evidence, and beta stays."""
        evidence = _write(tmp_path, _ALPHA, days=TRAIN_DAYS)

        outcome = judge_stage(candidate_batch(), "train", evidence, _LOG)

        assert (outcome.goes_on, _statuses(outcome)[_BETA]) == (
            (_ALPHA,),
            [(MISSING_EVIDENCE, "FAIL")],
        )

    def test_a_failed_gate_does_not_go_on(self, tmp_path: Path) -> None:
        """A candidate whose gate fails on Train does not reach Validation."""
        evidence = _write(tmp_path, _ALPHA, _BETA, days=TRAIN_DAYS)
        judge = _StubJudge({_ALPHA: (False, 0.5), _BETA: (True, 0.99)})

        outcome = judge_stage(candidate_batch(), "train", evidence, _LOG, judge=judge)

        assert outcome.goes_on == (_BETA,)

    def test_train_names_no_champion(self, tmp_path: Path) -> None:
        """The champion is picked only after Validation."""
        evidence = _write(tmp_path, _ALPHA, _BETA, days=TRAIN_DAYS)
        judge = _StubJudge({_ALPHA: (True, 0.99), _BETA: (True, 0.99)})

        outcome = judge_stage(candidate_batch(), "train", evidence, _LOG, judge=judge)

        assert (outcome.survivors, outcome.champion) == ((), None)


class TestTrialCount:
    """Every candidate is deflated by one N: the whole log (ADR-026 §4)."""

    def test_every_candidate_gets_the_count_of_the_whole_log(self, tmp_path: Path) -> None:
        """Five lines over three strategies and both splits: N = 5 for alpha and for beta."""
        log = (
            *_LOG,
            _record(_BETA, DataSplit.TRAIN, "b1", 2),
            _record("swing-donchian-4h", DataSplit.VALIDATION, None, 3),
            _record(_ALPHA, DataSplit.VALIDATION, "b0", 4),
        )
        evidence = _write(tmp_path, _ALPHA, _BETA, days=TRAIN_DAYS)
        judge = _StubJudge({_ALPHA: (True, 0.99), _BETA: (True, 0.99)})

        judge_stage(candidate_batch(), "train", evidence, log, judge=judge)

        assert [t.count for t in judge.trials] == [5, 5]

    def test_trial_lines_count_this_candidate_batch_and_split_only(self, tmp_path: Path) -> None:
        """Alpha has two Train lines in b1; its b2, Validation and unbatched lines do not count."""
        log = (
            *_LOG,
            _record(_ALPHA, DataSplit.TRAIN, "b2", 2),
            _record(_ALPHA, DataSplit.VALIDATION, "b1", 3),
            _record(_BETA, DataSplit.TRAIN, "b1", 4),
            _record(_ALPHA, DataSplit.TRAIN, None, 5),
        )
        evidence = _write(tmp_path, _ALPHA, days=TRAIN_DAYS)

        outcome = judge_stage(candidate_batch(), "train", evidence, log)

        assert {v.strategy_id: v.trial_lines for v in outcome.verdicts} == {_ALPHA: 2, _BETA: 1}


class TestValidation:
    """After Validation the signed rule picks the champion, or none (ADR-026 §5)."""

    def test_the_highest_dsr_survivor_is_champion(self, tmp_path: Path) -> None:
        """DSR 0.97 and 0.99: p = 0.03 and 0.01, both under BH at q = 0.05; beta ranks first."""
        evidence = _write(tmp_path, _ALPHA, _BETA)
        judge = _StubJudge({_ALPHA: (True, 0.97), _BETA: (True, 0.99)})

        outcome = judge_stage(candidate_batch(), "validation", evidence, _LOG, judge=judge)

        assert (outcome.survivors, outcome.champion, outcome.goes_on) == (
            (_BETA, _ALPHA),
            _BETA,
            (),
        )

    def test_bh_counts_a_candidate_without_evidence(self, tmp_path: Path) -> None:
        """BH's m is the batch size: alpha's p = 0.03 exceeds 1 x 0.05 / 2 once beta (p = 1) counts.

        Alone (m = 1) it would survive; with beta in the batch, no champion.
        """
        evidence = _write(tmp_path, _ALPHA)
        judge = _StubJudge({_ALPHA: (True, 0.97)})

        outcome = judge_stage(candidate_batch(), "validation", evidence, _LOG, judge=judge)

        assert (outcome.survivors, outcome.champion) == ((), None)

    def test_a_failed_gate_is_never_champion(self, tmp_path: Path) -> None:
        """Beta's DSR is higher, but its gate failed: alpha (0.99, p = 0.01) is champion."""
        evidence = _write(tmp_path, _ALPHA, _BETA)
        judge = _StubJudge({_ALPHA: (True, 0.99), _BETA: (False, 0.999)})

        outcome = judge_stage(candidate_batch(), "validation", evidence, _LOG, judge=judge)

        assert (outcome.survivors, outcome.champion) == ((_ALPHA,), _ALPHA)

    def test_candidates_over_different_windows_are_refused(self, tmp_path: Path) -> None:
        """The tie-break compares daily returns day by day, so the windows must match."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=730)
        write_candidate(tmp_path, strategy_id=_BETA, days=700)
        judge = _StubJudge({_ALPHA: (True, 0.99), _BETA: (True, 0.99)})

        with pytest.raises(ValueError, match="different windows"):
            judge_stage(candidate_batch(), "validation", tmp_path / "evidence", _LOG, judge=judge)


class TestRecords:
    """The verdict record holds statuses only (contract §5, ADR-012 D-06)."""

    def test_a_train_verdict_record_is_the_contract_shape(self, tmp_path: Path) -> None:
        """Every field of the §5 example, for alpha passing Train with two trial lines."""
        evidence = _write(tmp_path, _ALPHA, days=TRAIN_DAYS)
        judge = _StubJudge({_ALPHA: (True, 0.99)})
        outcome = judge_stage(candidate_batch(), "train", evidence, _LOG, judge=judge)

        record = verdict_record(outcome, outcome.verdicts[0])

        assert record == {
            "schema": "hermes.verdict/1",
            "batch_id": "b1",
            "strategy_id": _ALPHA,
            "idea_family": "breakout",
            "stage": "train",
            "run_id": None,
            "commit": None,
            "window": {"start": "2020-12-31", "end": "2021-12-31"},
            "trial_lines": 2,
            "checks": [{"check": "dsr", "status": "PASS"}],
            "passed": True,
            "goes_on": True,
            "champion": None,
        }

    def test_a_missing_candidate_has_no_window(self, tmp_path: Path) -> None:
        """Beta never ran: window null, one FAIL, and it does not go on."""
        evidence = _write(tmp_path, _ALPHA, days=TRAIN_DAYS)
        outcome = judge_stage(candidate_batch(), "train", evidence, _LOG)

        record = verdict_record(outcome, outcome.verdicts[1])

        assert (record["window"], record["checks"], record["goes_on"]) == (
            None,
            [{"check": MISSING_EVIDENCE, "status": "FAIL"}],
            False,
        )

    def test_validation_records_mark_only_the_champion(self, tmp_path: Path) -> None:
        """At Validation goes_on is null and champion is true for beta alone."""
        evidence = _write(tmp_path, _ALPHA, _BETA)
        judge = _StubJudge({_ALPHA: (True, 0.97), _BETA: (True, 0.99)})
        outcome = judge_stage(candidate_batch(), "validation", evidence, _LOG, judge=judge)

        flags = [
            (r["strategy_id"], r["goes_on"], r["champion"])
            for r in (verdict_record(outcome, v) for v in outcome.verdicts)
        ]

        assert flags == [(_ALPHA, None, False), (_BETA, None, True)]

    def test_the_only_numbers_in_a_record_are_run_id_and_trial_lines(self, tmp_path: Path) -> None:
        """Contract §5: a real gate's verdict carries no metric value, at any depth."""
        evidence = _write(tmp_path, _ALPHA, _BETA)
        outcome = judge_stage(candidate_batch(), "validation", evidence, _LOG)

        numeric: set[str] = set()
        for verdict in outcome.verdicts:
            _collect_numbers(verdict_record(outcome, verdict), "", numeric)

        assert numeric == {"trial_lines"}


def _collect_numbers(value: object, key: str, out: set[str]) -> None:
    """Add the key of every int or float (not bool) under ``value`` to ``out``."""
    if isinstance(value, dict):
        for k, v in value.items():
            _collect_numbers(v, str(k), out)
    elif isinstance(value, list):
        for item in value:
            _collect_numbers(item, key, out)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        out.add(key)


class TestSummary:
    """The stage's summary file names who goes on, or the survivors and champion."""

    def test_the_train_summary_lists_who_goes_on(self, tmp_path: Path) -> None:
        """validation-candidates.json after Train (contract §4)."""
        evidence = _write(tmp_path, _ALPHA, _BETA, days=TRAIN_DAYS)
        judge = _StubJudge({_ALPHA: (False, 0.5), _BETA: (True, 0.99)})
        outcome = judge_stage(candidate_batch(), "train", evidence, _LOG, judge=judge)

        assert stage_summary(outcome) == {"batch_id": "b1", "stage": "train", "candidates": [_BETA]}

    def test_the_validation_summary_names_survivors_and_champion(self, tmp_path: Path) -> None:
        """selection.json after Validation (contract §4)."""
        evidence = _write(tmp_path, _ALPHA, _BETA)
        judge = _StubJudge({_ALPHA: (True, 0.97), _BETA: (True, 0.99)})
        outcome = judge_stage(candidate_batch(), "validation", evidence, _LOG, judge=judge)

        assert stage_summary(outcome) == {
            "batch_id": "b1",
            "stage": "validation",
            "survivors": [_BETA, _ALPHA],
            "champion": _BETA,
        }

    def test_no_champion_is_null(self, tmp_path: Path) -> None:
        """No survivor: champion null, survivors empty."""
        evidence = _write(tmp_path, _ALPHA, _BETA)
        judge = _StubJudge({_ALPHA: (False, 0.97), _BETA: (False, 0.99)})
        outcome = judge_stage(candidate_batch(), "validation", evidence, _LOG, judge=judge)

        assert stage_summary(outcome) == {
            "batch_id": "b1",
            "stage": "validation",
            "survivors": [],
            "champion": None,
        }
