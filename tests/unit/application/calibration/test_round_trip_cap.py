"""Tests for the ADR-029 §4 round-trip cap: at most 250 round trips in any rolling 90 days.

Invariants (ADR-029 §4, issue #1240, openspec/changes/1240-round-trip-cap/change.md):

- A candidate's Train run fails the row when any 90-day window holds more
  than the signed cap; exactly the cap passes.
- The window is closed at both ends, as the ADR-002 tax guard counts it: two
  round trips opened exactly 90 days apart share a window, one second more
  and they do not.
- Only round trips opened on or before ``train_end`` count: the row judges
  the Train run.
- Evidence without round-trip times fails the row; it is never a pass.

Every timestamp is made up; no market data or trial log is read.
"""

from datetime import UTC, date, datetime, timedelta

from hypothesis import example, given
from hypothesis import strategies as st

from hermes.application.calibration.round_trip_cap import (
    ROUND_TRIP_CAP_CHECK,
    busiest_window,
    round_trip_cap_row,
)

_T0 = datetime(2021, 3, 1, 12, 0, tzinfo=UTC)
_TRAIN_END = date(2021, 12, 31)
_NINETY_DAYS = timedelta(days=90)
_CAP = 250


def _spread(count: int, start: datetime = _T0) -> list[datetime]:
    """``count`` round trips one hour apart: all inside one 90-day window."""
    return [start + timedelta(hours=i) for i in range(count)]


def test_exactly_250_round_trips_in_one_window_pass_the_row() -> None:
    """ADR-029 §4 fails a window holding *more than* 250; 250 itself passes."""
    row = round_trip_cap_row(_spread(250), cap=_CAP, train_end=_TRAIN_END)

    assert (row.name, row.observed, row.passed) == (ROUND_TRIP_CAP_CHECK, "250", True)


def test_251_round_trips_in_one_window_fail_the_row() -> None:
    """ADR-029 §4: one round trip past the cap in a 90-day window fails Train."""
    row = round_trip_cap_row(_spread(251), cap=_CAP, train_end=_TRAIN_END)

    assert (row.observed, row.passed) == ("251", False)


def test_a_round_trip_exactly_90_days_after_the_first_shares_its_window() -> None:
    """The window is closed at both ends, as the ADR-002 guard's: 250 + 1 at +90 days is 251."""
    trips = [*_spread(250), _T0 + _NINETY_DAYS]

    row = round_trip_cap_row(trips, cap=_CAP, train_end=_TRAIN_END)

    assert (row.observed, row.passed) == ("251", False)


def test_a_round_trip_just_over_90_days_after_the_first_starts_another_window() -> None:
    """A trip one second past 90 days after the first is outside that first trip's window.

    The 250 trips run one hour apart from _T0, so the late trip's window
    [_T0 + 1s, _T0 + 90d + 1s] holds 249 of them plus itself: 250, a pass.
    """
    trips = [*_spread(250), _T0 + _NINETY_DAYS + timedelta(seconds=1)]

    row = round_trip_cap_row(trips, cap=_CAP, train_end=_TRAIN_END)

    assert (row.observed, row.passed) == ("250", True)


def test_round_trips_opened_after_train_end_are_not_counted() -> None:
    """The row judges the Train run: a Validation stretch's trips never move it."""
    after_train = datetime(2022, 1, 1, tzinfo=UTC)
    trips = [*_spread(10), *_spread(300, after_train)]

    row = round_trip_cap_row(trips, cap=_CAP, train_end=_TRAIN_END)

    assert (row.observed, row.passed) == ("10", True)


def test_a_round_trip_opened_on_train_end_is_counted() -> None:
    """``train_end`` is the last Train day, so a trip opened late that day is a Train trip."""
    last_day = datetime(2021, 12, 31, 23, 0, tzinfo=UTC)

    row = round_trip_cap_row(
        _spread(251, last_day - timedelta(hours=250)), cap=_CAP, train_end=_TRAIN_END
    )

    assert (row.observed, row.passed) == ("251", False)


def test_evidence_without_round_trip_times_fails_the_row() -> None:
    """Fail closed: a run that did not record when its trips opened cannot pass the cap."""
    row = round_trip_cap_row(None, cap=_CAP, train_end=_TRAIN_END)

    assert (row.observed, row.passed) == ("not computed", False)


def test_the_requirement_names_the_signed_cap() -> None:
    """The row states the number the candidate signed, not a constant."""
    row = round_trip_cap_row([], cap=120, train_end=_TRAIN_END)

    assert row.requirement == "<= 120 round trips in any 90 days of Train"


def test_no_round_trips_is_a_busiest_window_of_zero() -> None:
    """A run that never traded holds no round trip in any window."""
    assert busiest_window([], _NINETY_DAYS) == 0


_OFFSETS = st.lists(st.integers(min_value=0, max_value=400 * 24), max_size=60)
"""Round-trip opening times as hours after _T0, unsorted and with repeats."""


def _brute_force(times: list[datetime], window: timedelta) -> int:
    """Count the busiest window from every trip as its start, where some busiest window starts."""
    return max((sum(1 for t in times if s <= t <= s + window) for s in times), default=0)


@given(hours=_OFFSETS)
@example(hours=[0, 90 * 24])
@example(hours=[90 * 24, 0, 0])
def test_the_busiest_window_equals_a_count_from_every_start(hours: list[int]) -> None:
    """The busiest 90-day window is the most trips any one closed window holds."""
    times = [_T0 + timedelta(hours=h) for h in hours]

    assert busiest_window(times, _NINETY_DAYS) == _brute_force(times, _NINETY_DAYS)


@given(
    hours=_OFFSETS,
    extra=st.integers(min_value=0, max_value=400 * 24),
    cap=st.integers(min_value=1, max_value=40),
)
def test_adding_a_round_trip_never_turns_a_fail_into_a_pass(
    hours: list[int], extra: int, cap: int
) -> None:
    """ADR-029 §4 is a ceiling: trading more can never bring a run back under it."""
    times = [_T0 + timedelta(hours=h) for h in hours]
    before = round_trip_cap_row(times, cap=cap, train_end=_TRAIN_END)
    after = round_trip_cap_row(
        [*times, _T0 + timedelta(hours=extra)], cap=cap, train_end=_TRAIN_END
    )

    assert (before.passed, after.passed) != (False, True)
