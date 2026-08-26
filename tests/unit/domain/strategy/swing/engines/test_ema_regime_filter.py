"""Unit tests for EmaRegimeFilter — EMA(200) 1D regime filter.

Covers:
- Returns False for insufficient bars
- above_ema() returns True when close > EMA, False otherwise
- EMA uses closed 1D bars only (no partial bar peeking)
- Deterministic on identical inputs
- ema_value() returns None for insufficient bars

Reference: spec-5-swing-strategy-adapter change.md D-1, D-3.
pre-registration-v1.md §5.1 (EMA(200) 1D regime filter).
"""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.swing.engines.ema import EmaRegimeFilter
from tests.unit.domain.strategy.swing.conftest import make_bars


class TestEmaRegimeInsufficientBars:
    """Returns False / None when fewer than period bars are available."""

    def test_above_ema_false_on_empty_bars(self) -> None:
        """above_ema must return False for empty bar sequence."""
        result = EmaRegimeFilter.above_ema([], period=200)
        assert not result, (
            "above_ema([]) must return False — conservative: no signal without regime data"
        )

    def test_above_ema_false_below_period(self) -> None:
        """Returns False when fewer than period 1D bars exist."""
        bars = make_bars([Decimal("100")] * 50, interval=KlineInterval.D1, interval_hours=24)
        result = EmaRegimeFilter.above_ema(bars, period=200)
        assert not result, "above_ema with only 50 bars and period=200 must return False"

    def test_ema_value_none_below_period(self) -> None:
        """ema_value returns None when fewer than period bars exist."""
        bars = make_bars([Decimal("100")] * 100, interval=KlineInterval.D1, interval_hours=24)
        result = EmaRegimeFilter.ema_value(bars, period=200)
        assert result is None, "ema_value with 100 bars and period=200 must return None"
        # Concrete anchor: with sufficient bars the result equals exactly 100
        assert EmaRegimeFilter.ema_value(bars + bars, period=200) == Decimal("100"), (
            "ema_value with 200 flat bars at 100 must equal Decimal('100')"
        )

    def test_ema_value_not_none_at_period(self) -> None:
        """ema_value must compute with exactly period bars."""
        bars = make_bars([Decimal("100")] * 200, interval=KlineInterval.D1, interval_hours=24)
        result = EmaRegimeFilter.ema_value(bars, period=200)
        assert result is not None, "ema_value must compute with exactly 200 bars (period=200)"
        assert isinstance(result, Decimal), f"ema_value must return a Decimal, got {type(result)}"


class TestEmaRegimeAboveBelow:
    """above_ema() behaviour on bars above/below EMA."""

    def test_above_ema_true_when_price_climbs_well_above(self) -> None:
        """Returns True when recent prices are well above the EMA seed level."""
        # 200 bars at 100, then 10 bars at 200 — last bar close=200 well above EMA≈100
        closes = [100] * 200 + [200] * 10
        bars = make_bars(closes, interval=KlineInterval.D1, interval_hours=24)
        result = EmaRegimeFilter.above_ema(bars, period=200)
        assert result is True, (
            "above_ema must return True when recent close (200) is well above EMA (~100)"
        )

    def test_above_ema_false_when_price_crashes_below(self) -> None:
        """Returns False when recent prices are well below the EMA seed level."""
        # 200 bars at 100 establish EMA ≈ 100; then 10 bars at 50
        closes = [100] * 200 + [50] * 10
        bars = make_bars(closes, interval=KlineInterval.D1, interval_hours=24)
        result = EmaRegimeFilter.above_ema(bars, period=200)
        assert not result, "above_ema must return False when recent close (50) is below EMA (~100)"

    def test_above_ema_with_small_period(self) -> None:
        """Works correctly with period=3 for fast testing."""
        # 3 bars at 100 → EMA seed = 100; then 1 bar at 101 → above EMA
        bars = make_bars([100, 100, 100, 101], interval=KlineInterval.D1, interval_hours=24)
        result = EmaRegimeFilter.above_ema(bars, period=3)
        assert result is True, "above_ema must return True: close=101 > EMA(3)≈100 after 3-bar seed"

    def test_above_ema_false_with_small_period_below(self) -> None:
        """Works correctly with period=3 when below EMA."""
        bars = make_bars([100, 100, 100, 99], interval=KlineInterval.D1, interval_hours=24)
        result = EmaRegimeFilter.above_ema(bars, period=3)
        assert not result, "above_ema must return False: close=99 < EMA(3)≈100"


class TestEmaRegimeDeterminism:
    """Determinism property."""

    def test_deterministic_on_identical_inputs(self) -> None:
        """above_ema must return the same result on two identical calls."""
        bars = make_bars([100] * 210, interval=KlineInterval.D1, interval_hours=24)
        r1 = EmaRegimeFilter.above_ema(bars, period=200)
        r2 = EmaRegimeFilter.above_ema(bars, period=200)
        # Both calls must produce a bool
        assert isinstance(r1, bool), f"above_ema must return bool, got {type(r1)}"
        assert r1 == r2, f"above_ema must be deterministic: first={r1}, second={r2}"

    def test_ema_value_deterministic(self) -> None:
        """ema_value must return the same result on two identical calls."""
        bars = make_bars([100] * 210, interval=KlineInterval.D1, interval_hours=24)
        r1 = EmaRegimeFilter.ema_value(bars, period=200)
        r2 = EmaRegimeFilter.ema_value(bars, period=200)
        # Value must be non-None (210 bars >= period=200)
        assert r1 is not None, "ema_value must return Decimal with 210 bars and period=200"
        assert isinstance(r1, Decimal), f"ema_value must return Decimal, got {type(r1)}"
        assert r1 == r2, f"ema_value must be deterministic: first={r1}, second={r2}"


class TestEmaRegimeClosedBarOnly:
    """EMA uses only closed 1D bars (not intra-day partial bars)."""

    def test_ema_unaffected_by_bar_high(self) -> None:
        """EMA uses bar.close only; a bar with very high high but same close must give same EMA."""
        bars_normal = make_bars(
            [100] * 5,
            interval=KlineInterval.D1,
            interval_hours=24,
        )
        bars_spike = make_bars(
            [100] * 5,
            highs=[500] * 5,  # extreme highs
            interval=KlineInterval.D1,
            interval_hours=24,
        )
        ema_normal = EmaRegimeFilter.ema_value(bars_normal, period=3)
        ema_spike = EmaRegimeFilter.ema_value(bars_spike, period=3)
        # Both EMAs must be exactly 100.0 (all closes are 100, so EMA seed = 100)
        assert ema_normal is not None, "ema_value must compute with 5 bars and period=3"
        assert ema_spike is not None, "ema_value must compute with 5 bars and period=3 (spike)"
        # Concrete anchor: all closes are 100, so EMA must equal exactly 100
        assert ema_normal == Decimal("100"), (
            f"EMA of 5 flat bars at 100 with period=3 must equal Decimal('100'), got {ema_normal}"
        )
        assert ema_normal == ema_spike, (
            f"EMA uses close only; intra-bar high spike must not affect EMA: "
            f"normal={ema_normal}, spike={ema_spike}"
        )
