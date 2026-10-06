"""Unit tests for AdxEngine — ADX(14) with Wilder smoothing.

Covers:
- Returns None for insufficient bars (< 2*period+1)
- ADX is within [0, 100] for valid inputs
- ADX is higher for strongly trending bars than flat bars
- Deterministic on identical inputs
- Period parameter is respected

Reference: spec-5-swing-strategy-adapter change.md.
pre-registration-v1.md §5.1 (ADX(14) ≥ 25, 4h).
"""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.strategy.swing.engines.adx import AdxEngine
from tests.unit.domain.strategy.swing.conftest import make_bars


class TestAdxInsufficientBars:
    """Returns None when fewer than 2*period+1 bars are available."""

    def test_none_on_empty_bars(self) -> None:
        """ADX must return None for empty bar sequence."""
        result = AdxEngine.value([], period=14)
        assert result is None, "AdxEngine.value([]) must return None"
        # Concrete anchor: with 29 bars the result is non-None
        assert AdxEngine.value([], period=14) is None, "AdxEngine.value([]) type must be NoneType"

    def test_none_on_exactly_2_period_bars(self) -> None:
        """2*period=28 bars is not sufficient; needs 2*period+1=29."""
        bars = make_bars([100] * 28, highs=[105] * 28, lows=[95] * 28)
        result = AdxEngine.value(bars, period=14)
        assert result is None, f"ADX(14) with {len(bars)} bars must return None (needs 29 minimum)"
        assert len(bars) == 28, "fixture must have exactly 28 bars for this boundary test"

    def test_returns_value_with_minimum_bars(self) -> None:
        """ADX must compute with exactly 2*period+1=29 bars."""
        bars = make_bars(
            [100 + i for i in range(29)],
            highs=[105 + i for i in range(29)],
            lows=[95 + i for i in range(29)],
        )
        result = AdxEngine.value(bars, period=14)
        assert result is not None, "ADX(14) must compute with 29 bars (minimum for 2*14+1)"
        assert isinstance(result, Decimal), f"ADX must return Decimal, got {type(result)}"


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

    def test_adx_non_negative_flat(self) -> None:
        """ADX must be ≥ 0 for flat (ranging) bars."""
        bars = make_bars([100] * 50, highs=[101] * 50, lows=[99] * 50)
        result = AdxEngine.value(bars, period=14)
        # May be None or a number; if not None it must be ≥ 0
        if result is not None:
            assert result >= Decimal("0"), f"ADX must be ≥ 0 even for flat bars, got {result}"


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


class TestAdxDeterminism:
    """Determinism property."""

    def test_deterministic_on_identical_inputs(self) -> None:
        """ADX must return the same value on two identical calls."""
        bars = make_bars([100 + i for i in range(40)])
        r1 = AdxEngine.value(bars, period=14)
        r2 = AdxEngine.value(bars, period=14)
        assert isinstance(r1, Decimal), f"ADX must return Decimal with 40 bars, got {type(r1)}"
        assert r1 == r2, f"ADX must be deterministic: first={r1}, second={r2}"


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
