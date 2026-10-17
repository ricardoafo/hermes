"""Unit tests for judging a research batch at its end (ADR-026 §4, §5; issue #1059).

The rule, signed with the batch: a candidate is eligible when it passes every
gate row, the DSR row judged with the batch-wide trial count included.
Benjamini-Hochberg at q runs over every candidate in the batch with
p = 1 - DSR (no DSR counts as p = 1). The survivors are the eligible candidates
BH rejects, ranked by DSR, then by lowest mean daily-return correlation with
the other survivors, then by strategy id. The champion is the first survivor,
or none. Every expected value is worked from that rule by hand.
"""

import math
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.calibration.batch_judge import (
    BatchJudgement,
    CandidateResult,
    SelectionRule,
    batch_trial_summary,
    judge_batch,
)
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord, TrialSummary
from hermes.domain.market_data.dataset_manifest import DataSplit

_RULE = SelectionRule(fdr_q=0.05)
_FLAT = (0.0, 0.0, 0.0, 0.0)


def _candidate(
    strategy_id: str,
    dsr: float | None,
    *,
    gate_passed: bool = True,
    returns: tuple[float, ...] = _FLAT,
) -> CandidateResult:
    return CandidateResult(
        strategy_id=strategy_id, gate_passed=gate_passed, dsr=dsr, daily_returns=returns
    )


def _judge(*candidates: CandidateResult, rule: SelectionRule = _RULE) -> BatchJudgement:
    return judge_batch(candidates, rule)


class TestChampion:
    """The champion is the best survivor of the signed rule, or none."""

    # SEV-1: the champion is the strategy that goes to shadow (ADR-026 §5)
    def test_the_only_survivor_is_the_champion(self) -> None:
        """With m = 2, p = (0.01, 0.5): 0.01 <= 0.025 survives BH, 0.5 > 0.05 does not."""
        judgement = _judge(_candidate("a", 0.99), _candidate("b", 0.5))
        assert (judgement.survivors, judgement.champion) == (("a",), "a")

    # SEV-1: an eligible candidate BH removes must not reach shadow (ADR-026 §5.2)
    def test_bh_removes_an_eligible_candidate_whose_p_misses_its_line(self) -> None:
        """With m = 2, p = (0.04, 0.5): 0.04 > 0.025 and 0.5 > 0.05, so no survivor."""
        judgement = _judge(_candidate("a", 0.96), _candidate("b", 0.5))
        assert (judgement.survivors, judgement.champion) == ((), None)

    # SEV-1: a candidate that fails a gate row must not reach shadow (ADR-026 §5.1)
    def test_a_failed_gate_row_excludes_the_highest_dsr(self) -> None:
        """With m = 2, p = (0.001, 0.01): BH rejects both, but a failed its gate, so b wins."""
        judgement = _judge(_candidate("a", 0.999, gate_passed=False), _candidate("b", 0.99))
        assert (judgement.survivors, judgement.champion) == (("b",), "b")

    def test_a_p_value_exactly_on_the_bh_line_survives(self) -> None:
        """With m = 1: DSR 0.95 gives p = 0.05, on the line q = 0.05 (in floats 1 - 0.95 > 0.05)."""
        assert _judge(_candidate("a", 0.95)).champion == "a"

    def test_a_p_value_just_over_the_bh_line_does_not_survive(self) -> None:
        """With m = 1: DSR 0.9499 gives p = 0.0501 > 0.05, so there is no champion."""
        judgement = _judge(_candidate("a", 0.9499))
        assert (judgement.survivors, judgement.champion) == ((), None)

    def test_a_missing_dsr_is_never_eligible(self) -> None:
        """A DSR that could not be computed fails closed (p = 1)."""
        judgement = _judge(_candidate("a", None), _candidate("b", 0.999))
        assert (judgement.eligible, judgement.champion) == (("b",), "b")

    def test_a_missing_dsr_still_counts_in_m(self) -> None:
        """With m = 2 and p = (1, 0.04): 0.04 > 0.025, so a missing DSR still raises the bar."""
        judgement = _judge(_candidate("a", None), _candidate("b", 0.96))
        assert (judgement.survivors, judgement.champion) == ((), None)


class TestRanking:
    """Survivors are ranked by DSR, then lowest mean return correlation, then id."""

    def test_survivors_rank_by_dsr_highest_first(self) -> None:
        """With m = 2, p = (0.01, 0.001): both under their lines; b has the higher DSR."""
        judgement = _judge(_candidate("a", 0.99), _candidate("b", 0.999))
        assert judgement.survivors == ("b", "a")

    def test_a_dsr_tie_goes_to_the_lowest_mean_correlation(self) -> None:
        """Candidates a and b move together (correlation 1); c moves against both.

        Centred, a = b = (-1.5, -0.5, 0.5, 1.5) and c = (1, -1, 1, -1), so
        corr(a, c) = corr(b, c) = -2 / (2 * sqrt(5)) = -0.447. Mean correlation:
        c = -0.447, a = b = (1 - 0.447) / 2 = 0.276. c ranks first; a and b tie
        again and fall back to the id.
        """
        rising = (1.0, 2.0, 3.0, 4.0)
        judgement = _judge(
            _candidate("b", 0.99, returns=rising),
            _candidate("a", 0.99, returns=rising),
            _candidate("c", 0.99, returns=(1.0, -1.0, 1.0, -1.0)),
        )
        assert judgement.survivors == ("c", "a", "b")

    def test_flat_returns_count_as_fully_correlated_in_a_tie(self) -> None:
        """Correlation with a flat series is undefined, so it counts as 1, the worst.

        a is flat: its correlations count as 1, mean 1. b and c: corr(b, c) = -0.447
        (as above) and 1 with a, mean 0.276 each. b and c rank first by id, a last.
        """
        judgement = _judge(
            _candidate("a", 0.99, returns=_FLAT),
            _candidate("b", 0.99, returns=(1.0, 2.0, 3.0, 4.0)),
            _candidate("c", 0.99, returns=(1.0, -1.0, 1.0, -1.0)),
        )
        assert judgement.survivors == ("b", "c", "a")


