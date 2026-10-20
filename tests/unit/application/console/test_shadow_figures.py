"""The shadow test line from the capital seed and the fill count (change live-home-screen D-4).

Invariants:

- no seed is "not started" and says why;
- the seed's local day is day 1; days count by local calendar day;
- past day 90 the test is finished and stays at day 90;
- a run under way is "running": the ledger cannot say whether it is on track,
  so the note says the verdict is not available, with the fill count.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.shadow_figures import SHADOW_TEST_DAYS, build_shadow_test
from hermes.application.console.views import ShadowTest, ShadowTestStatus

_MADRID = ZoneInfo("Europe/Madrid")
_SEEDED = datetime(2026, 10, 1, 7, tzinfo=UTC)


def _test(as_of: datetime, *, fills: int = 2, checked: int = 0) -> ShadowTest:
    return build_shadow_test(
        seeded_at=_SEEDED, as_of=as_of, tz=_MADRID, fills=fills, quality_checked=checked
    )


def test_no_seed_is_not_started() -> None:
    """Without a capital seed the run has not started and the note names the missing seed."""
    test = build_shadow_test(seeded_at=None, as_of=_SEEDED, tz=_MADRID, fills=0)
    assert (test.status, test.day, test.note) == (
        ShadowTestStatus.NOT_STARTED,
        None,
        "no capital seed in the ledger yet",
    )


def test_seed_day_is_day_one_and_the_verdict_is_not_guessed() -> None:
    """On the seed's local day the run is on day 1, running, with the fill count."""
    test = _test(_SEEDED + timedelta(hours=10))
    assert (test.status, test.day, test.length_days, test.note) == (
        ShadowTestStatus.RUNNING,
        1,
        90,
        "2 fills · verdict not available yet",
    )


def test_days_follow_the_local_calendar() -> None:
    """23:30 UTC on 1 October is already 2 October in Madrid: day 2."""
    assert _test(datetime(2026, 10, 1, 23, 30, tzinfo=UTC)).day == 2


def test_after_ninety_days_the_test_is_finished() -> None:
    """Day 91 reads finished at day 90 of 90."""
    test = _test(_SEEDED + timedelta(days=90))
    assert (test.status, test.day) == (ShadowTestStatus.FINISHED, 90)


def test_a_seed_dated_later_is_not_started_yet() -> None:
    """A read before the seed's day does not count negative days."""
    test = _test(_SEEDED - timedelta(days=2))
    assert (test.status, test.day, test.note) == (
        ShadowTestStatus.NOT_STARTED,
        None,
        "starts 01 Oct",
    )


def test_one_fill_and_quality_checks_are_named() -> None:
    """The note says "1 fill" in the singular and counts fill-quality records when present."""
    assert _test(_SEEDED, fills=1, checked=1).note == (
        "1 fill (1 quality-checked) · verdict not available yet"
    )


@given(days=st.integers(min_value=0, max_value=400))
def test_day_never_leaves_one_to_ninety(days: int) -> None:
    """For any read time on or after the seed, the day is within 1..90."""
    test = _test(_SEEDED + timedelta(days=days))
    assert test.day is not None
    assert 1 <= test.day <= SHADOW_TEST_DAYS
