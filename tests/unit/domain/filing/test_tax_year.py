"""Tests for the tax-year calendar (ADR-016 item 2: Madrid calendar year)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from hermes.domain.filing.tax_year import tax_year_bounds, tax_year_of


def test_late_new_years_eve_utc_fill_counts_in_the_next_year() -> None:
    """Invariant: 23:30 UTC on 31 December is 00:30 Madrid, so the next tax year."""
    assert tax_year_of(datetime(2026, 12, 31, 23, 30, tzinfo=UTC)) == 2027


def test_fill_before_madrid_midnight_stays_in_the_year() -> None:
    """Invariant: 22:59 UTC on 31 December is still 31 December in Madrid."""
    assert tax_year_of(datetime(2026, 12, 31, 22, 59, tzinfo=UTC)) == 2026


def test_bounds_are_madrid_midnights_in_utc() -> None:
    """Invariant: the year runs from 1 Jan 00:00 Madrid (23:00 UTC the day before)."""
    assert tax_year_bounds(2026) == (
        datetime(2025, 12, 31, 23, 0, tzinfo=UTC),
        datetime(2026, 12, 31, 23, 0, tzinfo=UTC),
    )


def test_bounds_agree_with_tax_year_of_at_both_edges() -> None:
    """Invariant: start belongs to the year, the instant before it does not."""
    start, end = tax_year_bounds(2026)
    edge = timedelta(microseconds=1)
    assert (tax_year_of(start - edge), tax_year_of(start), tax_year_of(end - edge)) == (
        2025,
        2026,
        2026,
    )


def test_offset_aware_non_utc_input_is_converted() -> None:
    """Invariant: any aware instant works, not only UTC ones."""
    plus_ten = timezone(timedelta(hours=10))
    assert tax_year_of(datetime(2027, 1, 1, 8, 0, tzinfo=plus_ten)) == 2026


def test_naive_datetime_is_rejected() -> None:
    """Invariant: a naive time has no tax year."""
    with pytest.raises(ValueError, match="naive"):
        tax_year_of(datetime(2026, 6, 1))
