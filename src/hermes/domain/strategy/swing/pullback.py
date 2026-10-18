"""PullbackAdapter — research batch swing-b1, candidate C4 (``swing-b1-pullback``).

Buys a 4h oversold pullback while the daily trend is up (batch section C4 §5):

Trend, at the last daily close:
    The daily close is above the daily EMA(200), and the daily EMA(200) is
    above its value 20 daily closes earlier.

Entry (long only), on a 4h bar close while the trend holds:
    RSI(14) on 4h closes at or above ``rsi_reentry`` after the previous 4h
    bar's RSI(14) closed below it. Market order at the signal bar's close.

Exit, first to fire (checked in this order on each 4h bar after the entry):
    A. RSI exit: 4h RSI(14) closes above 70.
    B. Protective stop: the 4h low is at or below the stop, set at the signal
       bar's close minus ``stop_atr_mult`` x 4h ATR(22) and fixed for the trade.
    C. Time stop: the 4h bar closing at or after ``time_stop_days`` calendar
       days from the entry bar's close.
    D. Regime break: the last daily close is below the daily EMA(200).

Every exit is a market order at the exit bar's close (``exit_pricing:
bar_close``); the order above only decides the reason a signal carries.

Multi-timeframe handling and the no-lookahead rule are those of
:class:`~hermes.domain.strategy.swing.adapter.SwingStrategyAdapter`: 1D bars
update the daily buffer silently, and a daily bar counts only once a 4h bar
closing at or after it is evaluated.

Free parameters (card ``swing-b1-pullback``): ``rsi_reentry`` 35,
``stop_atr_mult`` 2.0, ``time_stop_days`` 20. The other constructor arguments
are fixed by the card and are not varied.

Domain purity: no I/O, no async.

Reference: docs/experiments/swing-mvp1/cards/swing-b1-pullback.md;
research batch swing-b1 §5 (C4).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, SignalSide, StrategySignal
from hermes.domain.strategy.swing.engines.atr import AtrEngine
from hermes.domain.strategy.swing.engines.ema import EmaRegimeFilter
from hermes.domain.strategy.swing.engines.rsi import RsiEngine

__all__ = ["PullbackAdapter", "PullbackEntryCheck"]

# Same caps as SwingStrategyAdapter, so a 300-bar daily warm-up seeds the
# EMA identically in serve and in the backtest (#880).
_4H_BUFFER_CAP = 300
_1D_BUFFER_CAP = 300

_RSI_MAX = 100


@dataclass(frozen=True, slots=True)
class PullbackEntryCheck:
    """Which of the two C4 entry conditions held on one 4h bar.

    Booleans only, so the record stays outcome-blind (ADR-012 D-06).

    Fields:
        trend: The daily close is above a daily EMA(200) that rose over the
            trend lookback.
        reentry: RSI crossed back to ``rsi_reentry`` or above on this bar.
    """

    trend: bool
    reentry: bool

    @property
    def all_met(self) -> bool:
        """Return True when both conditions hold, the C4 entry rule."""
        return self.trend and self.reentry


class PullbackAdapter:
    """StrategyPort adapter for ``swing-b1-pullback``.

    Args:
        rsi_reentry: RSI level the 4h RSI must cross back to (C4 base 35).
        stop_atr_mult: Protective stop distance in 4h ATRs (C4 base 2.0).
        time_stop_days: Calendar days from the entry bar's close (C4 base 20).
        rsi_period: RSI period on 4h (card: 14).
        rsi_exit: RSI level above which the position exits (card: 70).
        stop_atr_period: ATR period of the protective stop (card: 22).
        ema_period: Daily EMA period (card: 200).
        trend_lookback: Daily closes between the two EMA values compared (card: 20).
        atr_sizing_period: ATR period carried on signals as ``atr_context``
            (v2 §5.4 = 14); sizing itself uses the stop distance.

    Raises:
        ValueError: A period, the lookback, the time stop or the stop
            multiple is not positive, or ``rsi_reentry`` is not below ``rsi_exit``.
    """

    def __init__(
        self,
        *,
        rsi_reentry: int = 35,
        stop_atr_mult: Decimal = Decimal("2.0"),
        time_stop_days: int = 20,
        rsi_period: int = 14,
        rsi_exit: int = 70,
        stop_atr_period: int = 22,
        ema_period: int = 200,
        trend_lookback: int = 20,
        atr_sizing_period: int = 14,
    ) -> None:
        positive = {
            "time_stop_days": time_stop_days,
            "rsi_period": rsi_period,
            "stop_atr_period": stop_atr_period,
            "ema_period": ema_period,
            "trend_lookback": trend_lookback,
            "atr_sizing_period": atr_sizing_period,
        }
        for name, value in positive.items():
            if value < 1:
                raise ValueError(f"{name} must be at least 1, got {value}")
        if stop_atr_mult <= 0:
            raise ValueError(f"stop_atr_mult must be positive, got {stop_atr_mult}")
        if not 0 < rsi_reentry < rsi_exit <= _RSI_MAX:
            raise ValueError(
                f"need 0 < rsi_reentry < rsi_exit <= 100, got {rsi_reentry} and {rsi_exit}"
            )
        self._rsi_reentry = Decimal(rsi_reentry)
        self._stop_atr_mult = stop_atr_mult
        self._time_stop = timedelta(days=time_stop_days)
        self._rsi_period = rsi_period
        self._rsi_exit = Decimal(rsi_exit)
        self._stop_atr_period = stop_atr_period
        self._ema_period = ema_period
        self._trend_lookback = trend_lookback
        self._atr_sizing_period = atr_sizing_period

        self._bars_4h: list[Bar] = []
        self._bars_1d: list[Bar] = []

        self._entry_time: datetime | None = None
        self._entry_price: Decimal | None = None
        self._stop: Decimal | None = None
        self._last_entry_check: PullbackEntryCheck | None = None

    @property
    def last_entry_check(self) -> PullbackEntryCheck | None:
        """Return the entry evaluation of the latest bar, or ``None``.

        ``None`` when the latest bar was a daily bar, was evaluated for an exit,
        or no bar has been evaluated yet. Nothing in the signal path reads it.
        """
        return self._last_entry_check

    # ------------------------------------------------------------------
    # StrategyPort implementation
    # ------------------------------------------------------------------

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Process one closed bar and optionally emit a trading signal.

        Args:
            bar: A fully closed OHLCV bar; intervals other than 4h and 1D are ignored.
            portfolio_state: Current portfolio snapshot; unused by C4.

        Returns:
            An entry or exit signal, or ``None``.
        """
        self._last_entry_check = None
        if bar.interval == KlineInterval.H4:
            self._append_4h(bar)
            if self._entry_time is None:
                return self._evaluate_entry(bar)
            return self._evaluate_exit(bar)
        if bar.interval == KlineInterval.D1:
            self._append_1d(bar)
        return None

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Fill the bar buffers from history without evaluating any bar.

        Args:
            bars: Closed bars in delivery order; other intervals are ignored.
            open_position: Position to restore, or ``None`` for flat.
        """
        for bar in bars:
            if bar.interval == KlineInterval.H4:
                self._append_4h(bar)
            elif bar.interval == KlineInterval.D1:
                self._append_1d(bar)
        self.restore_position(open_position)

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Set the position state to what the portfolio holds; buffers untouched.

        The protective stop is fixed at the entry, so it is restored rather
        than recomputed from today's bars: the stop this adapter set for the
        same entry, else the one recomputed from the buffered entry bar, else
        ``open_position.last_stop``. With none of them the stop exit cannot
        fire and the other exits still apply.

        Args:
            open_position: Position held, or ``None`` for flat.
        """
        if open_position is None:
            self._entry_time = None
            self._entry_price = None
            self._stop = None
            return
        same_entry = self._entry_time == open_position.entry_time and self._stop is not None
        stop = self._stop if same_entry else self._stop_from_entry_bar(open_position.entry_time)
        self._entry_time = open_position.entry_time
        self._entry_price = open_position.entry_price
        self._stop = stop if stop is not None else open_position.last_stop

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_4h(self, bar: Bar) -> None:
        self._bars_4h.append(bar)
        if len(self._bars_4h) > _4H_BUFFER_CAP:
            self._bars_4h = self._bars_4h[-_4H_BUFFER_CAP:]

    def _append_1d(self, bar: Bar) -> None:
        self._bars_1d.append(bar)
        if len(self._bars_1d) > _1D_BUFFER_CAP:
            self._bars_1d = self._bars_1d[-_1D_BUFFER_CAP:]

    def _closed_1d_bars(self, as_of: datetime) -> list[Bar]:
        return [b for b in self._bars_1d if b.close_time <= as_of]

    def _trend_up(self, as_of: datetime) -> bool:
        """Return True when the last daily close is above a rising daily EMA."""
        closed = self._closed_1d_bars(as_of)
        if not closed:
            return False
        # Both values come from one EMA series: the engine seeds from the
        # oldest closes it is given and the 300-bar buffer fits its
        # 3 x 200 window, so the slice shares the seed.
        now = EmaRegimeFilter.ema_value(closed, period=self._ema_period)
        then = EmaRegimeFilter.ema_value(closed[: -self._trend_lookback], period=self._ema_period)
        if now is None or then is None:
            return False
        return closed[-1].close > now > then

    def _below_ema(self, as_of: datetime) -> bool:
        """Return True when the last daily close is below the daily EMA."""
        closed = self._closed_1d_bars(as_of)
        if not closed:
            return False
        ema = EmaRegimeFilter.ema_value(closed, period=self._ema_period)
        return ema is not None and closed[-1].close < ema

    def _reentry(self) -> bool:
        """Return True when this bar's RSI crossed back to the re-entry level."""
        now = RsiEngine.value(self._bars_4h, period=self._rsi_period)
        before = RsiEngine.value(self._bars_4h[:-1], period=self._rsi_period)
        if now is None or before is None:
            return False
        return before < self._rsi_reentry <= now

    def _stop_level(self, bars: Sequence[Bar]) -> Decimal | None:
        """Return the protective stop for an entry at the last bar's close."""
        atr = AtrEngine.value(bars, period=self._stop_atr_period)
        if atr is None:
            return None
        return bars[-1].close - self._stop_atr_mult * atr

    def _stop_from_entry_bar(self, entry_time: datetime) -> Decimal | None:
        """Recompute the protective stop from the buffered entry bar, if it is held."""
        for index, bar in enumerate(self._bars_4h):
            if bar.close_time == entry_time:
                return self._stop_level(self._bars_4h[: index + 1])
        return None

    def _evaluate_entry(self, bar: Bar) -> StrategySignal | None:
        check = PullbackEntryCheck(trend=self._trend_up(bar.close_time), reentry=self._reentry())
        self._last_entry_check = check
        if not check.all_met:
            return None
        stop = self._stop_level(self._bars_4h)
        atr = AtrEngine.value(self._bars_4h, period=self._atr_sizing_period)
        if stop is None or atr is None or atr <= 0 or not 0 < stop < bar.close:
            return None

        self._entry_time = bar.close_time
        self._entry_price = bar.close
        self._stop = stop
        return StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=bar.close,
            stop_price=stop,
            atr_context=atr,
            signal_time=bar.close_time,
            exit_reason=None,
            f_star_estimate=None,
        )

    def _evaluate_exit(self, bar: Bar) -> StrategySignal | None:
        entry_time = self._entry_time
        assert entry_time is not None, (  # noqa: S101 — invariant
            "PullbackAdapter: an exit is evaluated only while a position is held"
        )
        rsi = RsiEngine.value(self._bars_4h, period=self._rsi_period)
        stop = self._stop

        reason: str | None = None
        if rsi is not None and rsi > self._rsi_exit:
            reason = "rsi_exit"
        elif stop is not None and bar.low <= stop:
            reason = "stop"
        elif bar.close_time >= entry_time + self._time_stop:
            reason = "time_stop"
        elif self._below_ema(bar.close_time):
            reason = "regime_break"
        if reason is None:
            return None

        atr = AtrEngine.value(self._bars_4h, period=self._atr_sizing_period)
        if atr is None or atr <= 0:
            atr = Decimal("1")
        entry_price = self._entry_price
        self._entry_time = None
        self._entry_price = None
        self._stop = None
        return StrategySignal(
            kind=SignalKind.EXIT,
            side=SignalSide.LONG,
            entry_price=entry_price,
            stop_price=stop if stop is not None else bar.close - atr,
            atr_context=atr,
            signal_time=bar.close_time,
            exit_reason=reason,
            f_star_estimate=None,
        )
