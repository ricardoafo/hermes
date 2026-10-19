"""ChandelierEngine — Chandelier trailing stop (4h).

Implements the Chandelier Exit per pre-registration v1 §5.2:

    Chandelier Stop = highest_high(22, 4h) - atr_mult * ATR(22, 4h)

Where:
    - ``period`` = 22 bars (4h bars, binding per §5.2)
    - ``atr_mult`` = 3.0 (binding per §5.2)
    - ``highest_high(22)`` = max(high) of the last 22 bars INCLUDING the
      current bar (the Chandelier stop trails the position as price moves up)
    - ``ATR(22)`` computed with Wilder smoothing

Lookahead safety:
    The stop only uses bars up to and including the current signal bar.
    Callers pass the full bar buffer up to the current bar; no peeking.

No clamp:
    The level is not clamped under the close (issue #1157). A bar that opens
    at or under the stop in force exits the position: the backtest fills it
    from the open, and the shadow sells at the bar's close under the
    2026-10-18 §11 deviation in the lab notebook.

Domain purity: no I/O, no async, stdlib + Decimal only.

Reference: pre-registration-v1.md §5.2.
spec-5-swing-strategy-adapter change.md D-4.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from hermes.domain.market_data.bar import Bar
from hermes.domain.strategy.swing.engines.atr import AtrEngine

__all__ = ["ChandelierEngine"]

_DEFAULT_PERIOD = 22
_DEFAULT_ATR_MULT = Decimal("3.0")


class ChandelierEngine:
    """Chandelier trailing-stop engine.

    All methods are stateless class methods.

    Reference: pre-registration-v1.md §5.2 (Chandelier 22, 3.0).
    """

    @classmethod
    def trailing_stop(
        cls,
        bars: Sequence[Bar],
        period: int = _DEFAULT_PERIOD,
        atr_mult: Decimal = _DEFAULT_ATR_MULT,
    ) -> Decimal | None:
        """Compute the Chandelier trailing stop for the last bar in ``bars``.

        Returns ``None`` when there are not enough bars (fewer than
        ``period + 1``; AtrEngine needs ``period + 1`` bars).

        The trailing stop is:
            ``max(high[-period:]) - atr_mult * ATR(period)``

        Args:
            bars: Sequence of closed 4h bars in ascending ``open_time`` order.
                The last bar is the current signal bar.
            period: Lookback window for both highest-high and ATR.
                Defaults to 22 per pre-reg §5.2.
            atr_mult: ATR multiplier.  Defaults to 3.0 per pre-reg §5.2.

        Returns:
            Chandelier stop as a ``Decimal``, or ``None`` if insufficient bars.
        """
        atr = AtrEngine.value(bars, period=period)
        if atr is None:
            return None

        # highest_high over the last ``period`` bars INCLUDING the current bar
        lookback = bars[-period:]
        highest_high = max(b.high for b in lookback)

        stop = highest_high - atr_mult * atr
        return stop
