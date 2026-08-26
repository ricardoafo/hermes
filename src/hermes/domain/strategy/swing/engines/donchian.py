"""DonchianEngine — Donchian channel breakout detector (4h).

Implements the closed-bar Donchian(20) breakout condition per pre-registration
v1 §5.1:

    Entry signal fires when:
        bar.close > max(high of prior ``period`` bars)

The comparison is against the HIGH of prior bars (not close), because the
Donchian channel upper band is defined as the highest high over the lookback
window.  "Prior" means the bars BEFORE the current signal bar — the current
bar's high is NOT included in the channel calculation (no lookahead).

No-lookahead contract (SA-4 P1):
    ``high_break`` only reads bars with index [0..n-2] for the channel, and
    checks bar [n-1] (the signal bar) for the breakout.  The signal bar's
    high does not enter the channel computation.

Domain purity: no I/O, no async, stdlib + Decimal only.

Reference: pre-registration-v1.md §5.1 (Donchian 20, 4h).
spec-5-swing-strategy-adapter change.md D-1, D-3.
"""

from __future__ import annotations

from collections.abc import Sequence

from hermes.domain.market_data.bar import Bar

__all__ = ["DonchianEngine"]


class DonchianEngine:
    """Donchian channel breakout engine.

    All methods are stateless class methods.

    Reference: pre-registration-v1.md §5.1.
    """

    @classmethod
    def high_break(cls, bars: Sequence[Bar], period: int = 20) -> bool:
        """Return ``True`` when the last bar closes above the Donchian upper band.

        The Donchian upper band = max(high) of the ``period`` bars immediately
        preceding the signal bar.  The signal bar itself is NOT included in the
        channel computation (closed-bar rule, SA-4 P1).

        Returns ``False`` (not a breakout) when there are fewer than
        ``period + 1`` bars, because there is insufficient history to form
        a valid channel.

        Args:
            bars: Sequence of closed bars in ascending ``open_time`` order.
                The last bar is the potential signal bar.
            period: Donchian lookback window.  Defaults to 20 per pre-reg §5.1.

        Returns:
            ``True`` if the last bar's close is strictly above the highest
            high of the prior ``period`` bars; ``False`` otherwise.
        """
        if len(bars) < period + 1:
            return False

        signal_bar = bars[-1]
        channel_bars = bars[-(period + 1) : -1]  # prior `period` bars only

        highest_high = max(b.high for b in channel_bars)
        return signal_bar.close > highest_high
