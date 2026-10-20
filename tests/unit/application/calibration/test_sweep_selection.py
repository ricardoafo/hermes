"""Tests for the sweep's Validation pick and the batch its champion rule judges (ADR-031 §3, §4).

Verdicts are built by hand from the synthetic sweep (``tests.support.research_sweep``);
the DSR values are made up. The judge that produces real verdicts has its own tests
in ``test_batch_stage.py``; the null check runs both together.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.calibration.batch_stage import CandidateVerdict, StageOutcome
from hermes.application.calibration.research_batch import Candidate
from hermes.application.calibration.research_sweep import parse_sweep
from hermes.application.calibration.sweep_selection import family_winners, validation_batch
from tests.support.research_batch import BATCH_PATH, batch_text
from tests.support.research_sweep import SWEEP_IDS, sweep_front_matter

_SWEEP = parse_sweep(BATCH_PATH, batch_text(sweep_front_matter()))
_CANDIDATES = {c.strategy_id: c for c in _SWEEP.batch.candidates}


def _verdict(candidate: Candidate, *, passed: bool, dsr: float | None) -> CandidateVerdict:
    return CandidateVerdict(
        candidate=candidate,
        checks=(),
        passed=passed,
        dsr=dsr,
        window=None,
        returns=(),
        trial_lines=0,
    )


def _train(results: dict[str, tuple[bool, float | None]]) -> StageOutcome:
    verdicts = tuple(
        _verdict(
            _CANDIDATES[sid],
            passed=results.get(sid, (False, None))[0],
            dsr=results.get(sid, (False, None))[1],
        )
        for sid in SWEEP_IDS
    )
    return StageOutcome("b1", "train", verdicts, (), ())


def test_each_family_sends_its_passer_with_the_highest_train_dsr() -> None:
    """ADR-031 §3: at most one candidate per family, the strongest on Train."""
    outcome = _train(
        {
            SWEEP_IDS[1]: (True, 0.96),
            SWEEP_IDS[4]: (True, 0.99),
            SWEEP_IDS[7]: (True, 0.97),
        }
    )

    assert family_winners(outcome) == (SWEEP_IDS[4], SWEEP_IDS[7])


def test_a_candidate_that_failed_train_is_never_sent_whatever_its_dsr() -> None:
    """Only candidates that pass every Train-judgeable check go on (ADR-031 §3)."""
    outcome = _train({SWEEP_IDS[0]: (False, 0.999), SWEEP_IDS[2]: (True, 0.96)})

    assert family_winners(outcome) == (SWEEP_IDS[2],)


def test_a_family_with_no_passer_sends_nothing() -> None:
    """No passer means no Validation touch, so the family's budget is not spent."""
    assert family_winners(_train({})) == ()


def test_a_tie_keeps_the_first_candidate_in_expansion_order() -> None:
    """The pick depends only on the verdicts and the signed order."""
    outcome = _train({SWEEP_IDS[3]: (True, 0.97), SWEEP_IDS[1]: (True, 0.97)})

    assert family_winners(outcome) == (SWEEP_IDS[1],)


def test_winners_come_only_from_a_train_outcome() -> None:
    """A Validation outcome has no Train ranking to pick from."""
    outcome = StageOutcome("b1", "validation", (), (), ())

    with pytest.raises(ValueError, match="from Train"):
        family_winners(outcome)


def test_the_validation_batch_holds_only_the_winners_in_batch_order() -> None:
    """ADR-031 §4: BH's m is the count of candidates sent to Validation."""
    judged = validation_batch(_SWEEP.batch, (SWEEP_IDS[7], SWEEP_IDS[2]))

    assert tuple(c.strategy_id for c in judged.candidates) == (SWEEP_IDS[2], SWEEP_IDS[7])


def test_a_winner_outside_the_batch_is_refused() -> None:
    """A typo must not shrink m silently."""
    with pytest.raises(ValueError, match="not candidates"):
        validation_batch(_SWEEP.batch, ("pullback@lookback=99",))


@given(
    st.dictionaries(
        st.sampled_from(SWEEP_IDS),
        st.tuples(st.booleans(), st.floats(min_value=0.0, max_value=1.0)),
    )
)
def test_winners_are_one_passer_per_family_and_none_beats_them_in_their_family(
    results: dict[str, tuple[bool, float]],
) -> None:
    """For any verdicts: one id per family that passed, none outranked by a passer of its family."""
    winners = family_winners(_train(dict(results)))

    families = [_CANDIDATES[w].idea_family for w in winners]
    beaten = [
        w
        for w in winners
        for sid, (passed, dsr) in results.items()
        if passed
        and _CANDIDATES[sid].idea_family == _CANDIDATES[w].idea_family
        and dsr > results[w][1]
    ]
    assert (len(families) == len(set(families)), all(results[w][0] for w in winners), beaten) == (
        True,
        True,
        [],
    )
