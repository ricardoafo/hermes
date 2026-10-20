"""EmaRegimeFilter — EMA(200) 1D regime filter.

Implements the EMA(200) closing-price filter using the standard exponential
moving average formula with smoothing factor alpha = 2 / (period + 1).

The filter checks whether the 1D market is in an uptrend: if the current
daily bar's close is above the 200-period EMA, the regime favours longs.

Closed-bar rule (SA-4 P1):
    Only fully closed 1D bars are accepted.  The caller (SwingStrategyAdapter)
    is responsible for only feeding the filter complete 1D bars — partial
    intra-day bars are never passed here.

Returns ``None`` when fewer than ``period`` bars are available.

Domain purity: no I/O, no async, stdlib + Decimal only.

Reference: pre-registration-v1.md §5.1 (EMA(200) 1D regime filter).
spec-5-swing-strategy-adapter change.md D-1, D-3.
"""

from collections.abc import Sequence
from decimal import Decimal

from hermes.domain.market_data.bar import Bar

__all__ = ["EmaRegimeFilter"]

_ZERO = Decimal("0")
_TWO = Decimal("2")


class EmaRegimeFilter:
    """1D EMA regime filter.

    All methods are stateless class methods.

    Reference: pre-registration-v1.md §5.1.
    """

    @classmethod
    def ema_value(cls, bars: Sequence[Bar], period: int = 200) -> Decimal | None:
        """Compute EMA(period) for the close of the last bar in ``bars``.

        Uses standard EMA with alpha = 2 / (period + 1).  Seeds with the
        arithmetic mean of the first ``period`` bars.

        Args:
            bars: Sequence of closed 1D bars in ascending ``open_time`` order.
            period: EMA period.  Defaults to 200 per pre-reg §5.1.

        Returns:
            EMA as a ``Decimal``, or ``None`` if fewer than ``period`` bars.
        """
        if len(bars) < period:
            return None

        alpha = _TWO / Decimal(period + 1)
        one_minus_alpha = Decimal("1") - alpha

        # Seed: SMA of first ``period`` closes
        window = bars[-min(len(bars), period * 3) :]  # bounded window
        seed_bars = window[:period]
        ema = sum((b.close for b in seed_bars), _ZERO) / Decimal(period)

        # Roll EMA over remaining bars
        for bar in window[period:]:
            ema = bar.close * alpha + ema * one_minus_alpha

        return ema

    @classmethod
    def above_ema(cls, bars: Sequence[Bar], period: int = 200) -> bool:
        """Return ``True`` when the last 1D bar's close is above EMA(period).

        Returns ``False`` when there are fewer than ``period`` bars (not
        enough history — regime filter is conservative: no signal unless
        we can confirm the regime).

        The last bar in ``bars`` is the most recently closed 1D bar.  Its
        close is compared against the EMA computed on the same bar sequence
        (the current bar IS included in the EMA because EMA(T) uses close(T)).

        Args:
            bars: Sequence of closed 1D bars in ascending ``open_time`` order.
            period: EMA period.  Defaults to 200 per pre-reg §5.1.

        Returns:
            ``True`` if the last bar's close > EMA(period), ``False`` otherwise.
        """
        ema = cls.ema_value(bars, period=period)
        if ema is None:
            return False
        return bars[-1].close > ema
