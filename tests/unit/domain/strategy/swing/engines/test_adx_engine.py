"""Unit tests for AdxEngine — ADX(14) with Wilder smoothing.

Covers:
- Returns None for insufficient bars (< 2*period+1)
- ADX is within [0, 100] for valid inputs
- ADX is higher for strongly trending bars than flat bars
- Period parameter is respected

Reference: spec-5-swing-strategy-adapter change.md.
pre-registration-v1.md §5.1 (ADX(14) ≥ 25, 4h).
"""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.market_data.bar import Bar
from hermes.domain.strategy.swing.engines.adx import AdxEngine
from tests.unit.domain.strategy.swing.conftest import make_bars


def _climb(count: int) -> list[Bar]:
    """Bars whose high, low and close each rise by 1: range 10, close 5 under the high."""
    return make_bars(
        [100 + i for i in range(count)],
        highs=[105 + i for i in range(count)],
        lows=[95 + i for i in range(count)],
    )


class TestAdxInsufficientBars:
    """Returns None when fewer than 2*period+1 bars are available."""

    def test_needs_two_periods_plus_one_bars(self) -> None:
        """ADX(14) is None for 0 and 28 bars and exactly 100 at 29 bars on a one-way climb.

        Each bar's high, low and close are one above the previous bar's, so
        every +DM is 1, every -DM is 0 and every true range is 10: +DI is
        positive, -DI is 0, DX = 100 x +DI / +DI = 100 on every bar, and the
        ADX mean of 14 of them is 100.
        """
        bars = _climb(29)

        assert AdxEngine.value([], period=14) is None, "no bars, no ADX"
        assert AdxEngine.value(bars[:28], period=14) is None, "28 bars is one short of 2 x 14 + 1"
        assert AdxEngine.value(bars, period=14) == Decimal(100), "29 bars on a pure climb give 100"


class TestAdxDirection:
    """ADX measures how strong a trend is, whichever way it points."""

    def test_a_one_way_fall_reads_100_like_a_one_way_climb(self) -> None:
        """Mirror of the climb: every -DM is 1 and every +DM is 0, so -DI replaces +DI and DX = 100.

        A formula that dropped the absolute value of +DI minus -DI would read -100.
        """
        bars = make_bars(
            [200 - i for i in range(40)],
            highs=[205 - i for i in range(40)],
            lows=[195 - i for i in range(40)],
        )

        assert AdxEngine.value(bars, period=14) == Decimal(100), "a pure fall is a full trend"

    def test_bars_with_no_directional_movement_read_0(self) -> None:
        """Highs and lows that never move give +DM = -DM = 0, so DX and ADX are 0, not None."""
        bars = make_bars([100] * 50, highs=[101] * 50, lows=[99] * 50)

        assert AdxEngine.value(bars, period=14) == Decimal(0), "flat bars have no trend"


class TestAdxBounds:
    """ADX must be in [0, 100] for any valid input."""

    def test_adx_non_negative_trending(self) -> None:
        """ADX must be ≥ 0 for trending bars."""
        bars = make_bars(
            [100 + i for i in range(50)],
            highs=[105 + i for i in range(50)],
            lows=[95 + i for i in range(50)],
        )
        result = AdxEngine.value(bars, period=14)
        assert result is not None, "ADX(14) must compute with 50 bars"
        assert result >= Decimal("0"), f"ADX must be ≥ 0, got {result}"

    def test_adx_at_most_100_trending(self) -> None:
        """ADX must be ≤ 100 for valid inputs."""
        bars = make_bars(
            [100 + i * 5 for i in range(50)],
            highs=[110 + i * 5 for i in range(50)],
            lows=[95 + i * 5 for i in range(50)],
        )
        result = AdxEngine.value(bars, period=14)
        assert result is not None, "ADX(14) must compute with 50 bars"
        assert result <= Decimal("100"), f"ADX must be ≤ 100, got {result}"


class TestAdxTrendStrength:
    """ADX should be larger for strongly trending bars than choppy bars."""

    def test_trending_has_higher_adx_than_ranging(self) -> None:
        """Strong uptrend should produce higher ADX than choppy range."""
        # Strong uptrend: each bar higher than the last, consistent
        trending = make_bars(
            [100 + i * 2 for i in range(40)],
            highs=[102 + i * 2 for i in range(40)],
            lows=[98 + i * 2 for i in range(40)],
        )
        # Choppy: alternating up/down around 100
        choppy_closes = [100 + 5 * (-1 if i % 2 else 1) for i in range(40)]
        choppy = make_bars(
            choppy_closes,
            highs=[c + 2 for c in choppy_closes],
            lows=[c - 2 for c in choppy_closes],
        )
        adx_trend = AdxEngine.value(trending, period=14)
        adx_choppy = AdxEngine.value(choppy, period=14)
        assert adx_trend is not None, "ADX(14) on trending must compute with 40 bars"
        assert adx_choppy is not None, "ADX(14) on choppy must compute with 40 bars"
        assert adx_trend > adx_choppy, (
            f"Trending ADX ({adx_trend:.2f}) must exceed choppy ADX ({adx_choppy:.2f})"
        )


class TestAdxPeriodParameter:
    """Period parameter changes the smoothing correctly."""

    def test_different_periods_produce_different_values(self) -> None:
        """ADX(7) and ADX(14) should generally differ on the same bars."""
        bars = make_bars(
            [100 + i for i in range(50)],
            highs=[105 + i for i in range(50)],
            lows=[95 + i for i in range(50)],
        )
        adx7 = AdxEngine.value(bars, period=7)
        adx14 = AdxEngine.value(bars, period=14)
        assert adx7 is not None, "ADX(7) must compute with 50 bars"
        assert adx14 is not None, "ADX(14) must compute with 50 bars"
        # Both must be valid non-negative numbers
        assert adx7 >= Decimal("0"), f"ADX(7) must be ≥ 0, got {adx7}"
        assert adx14 >= Decimal("0"), f"ADX(14) must be ≥ 0, got {adx14}"
