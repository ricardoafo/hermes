"""Tests for a candidate's signed ADR-029 §4 round-trip cap (issue #1240).

Invariants (ADR-029 §4, §5; openspec/changes/1240-round-trip-cap/change.md):

- A candidate holding under a day must sign ``round_trips_90d_max``.
- No cap may be looser than 250; a cap is a positive integer.
- A candidate with neither key signs no cap, as every batch signed before the row.

The ADR-021 §5 floors that moved here with them stay pinned by
``test_research_batch.py``'s floor property.
"""

import pytest

from hermes.application.calibration.batch_floors import (
    RoundTripCapError,
    caps_detail,
    signed_cap,
)

_WHERE = "candidates[0]"


def test_a_candidate_holding_under_a_day_signs_its_cap() -> None:
    """ADR-029 §4: the cap the candidate signed is the one the gate gets."""
    assert (
        signed_cap({"holding": "under_a_day", "round_trips_90d_max": 250}, _WHERE, required=False)
        == 250
    )


def test_a_candidate_with_neither_key_signs_no_cap() -> None:
    """Batches signed before ADR-029 §4 carry neither key and must read as they did."""
    caps = [signed_cap(f, _WHERE, required=False) for f in ({}, {"round_trips_90d_max": 120})]

    assert caps == [None, 120]


def test_a_candidate_holding_a_day_or_more_may_sign_no_cap() -> None:
    """ADR-029 §5 asks the cap only of candidates holding under a day."""
    caps = [
        signed_cap({"holding": "a_day_or_more", **extra}, _WHERE, required=False)
        for extra in ({}, {"round_trips_90d_max": 120})
    ]

    assert caps == [None, 120]


def test_holding_under_a_day_without_a_cap_is_refused() -> None:
    """A candidate that trades within the day cannot leave the tax-line row out."""
    with pytest.raises(RoundTripCapError) as caught:
        signed_cap({"holding": "under_a_day"}, _WHERE, required=False)

    assert str(caught.value) == (
        "candidates[0] holds under a day but signs no round_trips_90d_max (ADR-029 §4)"
    )


def test_a_cap_of_250_is_accepted_and_251_is_refused() -> None:
    """The limit at the limit: 250 is ADR-029 §4's number, 251 loosens it."""
    with pytest.raises(RoundTripCapError) as caught:
        signed_cap({"round_trips_90d_max": 251}, _WHERE, required=False)

    assert (
        signed_cap({"round_trips_90d_max": 250}, _WHERE, required=False),
        str(caught.value),
    ) == (
        250,
        "candidates[0].round_trips_90d_max 251 is looser than ADR-029 §4 (<= 250)",
    )


@pytest.mark.parametrize("cap", [0, -5, True, "250", 250.0])
def test_a_cap_that_is_not_a_positive_integer_is_refused(cap: object) -> None:
    """A cap of 0 fails every run that trades; a string or float is not a signed count."""
    with pytest.raises(RoundTripCapError) as caught:
        signed_cap({"round_trips_90d_max": cap}, _WHERE, required=False)

    assert str(caught.value) == (
        f"candidates[0].round_trips_90d_max must be a positive integer, got {cap!r}"
    )


def test_an_unknown_holding_is_refused() -> None:
    """Only the two ADR-029 §5 holdings exist, so a typo cannot dodge the cap rule."""
    with pytest.raises(RoundTripCapError) as caught:
        signed_cap({"holding": "intraday", "round_trips_90d_max": 250}, _WHERE, required=False)

    assert str(caught.value) == (
        "candidates[0].holding must be one of ['under_a_day', 'a_day_or_more'], got 'intraday'"
    )


def test_the_caps_note_is_empty_when_no_candidate_signs_a_cap() -> None:
    """A batch without caps prints the funnel_floors line exactly as before the row."""
    assert caps_detail([None, None]) == ""


def test_the_caps_note_lists_each_candidate_s_cap_in_order() -> None:
    """The operator sees which candidate signed which cap."""
    assert caps_detail([120, None]) == "; round trips per 90 days at most [120, None]"
