"""AdxEngine — Average Directional Index (ADX) using Wilder smoothing.

Implements ADX(period) following Wilder's original definition:

    +DM_i = high_i - high_{i-1}   if (up_move > down_move) AND (up_move > 0) else 0
    -DM_i = low_{i-1} - low_i     if (down_move > up_move) AND (down_move > 0) else 0

    Wilder-smoothed (alpha = 1/period, using Wilder's accumulation form):
        SmoothedX(i) = SmoothedX(i-1) - SmoothedX(i-1)/period + RawX(i)
        [Initial seed = arithmetic sum of first ``period`` raw values]

    +DI = 100 * Smoothed(+DM) / Smoothed(TR)
    -DI = 100 * Smoothed(-DM) / Smoothed(TR)
    DX  = 100 * |+DI - -DI| / (+DI + -DI)         -- bounded [0, 100]

    ADX = Wilder average of DX values:
        ADX seed = arithmetic mean of first ``period`` DX values
        ADX(i)   = [ADX(i-1) * (period - 1) + DX(i)] / period   -- bounded [0, 100]

Key distinction:
    - Smoothed(+DM), Smoothed(-DM), Smoothed(TR) use the accumulation form
      (seed = sum; update = prev - prev/p + new). This is NOT bounded.
    - ADX uses the weighted-average form (seed = mean; update = (prev*(p-1)+new)/p).
      This IS bounded to [0, 100] since DX ∈ [0, 100].

Requires at least ``2 *period + 1`` bars to produce an ADX reading
(``period + 1`` bars for the directional movement seed, then ``period``
more bars to seed the DX average from which ADX is derived).

Returns ``None`` when fewer than ``2 *period + 1`` bars are available.

Domain purity: no I/O, no async, stdlib + Decimal only.

Reference: pre-registration-v1.md §5.1 (ADX(14) ≥ 25, 4h).
spec-5-swing-strategy-adapter change.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from hermes.domain.market_data.bar import Bar

__all__ = ["AdxEngine"]

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def _compute_dx(
    smooth_plus_dm: Decimal,
    smooth_minus_dm: Decimal,
    smooth_tr: Decimal,
) -> Decimal | None:
    """Compute DX from smoothed directional movement and TR values.

    Returns ``None`` when ``smooth_tr`` is zero (degenerate bars).
    Returns value in [0, 100].
    """
    if smooth_tr == _ZERO:
        return None
    plus_di = _HUNDRED * smooth_plus_dm / smooth_tr
    minus_di = _HUNDRED * smooth_minus_dm / smooth_tr
    denom = plus_di + minus_di
    if denom == _ZERO:
        return _ZERO
    return _HUNDRED * abs(plus_di - minus_di) / denom


class AdxEngine:
    """ADX indicator engine using Wilder's smoothing.

    All methods are stateless class methods.

    Reference: pre-registration-v1.md §5.1 (ADX(14) ≥ 25).
    """

    @classmethod
    def value(cls, bars: Sequence[Bar], period: int = 14) -> Decimal | None:
        """Compute ADX(period) for the last bar in ``bars``.

        Requires at least ``2 * period + 1`` bars.  Returns ``None`` when
        the sequence is too short.

        Args:
            bars: Sequence of closed bars in ascending ``open_time`` order.
            period: ADX smoothing period.  Defaults to 14 per pre-reg §5.1.

        Returns:
            ADX as a ``Decimal`` in [0, 100], or ``None`` if insufficient history.
        """
        min_bars = 2 * period + 1
        if len(bars) < min_bars:
            return None

        # Use a bounded window to keep computation O(period)
        window = list(bars[-(min_bars + period) :])

        p = Decimal(period)

        # ── Step 1: compute raw +DM, -DM, TR for all consecutive bar pairs ──
        plus_dms: list[Decimal] = []
        minus_dms: list[Decimal] = []
        trs: list[Decimal] = []

        for i in range(1, len(window)):
            curr = window[i]
            prev = window[i - 1]

            up_move = curr.high - prev.high
            down_move = prev.low - curr.low

            plus_dm = up_move if (up_move > down_move and up_move > _ZERO) else _ZERO
            minus_dm = down_move if (down_move > up_move and down_move > _ZERO) else _ZERO

            hl = curr.high - curr.low
            h_pc = abs(curr.high - prev.close)
            l_pc = abs(curr.low - prev.close)
            tr = max(hl, h_pc, l_pc)

            plus_dms.append(plus_dm)
            minus_dms.append(minus_dm)
            trs.append(tr)

        if len(plus_dms) < 2 * period:
            return None

        # ── Step 2: Wilder seed for +DM, -DM, TR ──
        # Seed = arithmetic SUM (not mean) of first ``period`` raw values.
        # Wilder's accumulation form: S(i) = S(i-1) - S(i-1)/p + raw(i)
        smooth_plus_dm = sum(plus_dms[:period], _ZERO)
        smooth_minus_dm = sum(minus_dms[:period], _ZERO)
        smooth_tr = sum(trs[:period], _ZERO)

        # ── Step 3: compute DX for bars period..2*period to seed ADX ──
        dx_seed_list: list[Decimal] = []

        for i in range(period, 2 * period):
            smooth_plus_dm = smooth_plus_dm - smooth_plus_dm / p + plus_dms[i]
            smooth_minus_dm = smooth_minus_dm - smooth_minus_dm / p + minus_dms[i]
            smooth_tr = smooth_tr - smooth_tr / p + trs[i]
            dx = _compute_dx(smooth_plus_dm, smooth_minus_dm, smooth_tr)
            if dx is not None:
                dx_seed_list.append(dx)

        if len(dx_seed_list) < period:
            return None

        # ── Step 4: ADX seed = arithmetic MEAN of first ``period`` DX values ──
        adx = sum(dx_seed_list[:period], _ZERO) / p

        # ── Step 5: smooth ADX over any extra DX values collected above ──
        # (when window is larger than 2*period, dx_seed_list may have >period entries)
        for dx_val in dx_seed_list[period:]:
            adx = (adx * (p - Decimal(1)) + dx_val) / p

        # ── Step 6: continue smoothing over remaining raw bars ──
        for i in range(2 * period, len(plus_dms)):
            smooth_plus_dm = smooth_plus_dm - smooth_plus_dm / p + plus_dms[i]
            smooth_minus_dm = smooth_minus_dm - smooth_minus_dm / p + minus_dms[i]
            smooth_tr = smooth_tr - smooth_tr / p + trs[i]
            dx = _compute_dx(smooth_plus_dm, smooth_minus_dm, smooth_tr)
            if dx is not None:
                adx = (adx * (p - Decimal(1)) + dx) / p

        return adx
