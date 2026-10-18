"""Tests for the one Madrid tax calendar both tax surfaces share (issue #1171).

Reference: ADR-016 decision 2 (the tax year is the Madrid calendar year).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest

from hermes.domain.tax.tax_calendar import (
    add_months,
    madrid_date,
    madrid_midnight,
    tax_year_bounds,
    tax_year_of,
)


def test_madrid_date_rolls_over_at_madrid_midnight_in_summer() -> None:
    """22:00 UTC in August is 00:00 CEST the next day in Madrid."""
    assert madrid_date(datetime(2026, 8, 15, 21, 59, 59, tzinfo=UTC)) == date(2026, 8, 15)
    assert madrid_date(datetime(2026, 8, 15, 22, 0, tzinfo=UTC)) == date(2026, 8, 16)


def test_madrid_date_rolls_over_at_madrid_midnight_in_winter() -> None:
    """23:00 UTC in January is 00:00 CET the next day in Madrid."""
    assert madrid_date(datetime(2026, 1, 10, 22, 59, 59, tzinfo=UTC)) == date(2026, 1, 10)
    assert madrid_date(datetime(2026, 1, 10, 23, 0, tzinfo=UTC)) == date(2026, 1, 11)


def test_madrid_midnight_is_the_utc_instant_of_the_day_start() -> None:
    """Madrid midnight is 22:00 UTC the day before in summer, 23:00 in winter."""
    assert madrid_midnight(date(2026, 8, 16)) == datetime(2026, 8, 15, 22, 0, tzinfo=UTC)
    assert madrid_midnight(date(2026, 1, 1)) == datetime(2025, 12, 31, 23, 0, tzinfo=UTC)


def test_a_sale_late_on_new_years_eve_utc_is_in_the_next_tax_year() -> None:
    """23:30 UTC on 31 Dec is 00:30 on 1 Jan in Madrid, so the next tax year."""
    assert tax_year_of(datetime(2025, 12, 31, 22, 59, 59, tzinfo=UTC)) == 2025
    assert tax_year_of(datetime(2025, 12, 31, 23, 30, tzinfo=UTC)) == 2026


def test_tax_year_bounds_are_madrid_midnights() -> None:
    """The year runs from Madrid midnight on 1 Jan to the next one."""
    assert tax_year_bounds(2026) == (
        datetime(2025, 12, 31, 23, 0, tzinfo=UTC),
        datetime(2026, 12, 31, 23, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize("call", [madrid_date, tax_year_of])
def test_naive_times_are_rejected(call: Callable[[datetime], object]) -> None:
    """A naive time has no Madrid date; guessing one would misfile a sale."""
    with pytest.raises(ValueError, match="naive"):
        call(datetime(2026, 6, 1, 12, 0))


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2026, 6, 15), 2, date(2026, 8, 15)),
        (date(2026, 6, 15), -2, date(2026, 4, 15)),
        (date(2026, 12, 31), 2, date(2027, 2, 28)),
        (date(2026, 4, 30), -2, date(2026, 2, 28)),
        (date(2027, 12, 31), 2, date(2028, 2, 29)),
        (date(2026, 1, 15), -2, date(2025, 11, 15)),
    ],
)
def test_add_months_moves_calendar_months_and_clamps_the_day(
    day: date, months: int, expected: date
) -> None:
    """Two months on keeps the day, clamped to the target month's last day."""
    assert add_months(day, months) == expected
