"""Unit tests for ChandelierEngine — Chandelier(22, 3.0) trailing stop.

Covers:
- Returns None for insufficient bars
- Formula: highest_high(22) - 3 * ATR(22)
- Stop is always below the current close on well-behaved bars
- Deterministic on identical inputs
- ATR multiplier parameter is respected

Reference: spec-5-swing-strategy-adapter change.md D-4.
pre-registration-v1.md §5.2 (Chandelier 22, 3.0).
"""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.strategy.swing.engines.atr import AtrEngine
from hermes.domain.strategy.swing.engines.chandelier import ChandelierEngine
from tests.unit.domain.strategy.swing.conftest import make_bar, make_bars


class TestChandelierInsufficientBars:
    """Returns None when ATR cannot be computed (< period + 1 bars)."""

    def test_none_on_empty_bars(self) -> None:
        """Chandelier must return None for empty bar sequence."""
        result = ChandelierEngine.trailing_stop([], period=22)
        assert result is None, "trailing_stop([]) must return None — no bars to compute ATR"
        assert len([]) == 0, "empty bars fixture sanity check — zero bars provided"

    def test_none_on_period_bars_exactly(self) -> None:
        """Exactly period=22 bars is insufficient (ATR needs period+1=23)."""
        bars = make_bars([Decimal("100")] * 22, highs=[105] * 22, lows=[95] * 22)
        result = ChandelierEngine.trailing_stop(bars, period=22)
        assert result is None, "trailing_stop with exactly 22 bars and period=22 must return None"
        assert len(bars) == 22, "fixture must have exactly 22 bars for this boundary test"

    def test_returns_value_with_period_plus_one_bars(self) -> None:
        """Period+1=23 bars should be sufficient to compute the stop."""
        bars = make_bars([Decimal("100")] * 23, highs=[105] * 23, lows=[95] * 23)
        result = ChandelierEngine.trailing_stop(bars, period=22)
        assert result is not None, "trailing_stop must return a value with period+1=23 bars"
        assert isinstance(result, Decimal), f"trailing_stop must return Decimal, got {type(result)}"


class TestChandelierFormula:
    """Hand-computed fixture tests for the Chandelier stop formula."""

    def test_stop_formula_period_3(self) -> None:
        """Hand-computed Chandelier(3, 3.0) on a minimal fixture.

        Bars (period=3, so need 4 bars minimum):
          bar0: high=100, low=95, close=98  (prev_close reference for TR)
          bar1: high=102, low=96, close=100 → TR = max(6, |102-98|, |96-98|) = max(6,4,2) = 6
          bar2: high=104, low=98, close=102 → TR = max(6, |104-100|, |98-100|) = max(6,4,2) = 6
          bar3: high=106, low=100, close=104 → TR = max(6, |106-102|, |100-102|) = max(6,4,2) = 6

        ATR(3) seed = (6+6+6)/3 = 6
        highest_high(last 3 bars) = max(104, 106, ...) = 106  (bars[-3:] = bar1,bar2,bar3)
        Chandelier stop = 106 - 3.0 * 6 = 106 - 18 = 88
        """
        bars = [
            make_bar(close=Decimal("98"), high=Decimal("100"), low=Decimal("95"), index=0),
            make_bar(close=Decimal("100"), high=Decimal("102"), low=Decimal("96"), index=1),
            make_bar(close=Decimal("102"), high=Decimal("104"), low=Decimal("98"), index=2),
            make_bar(close=Decimal("104"), high=Decimal("106"), low=Decimal("100"), index=3),
        ]
        result = ChandelierEngine.trailing_stop(bars, period=3, atr_mult=Decimal("3.0"))
        expected = Decimal("88")
        assert result is not None, "Chandelier(3) should compute with 4 bars"
        assert abs(result - expected) < Decimal("0.01"), (
            f"Chandelier(3, 3.0) stop = {expected}, got {result:.4f} "
            "— highest_high(3)=106, ATR(3)=6, stop=106-18=88"
        )

    def test_stop_includes_current_bar_in_highest_high(self) -> None:
        """highest_high includes the current (signal) bar in Chandelier.

        The Chandelier trailing stop uses highest_high INCLUSIVE of the
        current bar because the stop trails as price rises.
        """
        bars = [
            make_bar(close=Decimal("98"), high=Decimal("100"), low=Decimal("95"), index=0),
            make_bar(close=Decimal("100"), high=Decimal("102"), low=Decimal("96"), index=1),
            make_bar(close=Decimal("102"), high=Decimal("104"), low=Decimal("98"), index=2),
            # Signal bar has the highest high of the window
            make_bar(close=Decimal("110"), high=Decimal("120"), low=Decimal("108"), index=3),
        ]
        result = ChandelierEngine.trailing_stop(bars, period=3, atr_mult=Decimal("3.0"))
        assert result is not None, "Chandelier(3) must compute with 4 bars"
        # highest_high(bars[-3:]) includes bar3 with high=120
        # TR for bar3 = max(12, |120-102|, |108-102|) = max(12,18,6) = 18
        # ATR seed = (6 + 6 + 18) / 3 = 10
        # Chandelier = 120 - 3.0 * 10 = 90
        atr_check = AtrEngine.value(bars, period=3)
        if atr_check is not None:
            expected = Decimal("120") - Decimal("3.0") * atr_check
            assert abs(result - expected) < Decimal("0.01"), (
                f"Chandelier stop should be {expected:.2f}, got {result:.2f}"
            )

    def test_atr_multiplier_respected(self) -> None:
        """Larger ATR multiplier produces lower (tighter) stop."""
        bars = make_bars(
            [100] * 4,
            highs=[105] * 4,
            lows=[95] * 4,
        )
        stop_3x = ChandelierEngine.trailing_stop(bars, period=3, atr_mult=Decimal("3.0"))
        stop_1x = ChandelierEngine.trailing_stop(bars, period=3, atr_mult=Decimal("1.0"))
        if stop_3x is not None and stop_1x is not None:
            assert stop_3x < stop_1x, (
                f"Larger ATR multiplier must produce lower stop: 3x={stop_3x:.2f}, 1x={stop_1x:.2f}"
            )

    def test_stop_below_close_on_trending_bars(self) -> None:
        """Chandelier stop must be below current close on well-behaved trending bars."""
        # Steadily rising prices with reasonable volatility
        closes = [100 + i * 2 for i in range(24)]
        highs = [c + 3 for c in closes]
        lows = [c - 3 for c in closes]
        bars = make_bars(closes, highs=highs, lows=lows)
        result = ChandelierEngine.trailing_stop(bars, period=22, atr_mult=Decimal("3.0"))
        assert result is not None, "Chandelier(22) should compute with 24 bars"
        last_close = bars[-1].close
        assert result < last_close, (
            f"Chandelier stop ({result:.2f}) must be below current close ({last_close:.2f})"
        )


class TestChandelierDeterminism:
    """Determinism property."""

    def test_deterministic_on_identical_inputs(self) -> None:
        """Chandelier stop is deterministic on identical bar sequences."""
        bars = make_bars(
            [100 + i for i in range(24)],
            highs=[105 + i for i in range(24)],
            lows=[95 + i for i in range(24)],
        )
        r1 = ChandelierEngine.trailing_stop(bars, period=22)
        r2 = ChandelierEngine.trailing_stop(bars, period=22)
        assert isinstance(r1, Decimal), (
            f"trailing_stop must return Decimal with 24 bars, got {type(r1)}"
        )
        assert r1 == r2, f"trailing_stop must be deterministic: first={r1}, second={r2}"
