"""Unit tests for DonchianEngine — Donchian(20) breakout detector.

Covers:
- Returns False for insufficient bars (< period + 1)
- Returns True on the exact bar where close > highest_high(prior 20)
- Returns False when close ≤ highest_high
- Deterministic on identical bar sequences
- No-lookahead: channel uses prior bars only, not signal bar's high

Reference: spec-5-swing-strategy-adapter change.md.
pre-registration-v1.md §5.1 (Donchian(20) 4h breakout).
"""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.strategy.swing.engines.donchian import DonchianEngine
from tests.unit.domain.strategy.swing.conftest import make_bars


class TestDonchianInsufficientBars:
    """Returns False when fewer than period + 1 bars are available."""

    def test_false_on_empty_bars(self) -> None:
        """Donchian must return False on empty bar sequence."""
        result = DonchianEngine.high_break([], period=20)
        assert not result, "high_break([]) must return False — no history to form channel"

    def test_false_on_exactly_period_bars(self) -> None:
        """Exactly period bars is not enough (need period+1: period for channel + 1 signal)."""
        bars = make_bars([Decimal("100")] * 20)
        result = DonchianEngine.high_break(bars, period=20)
        assert not result, (
            f"high_break with exactly {len(bars)} bars (period=20) must return False — "
            "need 21 bars minimum"
        )

    def test_false_on_fewer_than_period_bars(self) -> None:
        """Fewer than period bars must return False."""
        bars = make_bars([Decimal("100")] * 5)
        result = DonchianEngine.high_break(bars, period=20)
        assert not result, "high_break with 5 bars and period=20 must return False"

    def test_false_on_period_plus_one_bars_no_breakout(self) -> None:
        """Period+1 bars with close equal to highest_high — not a breakout."""
        bars = make_bars([Decimal("100")] * 21)
        result = DonchianEngine.high_break(bars, period=20)
        assert not result, "high_break with flat bars (close = highest_high) must return False"


class TestDonchianBreakout:
    """Breakout detection on various scenarios."""

    def test_true_on_exact_breakout_bar(self) -> None:
        """Returns True when last bar's close strictly exceeds highest_high of prior 20."""
        # 20 bars at price 100, then 1 bar closing at 101
        bars = make_bars([Decimal("100")] * 20 + [Decimal("101")])
        result = DonchianEngine.high_break(bars, period=20)
        assert result is True, (
            "high_break must return True when last close (101) > highest_high(prior 20 = 100)"
        )

    def test_false_when_close_equals_highest_high(self) -> None:
        """Returns False when close equals highest_high (not strictly above)."""
        bars = make_bars([Decimal("100")] * 21)
        # All closes = all highs = 100; last close = 100 = highest_high → not a breakout
        result = DonchianEngine.high_break(bars, period=20)
        assert not result, (
            "high_break must return False when close == highest_high (not strictly greater)"
        )

    def test_false_when_close_below_highest_high(self) -> None:
        """Returns False when last close is below the channel high."""
        closes = [Decimal("100")] * 19 + [Decimal("110")] + [Decimal("105")]
        # prior 20 bars include the 110 bar; last bar closes at 105 < 110
        bars = make_bars(closes)
        result = DonchianEngine.high_break(bars, period=20)
        assert not result, (
            "high_break must return False when last close (105) < highest_high of prior 20 (110)"
        )

    def test_breakout_uses_only_prior_bars_for_channel(self) -> None:
        """Channel must NOT include the signal bar's high — only prior period bars.

        Setup: prior 20 bars have high=100; signal bar has high=200 but close=101.
        Since channel uses prior bars only, channel_high=100, close=101 > 100 → True.
        If the signal bar's high were included, channel_high=200 and close=101 < 200 → False.
        """
        bars = make_bars(
            [Decimal("100")] * 20 + [Decimal("101")],
            highs=[Decimal("100")] * 20 + [Decimal("200")],
        )
        result = DonchianEngine.high_break(bars, period=20)
        assert result is True, (
            "high_break must use PRIOR bars for channel (not signal bar's high=200); "
            "close=101 > channel_high=100 → breakout must be True"
        )

    def test_deterministic_on_identical_inputs(self) -> None:
        """Returns identical result on two calls with same bars."""
        bars = make_bars([Decimal("100")] * 20 + [Decimal("101")])
        r1 = DonchianEngine.high_break(bars, period=20)
        r2 = DonchianEngine.high_break(bars, period=20)
        assert isinstance(r1, bool), f"high_break must return bool, got {type(r1)}"
        assert r1 == r2, f"high_break must be deterministic: first={r1}, second={r2}"

    def test_breakout_on_spike_in_channel_window(self) -> None:
        """A spike within the prior 20 bars raises the channel; close below spike = no breakout."""
        # bar 10 has high=150, rest have high=100; signal bar closes at 120
        highs = [100] * 20 + [120]
        highs[10] = 150
        closes = [100] * 20 + [120]
        bars = make_bars(closes, highs=highs)
        result = DonchianEngine.high_break(bars, period=20)
        assert not result, (
            "high_break must return False: close=120 < channel_high=150 (spike in window)"
        )

    def test_channel_excludes_bars_older_than_period(self) -> None:
        """Very old spikes (before the period window) do not affect the channel."""
        # 5 extra bars at 200, then 20 bars at 100, then signal bar closing at 101
        # Channel = max(high of bars[-21:-1]) = 100 only; old 200-bars are outside
        highs = [200] * 5 + [100] * 20 + [101]
        closes = [200] * 5 + [100] * 20 + [101]
        bars = make_bars(closes, highs=highs)
        result = DonchianEngine.high_break(bars, period=20)
        assert result is True, (
            "high_break must return True: old 200-spikes are outside the 20-bar channel "
            "window; channel_high=100, close=101 → breakout"
        )


class TestDonchianPeriodParameter:
    """Period parameter changes channel width correctly."""

    def test_period_5_requires_6_bars(self) -> None:
        """Period=5 needs at least 6 bars."""
        bars = make_bars([Decimal("100")] * 5)
        result = DonchianEngine.high_break(bars, period=5)
        assert not result, "high_break with period=5 and only 5 bars must return False"

    def test_period_5_breakout(self) -> None:
        """Period=5 breakout with 6 bars."""
        bars = make_bars([Decimal("100")] * 5 + [Decimal("101")])
        result = DonchianEngine.high_break(bars, period=5)
        assert result is True, (
            "high_break with period=5: close=101 > channel_high=100 → must be True"
        )
