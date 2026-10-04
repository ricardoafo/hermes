"""Tests for ReconciliationMonth (change 35-monthly-reconciliation D-3).

Invariants protected here:

- month bounds are Madrid midnights expressed in UTC, half-open;
- a fill late on the last UTC day can belong to the next Madrid month;
- consecutive months tile time with no gap or overlap;
- ``parse`` accepts only ``YYYY-MM`` naming a real month.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.reconciliation.month import ReconciliationMonth


def test_start_is_madrid_midnight_in_utc_during_summer_time() -> None:
    """October 1 00:00 Madrid (CEST, UTC+2) is September 30 22:00 UTC."""
    assert ReconciliationMonth(2026, 10).start == datetime(2026, 9, 30, 22, tzinfo=UTC)


def test_end_is_next_month_start_across_the_dst_change() -> None:
    """November 1 00:00 Madrid (CET, UTC+1) is October 31 23:00 UTC."""
    assert ReconciliationMonth(2026, 10).end == datetime(2026, 10, 31, 23, tzinfo=UTC)


def test_a_fill_late_on_the_last_utc_day_belongs_to_the_next_month() -> None:
    """23:30 UTC on 30 September is already 1 October in Madrid (ADR-016 item 2)."""
    instant = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)

    assert ReconciliationMonth.containing(instant) == ReconciliationMonth(2026, 10)


def test_containing_rejects_a_naive_datetime() -> None:
    """A naive instant has no Madrid month; guessing would misfile a fill."""
    with pytest.raises(ValueError, match="aware"):
        ReconciliationMonth.containing(datetime(2026, 9, 1))


def test_previous_of_january_is_december_of_the_year_before() -> None:
    """The year rolls back with the month."""
    assert ReconciliationMonth(2027, 1).previous() == ReconciliationMonth(2026, 12)


def test_following_of_december_is_january_of_the_next_year() -> None:
    """The year rolls forward with the month."""
    assert ReconciliationMonth(2026, 12).following() == ReconciliationMonth(2027, 1)


def test_parse_reads_year_and_month() -> None:
    """``2026-09`` is September 2026 and prints back the same."""
    month = ReconciliationMonth.parse("2026-09")

    assert (month, str(month)) == (ReconciliationMonth(2026, 9), "2026-09")


@pytest.mark.parametrize("text", ["2026-13", "2026-00", "2026-9", "26-09", "september", ""])
def test_parse_rejects_anything_but_a_real_yyyy_mm(text: str) -> None:
    """A malformed or impossible month is refused, never coerced."""
    with pytest.raises(ValueError, match=r"YYYY-MM|month must be"):
        ReconciliationMonth.parse(text)


@given(year=st.integers(min_value=2000, max_value=2100), month=st.integers(1, 12))
def test_consecutive_months_tile_time(year: int, month: int) -> None:
    """Property: a month ends exactly where the next one starts, and starts before it ends."""
    current = ReconciliationMonth(year, month)

    assert current.end == current.following().start
    assert current.start < current.end
    assert current.following().previous() == current


@given(instant=st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2100, 1, 1)))
def test_containing_month_holds_the_instant(instant: datetime) -> None:
    """Property: the month containing an instant has it within [start, end)."""
    aware = instant.replace(tzinfo=UTC)
    month = ReconciliationMonth.containing(aware)

    assert month.start <= aware < month.end
