"""Unit tests for returns over dated periods of a daily equity curve (ADR-021 §5).

Point ``i`` of a curve is the equity at the end of ``start + i`` days. Return
``i`` (``i >= 1``) belongs to day ``start + i``.
"""

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.statistics.period_returns import (
    PeriodReturn,
    annualised_return,
    calendar_year_returns,
    stretch_return,
)


class TestCalendarYearReturns:
    """Each calendar year compounds the returns of its own days."""

    def test_year_boundary_splits_the_curve(self) -> None:
        """30 Dec to 2 Jan: 2020 is 110/100 - 1, 2021 is 108.9/110 - 1."""
        years = calendar_year_returns(date(2020, 12, 30), [100.0, 110.0, 99.0, 108.9])
        assert years == {
            2020: pytest.approx(0.10, abs=1e-12),
            2021: pytest.approx(-0.01, abs=1e-12),
        }

    def test_year_with_only_the_base_point_has_no_return(self) -> None:
        """A curve starting on 31 Dec has no 2020 return day, so 2020 is not a year of it."""
        assert calendar_year_returns(date(2020, 12, 31), [100.0, 105.0]) == {
            2021: pytest.approx(0.05, abs=1e-12)
        }

    def test_single_point_has_no_years(self) -> None:
        """One point is a base with no return."""
        assert calendar_year_returns(date(2021, 1, 1), [100.0]) == {}

    @given(st.lists(st.integers(min_value=50, max_value=150), min_size=2, max_size=800))
    def test_years_compound_to_the_whole_curve(self, points: list[int]) -> None:
        """The product of (1 + year return) equals last / first, whatever the year cuts."""
        equity = [float(p) for p in points]
        product = 1.0
        for value in calendar_year_returns(date(2020, 11, 15), equity).values():
            product *= 1.0 + value
        assert product == pytest.approx(equity[-1] / equity[0], rel=1e-9)


class TestStretchReturn:
    """A dated stretch compounds the returns of the days inside it."""

    def test_stretch_inside_the_curve(self) -> None:
        """Days 2 and 3 of a curve from 1 Jan: 121/110 - 1 over two days."""
        result = stretch_return(
            date(2021, 1, 1), [100.0, 110.0, 99.0, 121.0], date(2021, 1, 3), date(2021, 1, 4)
        )
        assert result == PeriodReturn(total=pytest.approx(0.10, abs=1e-12), days=2)

    def test_stretch_from_the_first_day_uses_the_first_point_as_base(self) -> None:
        """Day 0 has no return, so a stretch from it starts at point 0."""
        result = stretch_return(
            date(2021, 1, 1), [100.0, 110.0, 99.0], date(2021, 1, 1), date(2021, 1, 2)
        )
        assert result == PeriodReturn(total=pytest.approx(0.10, abs=1e-12), days=1)

    @pytest.mark.parametrize(
        ("first", "last"),
        [
            (date(2020, 12, 31), date(2021, 1, 2)),
            (date(2021, 1, 2), date(2021, 1, 4)),
            (date(2021, 1, 3), date(2021, 1, 2)),
            (date(2021, 1, 1), date(2021, 1, 1)),
        ],
    )
    def test_stretch_outside_the_curve_or_without_a_return_day_is_refused(
        self, first: date, last: date
    ) -> None:
        """Before the start, past the end, reversed, or only the base day: no number."""
        with pytest.raises(ValueError, match="stretch"):
            stretch_return(date(2021, 1, 1), [100.0, 110.0, 99.0], first, last)


class TestAnnualisedReturn:
    """Annualising uses 365 days a year (crypto trades every day)."""

    def test_two_years(self) -> None:
        """21% over 730 days is 10% a year."""
        assert annualised_return(PeriodReturn(0.21, 730)) == pytest.approx(0.10, abs=1e-12)

    def test_one_year_is_unchanged(self) -> None:
        """Over exactly 365 days the annualised return is the total."""
        assert annualised_return(PeriodReturn(-0.2, 365)) == pytest.approx(-0.2, abs=1e-12)

    def test_overflowing_rate_is_refused(self) -> None:
        """A 1e10 return over one day annualises past the float range: an error, not inf."""
        with pytest.raises(ValueError, match="overflows"):
            annualised_return(PeriodReturn(1e10, 1))

    def test_zero_days_is_refused(self) -> None:
        """A period with no return day cannot be annualised."""
        with pytest.raises(ValueError, match="at least one day"):
            annualised_return(PeriodReturn(0.0, 0))
