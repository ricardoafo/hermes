"""RsiEngine — Relative Strength Index (RSI) using Wilder smoothing.

Algorithm (Wilder's RSI):
    change_i = close_i - close_{i-1}
    gain_i   = max(change_i, 0);  loss_i = max(-change_i, 0)

    avg_1    = arithmetic mean of the first ``period`` gains (losses) (seed)
    avg_i    = avg_{i-1} * (period-1)/period + gain_i (loss_i) / period

    RSI      = 100 - 100 / (1 + avg_gain / avg_loss)

The window is the last ``2 * period + 1`` bars, as in :class:`AtrEngine`, so
the value depends only on a fixed number of recent bars: a caller seeded with
more history computes the same value as one seeded with exactly the warm-up.

Domain purity: no I/O, no async. stdlib + Decimal only.

Reference: research batch swing-b1 §5 (C4), card swing-b1-pullback.
"""

from collections.abc import Sequence
from decimal import Decimal

from hermes.domain.market_data.bar import Bar

__all__ = ["RsiEngine"]

_ZERO = Decimal("0")
_FIFTY = Decimal("50")
_HUNDRED = Decimal("100")


class RsiEngine:
    """RSI indicator computed with Wilder's smoothing.

    All methods are stateless class methods; the caller supplies the bars.
    """

    @classmethod
    def value(cls, bars: Sequence[Bar], period: int = 14) -> Decimal | None:
        """Compute RSI(period) at the close of the last bar in ``bars``.

        Args:
            bars: Closed bars in ascending ``open_time`` order.
            period: Smoothing period.

        Returns:
            RSI in [0, 100], or ``None`` with fewer than ``period + 1`` bars.
            A window with no losses is 100, with no gains 0, and with neither 50.
        """
        if len(bars) < period + 1:
            return None
        window = bars[-(period * 2 + 1) :]
        changes = [window[i].close - window[i - 1].close for i in range(1, len(window))]
        gains = [max(change, _ZERO) for change in changes]
        losses = [max(-change, _ZERO) for change in changes]

        avg_gain = sum(gains[:period], _ZERO) / Decimal(period)
        avg_loss = sum(losses[:period], _ZERO) / Decimal(period)
        keep = Decimal(period - 1)
        for gain, loss in zip(gains[period:], losses[period:], strict=True):
            avg_gain = (avg_gain * keep + gain) / Decimal(period)
            avg_loss = (avg_loss * keep + loss) / Decimal(period)

        if avg_loss == _ZERO:
            return _FIFTY if avg_gain == _ZERO else _HUNDRED
        return _HUNDRED - _HUNDRED / (1 + avg_gain / avg_loss)
