"""AtrEngine — Average True Range (ATR) using Wilder smoothing.

Implements ATR(period) with Wilder's Exponential Moving Average (EMA with
alpha = 1/period), consistent with the standard definition used by most
exchanges and backtesting frameworks.

Algorithm (Wilder's ATR):
    TR_i = max(high_i - low_i,
               |high_i - close_{i-1}|,
               |low_i  - close_{i-1}|)

    ATR_1   = arithmetic mean of first ``period`` TR values (seed)
    ATR_i   = ATR_{i-1} * (period-1)/period + TR_i * 1/period  (i > seed)

Domain purity: no I/O, no async.  stdlib + Decimal only.

Reference: spec-5-swing-strategy-adapter change.md.
pre-registration-v1.md §5.2 (Chandelier 22 3.0 uses ATR(22)).
pre-registration-v1.md §5.4 (sizing hint uses ATR(14)).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from hermes.domain.market_data.bar import Bar

__all__ = ["AtrEngine"]

_ZERO = Decimal("0")


def _true_range(bar: Bar, prev_close: Decimal) -> Decimal:
    """Compute True Range for a single bar given the previous close.

    Args:
        bar: The current bar.
        prev_close: Closing price of the immediately preceding bar.

    Returns:
        ``max(H-L, |H-prev_close|, |L-prev_close|)`` as a Decimal.
    """
    hl = bar.high - bar.low
    h_pc = abs(bar.high - prev_close)
    l_pc = abs(bar.low - prev_close)
    return max(hl, h_pc, l_pc)


class AtrEngine:
    """ATR indicator computed with Wilder's smoothing.

    All methods are stateless class methods — the engine does not hold
    bar history.  Callers (the adapter) supply the full bar slice.

    Reference: pre-registration-v1.md §5.2, §5.4.
    """

    @classmethod
    def value(cls, bars: Sequence[Bar], period: int = 14) -> Decimal | None:
        """Compute ATR(period) for the last bar in ``bars``.

        Requires at least ``period + 1`` bars (one extra for the initial TR
        seed because TR needs a previous close).  Returns ``None`` when the
        bar sequence is too short.

        Args:
            bars: Sequence of closed bars in ascending ``open_time`` order.
                The last element is the most recent bar.
            period: ATR smoothing period.  Defaults to 14 per pre-reg §5.4.

        Returns:
            ATR as a positive ``Decimal``, or ``None`` if insufficient history.
        """
        if len(bars) < period + 1:
            return None

        # Use only the last (period * 2 + 1) bars to keep computation bounded
        # while preserving Wilder seed accuracy for large lookbacks.
        window = bars[-(period * 2 + 1) :]

        # Compute all True Ranges (needs i>0 for prev_close)
        trs: list[Decimal] = [
            _true_range(window[i], window[i - 1].close) for i in range(1, len(window))
        ]

        if len(trs) < period:
            return None

        # Wilder seed: arithmetic mean of first `period` TRs
        atr = sum(trs[:period], _ZERO) / Decimal(period)

        # Wilder smoothing for subsequent TRs
        alpha_num = Decimal(period - 1)
        alpha_den = Decimal(period)
        tr_weight = Decimal(1)
        for tr in trs[period:]:
            atr = (atr * alpha_num + tr * tr_weight) / alpha_den

        return atr
