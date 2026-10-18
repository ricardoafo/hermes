"""TrendSlopeAdapter — research batch swing-b1, candidate C3 (``swing-b1-trend-slope``).

v2's 4h Donchian breakout with one more entry condition: the daily EMA(200)
at the last daily close is above its value ``ema_slope_lookback`` daily closes
earlier. Everything else, the exit included, is v2's (pre-registration-v2.md
§5.1, §5.2), so the adapter extends :class:`SwingStrategyAdapter` and only
replaces the entry check.

Free parameters (card ``swing-b1-trend-slope``, batch section C3):
``ema_slope_lookback`` 20, ``donchian_period`` 20, ``chandelier_atr_mult`` 3.0.
The other constructor arguments keep v2's values and are not varied.

Reference: docs/experiments/swing-mvp1/cards/swing-b1-trend-slope.md;
research batch swing-b1 §5 (C3).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from hermes.domain.strategy.swing.adapter import EntryCheck, SwingStrategyAdapter
from hermes.domain.strategy.swing.engines.ema import EmaRegimeFilter

__all__ = ["SlopeEntryCheck", "TrendSlopeAdapter"]


@dataclass(frozen=True, slots=True)
class SlopeEntryCheck(EntryCheck):
    """v2's three entry conditions plus the rising daily trend (C3).

    Fields:
        rising: The daily EMA(200) at the last daily close is above its value
            ``ema_slope_lookback`` daily closes earlier; ``False`` while the
            daily buffer is too short to hold both values.
    """

    rising: bool

    @property
    def all_met(self) -> bool:
        """Return True when all four conditions hold, the C3 entry rule."""
        return self.breakout and self.momentum and self.regime and self.rising


class TrendSlopeAdapter(SwingStrategyAdapter):
    """StrategyPort adapter for ``swing-b1-trend-slope``.

    Args:
        ema_slope_lookback: Daily closes between the two EMA values compared (C3 base 20).
        donchian_period: Donchian lookback in 4h bars (C3 base 20).
        adx_period: ADX period (v2 §5.1 = 14).
        adx_threshold: Minimum ADX for entry (v2 §5.1 = 25).
        ema_period: Daily EMA period (v2 §5.1 = 200).
        chandelier_period: Chandelier lookback and ATR period (v2 §5.2 = 22).
        chandelier_atr_mult: Chandelier ATR multiple (C3 base 3.0).
        time_stop_days: Calendar-day time stop (v2 §5.2 = 90).
        atr_sizing_period: ATR period carried on signals (v2 §5.4 = 14).

    Raises:
        ValueError: ``ema_slope_lookback`` is below 1.
    """

    def __init__(
        self,
        *,
        ema_slope_lookback: int = 20,
        donchian_period: int = 20,
        adx_period: int = 14,
        adx_threshold: Decimal = Decimal("25"),
        ema_period: int = 200,
        chandelier_period: int = 22,
        chandelier_atr_mult: Decimal = Decimal("3.0"),
        time_stop_days: int = 90,
        atr_sizing_period: int = 14,
    ) -> None:
        if ema_slope_lookback < 1:
            raise ValueError(f"ema_slope_lookback must be at least 1, got {ema_slope_lookback}")
        super().__init__(
            donchian_period=donchian_period,
            adx_period=adx_period,
            adx_threshold=adx_threshold,
            ema_period=ema_period,
            chandelier_period=chandelier_period,
            chandelier_atr_mult=chandelier_atr_mult,
            time_stop_days=time_stop_days,
            atr_sizing_period=atr_sizing_period,
        )
        self._ema_slope_lookback = ema_slope_lookback

    def _entry_check(self, as_of: datetime) -> SlopeEntryCheck:
        """Evaluate v2's three entry conditions and the rising trend (C3 §5)."""
        base = super()._entry_check(as_of)
        return SlopeEntryCheck(
            breakout=base.breakout,
            momentum=base.momentum,
            regime=base.regime,
            rising=self._trend_rising(as_of),
        )

    def _trend_rising(self, as_of: datetime) -> bool:
        """Return True when the daily EMA rose over the last ``ema_slope_lookback`` closes."""
        closed = self._closed_1d_bars(as_of)
        earlier = closed[: -self._ema_slope_lookback]
        # The EMA engine seeds from the oldest closes it is given. With the
        # pre-registered period 200 the 300-bar daily buffer fits its
        # 3 x period window, so both slices share one seed: the earlier value
        # is the same EMA series read ema_slope_lookback closes back, not a
        # re-seeded one.
        now = EmaRegimeFilter.ema_value(closed, period=self._ema_period)
        then = EmaRegimeFilter.ema_value(earlier, period=self._ema_period)
        return now is not None and then is not None and now > then
