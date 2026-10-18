"""Tests for the Madrid zone and the UTC check (`hermes.domain.value_objects.time`).

Invariants (issue #1180, code-conventions §9): every instant inside hermes is
UTC, so the one UTC check refuses naive times and every zone but UTC; and
``MADRID`` is the IANA Europe/Madrid zone, CET in winter and CEST in summer.
"""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from hermes.domain.value_objects.time import MADRID, require_utc


def test_madrid_is_one_hour_ahead_of_utc_in_winter() -> None:
    """15 Jan 2026 is CET, UTC+1."""
    assert datetime(2026, 1, 15, 12, tzinfo=MADRID).utcoffset() == timedelta(hours=1)


def test_madrid_is_two_hours_ahead_of_utc_in_summer() -> None:
    """15 Jul 2026 is CEST, UTC+2."""
    assert datetime(2026, 7, 15, 12, tzinfo=MADRID).utcoffset() == timedelta(hours=2)


@pytest.mark.parametrize("zone", [UTC, UTC, ZoneInfo("UTC"), ZoneInfo("Etc/UTC")])
def test_every_spelling_of_utc_is_accepted(zone: timezone | ZoneInfo) -> None:
    """The standard library has four ways to say UTC; all four pass."""
    require_utc(datetime(2026, 7, 15, 12, tzinfo=zone), name="now")


def test_a_naive_time_is_refused_and_named() -> None:
    """A naive time is a bug at the call site, not a UTC guess."""
    naive = datetime(2026, 7, 15, 12)
    with pytest.raises(
        ValueError,
        match=(
            r"^now must be UTC-aware \(a timezone-aware UTC datetime\), "
            r"got datetime\.datetime\(2026, 7, 15, 12, 0\)$"
        ),
    ):
        require_utc(naive, name="now")


def test_a_madrid_time_is_refused() -> None:
    """Two of the three old checks let a Madrid-aware time through; the one check does not."""
    with pytest.raises(ValueError, match="entry_time must be UTC-aware"):
        require_utc(datetime(2026, 7, 15, 14, tzinfo=MADRID), name="entry_time")


def test_a_fixed_offset_other_than_zero_is_refused() -> None:
    """A fixed +01:00 offset is not UTC."""
    with pytest.raises(ValueError, match="must be UTC-aware"):
        require_utc(datetime(2026, 7, 15, 12, tzinfo=timezone(timedelta(hours=1))), name="at")


def test_london_in_winter_is_refused_even_at_offset_zero() -> None:
    """London is UTC+0 on 15 Jan, but the zone is not UTC, so its next summer time would drift."""
    with pytest.raises(ValueError, match="must be UTC-aware"):
        require_utc(datetime(2026, 1, 15, 12, tzinfo=ZoneInfo("Europe/London")), name="at")
