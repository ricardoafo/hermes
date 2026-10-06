"""Unit tests for the §5.5 rolling return correlation (pre-registration §5.5)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.statistics.correlation import CORRELATION_DAYS, pearson, return_correlation

_POSITIVE = st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False)


def _walk(returns: list[float], start: float = 100.0) -> list[float]:
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    return closes


class TestPearson:
    """Pearson coefficient on hand-checked series."""

    def test_identical_series_correlate_perfectly(self) -> None:
        """A series correlates 1 with itself."""
        assert pearson([1.0, 2.0, 4.0], [1.0, 2.0, 4.0]) == pytest.approx(1.0)

    def test_mirrored_series_anti_correlate(self) -> None:
        """A series correlates -1 with its negation."""
        assert pearson([1.0, 2.0, 4.0], [-1.0, -2.0, -4.0]) == pytest.approx(-1.0)

    def test_hand_computed_value(self) -> None:
        """The series 1,2,3 and 1,3,2 correlate 0.5."""
        assert pearson([1.0, 2.0, 3.0], [1.0, 3.0, 2.0]) == pytest.approx(0.5)

    @pytest.mark.parametrize(
        ("xs", "ys"),
        [([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]), ([1.0], [2.0])],
        ids=["zero-variance", "single-point"],
    )
    def test_undefined_coefficient_is_none_not_a_number(
        self, xs: list[float], ys: list[float]
    ) -> None:
        """Zero variance or one point gives None, never a division by zero or a NaN."""
        assert [pearson(xs, ys), pearson(xs, xs)] == [None, None]

    def test_lengths_must_match(self) -> None:
        """Series of different lengths are refused."""
        with pytest.raises(ValueError, match="lengths differ"):
            pearson([1.0, 2.0], [1.0])

    @given(st.lists(st.tuples(_POSITIVE, _POSITIVE), min_size=2, max_size=40))
    def test_coefficient_is_bounded_and_symmetric(self, points: list[tuple[float, float]]) -> None:
        """Any defined coefficient lies in [-1, 1] and does not depend on argument order."""
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        forward = pearson(xs, ys)
        backward = pearson(ys, xs)
        if forward is None:
            assert backward is None
            return
        assert -1.0 <= forward <= 1.0
        assert backward == pytest.approx(forward)


class TestReturnCorrelation:
    """The lock correlates the last 30 daily returns, not price levels."""

    def test_two_trending_coins_with_opposite_daily_moves_anti_correlate(self) -> None:
        """Both prices rise, yet their daily moves are opposite, so the coefficient is -1.

        A correlation of price levels would read near +1 here; the returns
        reading is what makes the lock about co-movement, not shared trend.
        """
        zigzag = [0.03 if i % 2 else 0.01 for i in range(CORRELATION_DAYS)]
        zagzig = [0.01 if i % 2 else 0.03 for i in range(CORRELATION_DAYS)]
        assert return_correlation(_walk(zigzag), _walk(zagzig)) == pytest.approx(-1.0)

    def test_only_the_last_thirty_returns_count(self) -> None:
        """Older history does not move the coefficient."""
        recent = [0.01 * ((i * 7) % 5 - 2) for i in range(CORRELATION_DAYS)]
        a = _walk([0.05, -0.04, 0.02, *recent])
        b_same_tail = _walk([-0.05, 0.04, -0.02, *recent])
        assert return_correlation(a, b_same_tail) == pytest.approx(1.0)

    def test_thirty_returns_need_thirty_one_closes(self) -> None:
        """§5.5's 30 days: thirty closes give no coefficient, thirty-one give one."""
        closes = _walk([0.01 * (i % 3) for i in range(30)])
        short = closes[1:]
        assert [return_correlation(short, short), return_correlation(closes, closes)] == [
            None,
            pytest.approx(1.0),
        ]

    def test_non_positive_close_is_refused(self) -> None:
        """A zero close has no return."""
        closes = [100.0] * CORRELATION_DAYS + [0.0]
        with pytest.raises(ValueError, match="positive"):
            return_correlation(closes, closes)

    def test_misaligned_series_are_refused(self) -> None:
        """Close series must cover the same dates."""
        with pytest.raises(ValueError, match="lengths differ"):
            return_correlation([1.0] * 31, [1.0] * 32)

    def test_window_below_two_is_refused(self) -> None:
        """One return has no correlation."""
        with pytest.raises(ValueError, match="at least 2"):
            return_correlation([1.0, 2.0], [1.0, 2.0], days=1)
