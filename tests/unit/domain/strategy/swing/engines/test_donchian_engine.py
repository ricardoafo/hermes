"""Unit tests for DonchianEngine — Donchian(20) breakout detector.

Covers:
- Returns False for insufficient bars (< period + 1)
- Returns True on the exact bar where close > highest_high(prior 20)
- Returns False when close ≤ highest_high
- No-lookahead: channel uses prior bars only, not signal bar's high

Reference: spec-5-swing-strategy-adapter change.md.
pre-registration-v1.md §5.1 (Donchian(20) 4h breakout).
"""

from decimal import Decimal

from hermes.domain.strategy.swing.engines.donchian import DonchianEngine
from tests.unit.domain.strategy.swing.conftest import make_bars


class TestDonchianInsufficientBars:
    """Returns False when fewer than period + 1 bars are available."""

    def test_false_on_empty_bars(self) -> None:
        """Donchian must return False on empty bar sequence."""
        result = DonchianEngine.high_break([], period=20)
        assert result is False, "high_break([]) must return False — no history to form channel"

    def test_false_on_fewer_than_period_bars(self) -> None:
        """Five rising bars with period 20 are no breakout, however high the last close.

        Without the history check the channel would be the four bars before the
        signal (high 103), and a close of 104 would call a breakout.
        """
        bars = make_bars(list(range(100, 105)))
        result = DonchianEngine.high_break(bars, period=20)
        assert result is False, "high_break with 5 bars and period=20 must return False"


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
        assert result is False, (
            "high_break must return False when close == highest_high (not strictly greater)"
        )

    def test_false_when_close_below_highest_high(self) -> None:
        """Returns False when last close is below the channel high."""
        closes = [Decimal("100")] * 19 + [Decimal("110")] + [Decimal("105")]
        # prior 20 bars include the 110 bar; last bar closes at 105 < 110
        bars = make_bars(closes)
        result = DonchianEngine.high_break(bars, period=20)
        assert result is False, (
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

    def test_breakout_on_spike_in_channel_window(self) -> None:
        """A spike within the prior 20 bars raises the channel; close below spike = no breakout."""
        # bar 10 has high=150, rest have high=100; signal bar closes at 120
        highs = [100] * 20 + [120]
        highs[10] = 150
        closes = [100] * 20 + [120]
        bars = make_bars(closes, highs=highs)
        result = DonchianEngine.high_break(bars, period=20)
        assert result is False, (
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

    def test_period_5_breakout(self) -> None:
        """Period=5 breakout with 6 bars."""
        bars = make_bars([Decimal("100")] * 5 + [Decimal("101")])
        result = DonchianEngine.high_break(bars, period=5)
        assert result is True, (
            "high_break with period=5: close=101 > channel_high=100 → must be True"
        )


class TestDonchianPreRegisteredDefault:
    """With no ``period`` argument the channel is the pre-registered Donchian(20) of §5.1."""

    def test_default_channel_includes_the_twentieth_prior_bar(self) -> None:
        """A high of 102 at ``bars[-21]`` (20th bar before the signal) blocks a close of 101.

        21 bars: ``bars[0]`` high 102, ``bars[1..19]`` high 100, signal close 101.
        Donchian(20) channel = max(high of ``bars[0..19]``) = 102, and 101 > 102 is false.
        A 19-bar channel would see only 100 and call a breakout.
        """
        highs = [102] + [100] * 19 + [101]
        bars = make_bars([100] * 20 + [101], highs=highs)

        assert DonchianEngine.high_break(bars) is False, (
            "Donchian(20): the 102 high 20 bars back is in the channel, so close 101 is no breakout"
        )

    def test_default_channel_excludes_the_twenty_first_prior_bar(self) -> None:
        """A high of 200 at ``bars[-22]`` (21st bar before the signal) does not block a breakout.

        22 bars: ``bars[0]`` high 200, ``bars[1..20]`` high 100, signal close 101.
        Donchian(20) channel = max(high of ``bars[1..20]``) = 100, and 101 > 100.
        A 21-bar channel would include the 200 and miss the breakout.
        """
        highs = [200] + [100] * 20 + [101]
        bars = make_bars([200] + [100] * 20 + [101], highs=highs)

        assert DonchianEngine.high_break(bars) is True, (
            "Donchian(20): the 200 high 21 bars back is outside the channel; close 101 breaks out"
        )

    def test_default_needs_21_bars_even_when_the_last_close_tops_them_all(self) -> None:
        """20 rising bars are never a breakout: Donchian(20) needs 20 prior bars plus the signal.

        Closes and highs 100..119, so the last close (119) is above every earlier
        high (at most 118). Any shorter minimum history would call this a breakout.
        """
        bars = make_bars(list(range(100, 120)))

        assert DonchianEngine.high_break(bars) is False, (
            "Donchian(20) with only 20 bars must not signal, however high the last close"
        )


class TestDonchianOddPeriodWindow:
    """For an odd period the history check and the channel use exactly ``period + 1`` bars."""

    def test_period_5_with_5_rising_bars_is_no_breakout(self) -> None:
        """Closes and highs 100..104 with period 5: one bar short, so no signal.

        104 is above every earlier high, so only the ``period + 1`` history rule says no.
        """
        bars = make_bars([100, 101, 102, 103, 104])

        assert DonchianEngine.high_break(bars, period=5) is False, (
            "Donchian(5) with 5 bars must not signal: it needs 6"
        )

    def test_period_5_channel_includes_the_fifth_prior_bar(self) -> None:
        """A high of 102 at ``bars[-6]`` (5th bar before the signal) blocks a close of 101.

        6 bars: ``bars[0]`` high 102, ``bars[1..4]`` high 100, signal close 101.
        Channel = 102 > 101, so no breakout; a 4-bar channel would call one.
        """
        highs = [102, 100, 100, 100, 100, 101]
        bars = make_bars([100, 100, 100, 100, 100, 101], highs=highs)

        assert DonchianEngine.high_break(bars, period=5) is False, (
            "Donchian(5): the 102 high 5 bars back is in the channel, so close 101 is no breakout"
        )
