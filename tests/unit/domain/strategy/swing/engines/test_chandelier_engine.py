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

from hermes.domain.market_data.bar import Bar
from hermes.domain.strategy.swing.engines.atr import AtrEngine
from hermes.domain.strategy.swing.engines.chandelier import ChandelierEngine
from tests.unit.domain.strategy.swing.conftest import make_bar, make_bars


class TestChandelierInsufficientBars:
    """Returns None when ATR cannot be computed (< period + 1 bars)."""

    def test_none_on_empty_bars(self) -> None:
        """Chandelier must return None for empty bar sequence."""
        result = ChandelierEngine.trailing_stop([], period=22)
        assert result is None, "trailing_stop([]) must return None — no bars to compute ATR"

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
        assert atr_check == Decimal("10"), (
            f"ATR(3) seed must be (6 + 6 + 18) / 3 = 10, got {atr_check}"
        )
        assert result == Decimal("90"), f"Chandelier stop must be 120 - 3 x 10 = 90, got {result}"

    def test_atr_multiplier_respected(self) -> None:
        """The multiplier scales the ATR distance: 105 - 3 x 10 = 75 and 105 - 1 x 10 = 95."""
        bars = make_bars(
            [100] * 4,
            highs=[105] * 4,
            lows=[95] * 4,
        )
        stop_3x = ChandelierEngine.trailing_stop(bars, period=3, atr_mult=Decimal("3.0"))
        stop_1x = ChandelierEngine.trailing_stop(bars, period=3, atr_mult=Decimal("1.0"))
        assert stop_3x == Decimal("75"), f"Chandelier(3, 3.0) must be 75, got {stop_3x}"
        assert stop_1x == Decimal("95"), f"Chandelier(3, 1.0) must be 95, got {stop_1x}"

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


def _pinning_bars() -> list[Bar]:
    """24 bars whose stop moves if the period or the multiplier changes by one step.

    Every close is 100 and every low 95, so each bar's true range is its high
    minus 95. All highs are 105 (true range 10) except two:

    - ``bars[1]`` (``bars[-23]``, just outside a 22-bar window): high 140, TR 45.
    - ``bars[2]`` (``bars[-22]``, the oldest bar inside it): high 125, TR 30.

    A 21-bar window misses the 125, a 23-bar window catches the 140, and a
    slice taken from the wrong end of the list sees only 105s.
    """
    highs = [105] * 24
    highs[1] = 140
    highs[2] = 125
    return make_bars([100] * 24, highs=highs, lows=[95] * 24)


class TestChandelierPreRegisteredDefaults:
    """The defaults are the pre-registered Chandelier(22, 3.0) of pre-reg v1 §5.2."""

    def test_default_stop_is_the_hand_computed_chandelier_22_3(self) -> None:
        """With no arguments the stop is highest_high(22) - 3.0 x ATR(22), by hand.

        True ranges of bars 1..23: 45, 30, then 21 bars of 10.
        Wilder seed over bars 1..22: (45 + 30 + 20 x 10) / 22 = 275 / 22 = 12.5.
        One smoothing step for bar 23: (12.5 x 21 + 10) / 22 = 272.5 / 22 = 545 / 44.
        highest_high(bars[-22:]) = 125 (``bars[2]``); the 140 in ``bars[1]`` is outside.
        Stop = 125 - 3 x 545 / 44 = (5500 - 1635) / 44 = 3865 / 44 = 87.8409...

        Period 21 gives highest high 105, period 23 gives 140 and a 23-bar seed,
        multiplier 3.5 gives 125 - 3.5 x 545 / 44: none lands on 3865 / 44.
        """
        stop = ChandelierEngine.trailing_stop(_pinning_bars())

        assert stop is not None, "24 bars are enough for Chandelier(22)"
        assert abs(stop - Decimal(3865) / Decimal(44)) < Decimal("1e-20"), (
            f"Chandelier(22, 3.0) stop must be 3865/44 = 87.8409..., got {stop}"
        )

    def test_default_period_needs_23_bars(self) -> None:
        """With no arguments 22 bars return None and 23 bars return a stop.

        ATR(22) needs 22 true ranges, so 23 bars. A default of 21 would answer on
        22 bars; a default of 23 would still answer None on 23 bars.
        """
        bars = _pinning_bars()

        assert ChandelierEngine.trailing_stop(bars[-22:]) is None, (
            "Chandelier(22) must not answer with only 22 bars"
        )
        assert ChandelierEngine.trailing_stop(bars[-23:]) is not None, (
            "Chandelier(22) must answer with 23 bars"
        )


class TestChandelierLookbackWindow:
    """The highest high comes from exactly the last ``period`` bars."""

    def test_highest_high_includes_the_oldest_bar_of_the_window(self) -> None:
        """A peak at ``bars[-period]`` sets the stop even though later bars are lower.

        Period 3 over 5 bars, closes 100, lows 95, highs 105 except ``bars[2]``
        (``bars[-3]``) at 125. True ranges of bars 1..4: 10, 30, 10, 10.
        Wilder seed over bars 1..3: 50 / 3; one step for bar 4: (50/3 x 2 + 10) / 3 = 130 / 9.
        Stop = 125 - 3 x 130 / 9 = 125 - 130 / 3 = 245 / 3 = 81.666...
        A window that drops ``bars[-3]`` would use 105 and give 61.666...
        """
        highs = [105, 105, 125, 105, 105]
        bars = make_bars([100] * 5, highs=highs, lows=[95] * 5)

        stop = ChandelierEngine.trailing_stop(bars, period=3, atr_mult=Decimal("3.0"))

        assert stop is not None, "5 bars are enough for Chandelier(3)"
        assert abs(stop - Decimal(245) / Decimal(3)) < Decimal("1e-20"), (
            f"Chandelier(3, 3.0) stop must be 245/3 = 81.666..., got {stop}"
        )

    def test_highest_high_ignores_the_bar_just_before_the_window(self) -> None:
        """A peak at ``bars[-period - 1]`` is outside the window and does not set the stop.

        Period 3 over 5 bars, closes 100, lows 95, highs 105 except ``bars[1]``
        (``bars[-4]``) at 125. True ranges of bars 1..4: 30, 10, 10, 10.
        Wilder seed over bars 1..3: 50 / 3; one step for bar 4: 130 / 9 (as above).
        Stop = 105 - 3 x 130 / 9 = 105 - 130 / 3 = 185 / 3 = 61.666...
        A window one bar too wide would use 125 and give 81.666...
        """
        highs = [105, 125, 105, 105, 105]
        bars = make_bars([100] * 5, highs=highs, lows=[95] * 5)

        stop = ChandelierEngine.trailing_stop(bars, period=3, atr_mult=Decimal("3.0"))

        assert stop is not None, "5 bars are enough for Chandelier(3)"
        assert abs(stop - Decimal(185) / Decimal(3)) < Decimal("1e-20"), (
            f"Chandelier(3, 3.0) stop must be 185/3 = 61.666..., got {stop}"
        )