class TestEligible:
    """After Train, the eligible candidates go on to Validation (ADR-026 §3)."""

    def test_eligible_lists_every_candidate_whose_gate_passed_in_rule_order(self) -> None:
        """Candidate a fails the gate, c has no DSR; b and d pass, d with the higher DSR."""
        judgement = _judge(
            _candidate("a", 0.99, gate_passed=False),
            _candidate("b", 0.96),
            _candidate("c", None),
            _candidate("d", 0.98),
        )
        assert judgement.eligible == ("d", "b")


def _trial(
    strategy_id: str, split: DataSplit = DataSplit.TRAIN, sharpe: float | None = None
) -> TrialRecord:
    return TrialRecord(
        recorded_at=datetime(2026, 10, 17, 12, tzinfo=UTC),
        strategy_id=strategy_id,
        parameters={},
        split=split,
        window_start=datetime(2021, 1, 1, tzinfo=UTC),
        window_end=datetime(2021, 2, 1, tzinfo=UTC),
        datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
        metrics={},
        sharpe=sharpe,
        git_sha="c" * 40,
        git_dirty=False,
    )


class TestBatchTrialSummary:
    """Every candidate's DSR is deflated by the trials of the whole log (ADR-026 §4)."""

    def test_every_strategy_and_split_in_the_log_counts(self) -> None:
        """Two v1 lines, one v2 Validation line and two candidate lines make N = 5.

        Sharpe ratios 1, 2, 3 over every strategy: sample variance
        ((1-2)^2 + 0 + (3-2)^2) / 2 = 1.
        """
        log = [
            _trial("swing-mvp1", sharpe=1.0),
            _trial("swing-mvp1"),
            _trial("swing-mvp1-v2", DataSplit.VALIDATION, sharpe=2.0),
            _trial("swing-b1-a", sharpe=3.0),
            _trial("swing-b1-b"),
        ]
        assert batch_trial_summary(log) == TrialSummary(
            count=5, validation_count=1, sharpe_variance=1.0
        )


class TestRefusals:
    """A malformed batch or rule is refused, never judged."""

    def test_an_empty_batch_is_refused(self) -> None:
        """A batch with no candidate has nothing to judge."""
        with pytest.raises(ValueError, match="at least one candidate"):
            judge_batch((), _RULE)

    def test_a_repeated_strategy_id_is_refused(self) -> None:
        """Two results for one strategy would count it twice in m."""
        with pytest.raises(ValueError, match="more than once"):
            _judge(_candidate("a", 0.99), _candidate("a", 0.5))

    def test_return_series_of_different_lengths_are_refused(self) -> None:
        """Candidates are judged over one window, so their daily series align."""
        with pytest.raises(ValueError, match="same number of daily returns"):
            _judge(_candidate("a", 0.99), _candidate("b", 0.99, returns=(0.0, 0.0)))

    @pytest.mark.parametrize("dsr", [-0.01, 1.01, math.nan])
    def test_a_dsr_outside_the_unit_interval_is_refused(self, dsr: float) -> None:
        """A DSR is a probability."""
        with pytest.raises(ValueError, match="dsr must be"):
            _candidate("a", dsr)

    @pytest.mark.parametrize("q", [0.0, 1.0, math.nan])
    def test_a_q_outside_the_open_unit_interval_is_refused(self, q: float) -> None:
        """BH needs 0 < q < 1."""
        with pytest.raises(ValueError, match="fdr_q"):
            SelectionRule(fdr_q=q)


_RETURNS = st.lists(
    st.floats(min_value=-0.1, max_value=0.1, allow_nan=False), min_size=4, max_size=4
).map(tuple)
_CANDIDATES = st.lists(
    st.builds(
        _candidate,
        st.sampled_from("abcdef"),
        st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0)),
        gate_passed=st.booleans(),
        returns=_RETURNS,
    ),
    min_size=1,
    max_size=6,
    unique_by=lambda c: c.strategy_id,
)


class TestProperties:
    """Invariants that hold for every batch."""

    @given(
        _CANDIDATES.flatmap(lambda cs: st.tuples(st.just(cs), st.permutations(cs))),
    )
    def test_the_order_candidates_ran_in_does_not_change_the_judgement(
        self, batch: tuple[list[CandidateResult], list[CandidateResult]]
    ) -> None:
        """Run order cannot help or hurt a candidate (ADR-026 §4)."""
        ran, reordered = batch
        assert judge_batch(reordered, _RULE) == judge_batch(ran, _RULE)

    @given(_CANDIDATES)
    def test_every_survivor_is_eligible(self, candidates: list[CandidateResult]) -> None:
        """BH never adds a candidate the gate turned away."""
        judgement = judge_batch(candidates, _RULE)
        assert set(judgement.survivors) <= set(judgement.eligible)

    @given(_CANDIDATES)
    def test_no_survivor_has_a_higher_dsr_than_the_champion(
        self, candidates: list[CandidateResult]
    ) -> None:
        """The champion has the highest DSR among the survivors."""
        judgement = judge_batch(candidates, _RULE)
        dsr = {c.strategy_id: c.dsr or 0.0 for c in candidates}
        champion = judgement.champion
        if champion is not None:
            assert [s for s in judgement.survivors if dsr[s] > dsr[champion]] == []
