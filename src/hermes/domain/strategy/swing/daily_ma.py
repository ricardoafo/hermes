"""DailyMaAdapter: research batch swing-b1, candidate C2 (swing-b1-daily-ma).

Decisions are taken only on the 4h bar that closes at 00:00 UTC, from the
daily close that completed at the same instant.

Entry (long only), when flat: the daily close is above the daily
SMA(``sma_period``), and either the previous daily close was at or below its
SMA (a fresh cross) or the position was closed since the last daily close at
or below the SMA (re-entry after a stop exit).

Exit, first to fire:
    A. Protective stop: a 4h low at or below the stop in force when the bar
       opened. The stop is posted at entry as the signal bar's close minus
       ``stop_atr_mult`` x daily ATR(22). At each later UTC daily close it is
       recomputed as that close minus ``stop_atr_mult`` x ATR(22) and raised to
       that level if higher, never lowered.
    B. Moving-average break: on the 00:00 UTC 4h bar, the daily close is below
       the daily SMA(``sma_period``).

The two parameters are the batch's free parameters, named as in its
``parameters`` block; their defaults are the signed base values. The entry
signal's stop is the protective stop, so sizing uses its distance.

No-lookahead: only daily bars whose ``close_time`` is at or before the 4h
bar's ``close_time`` are read, whatever order they were delivered in.

Reference: research batch swing-b1 §C2 (ADR-026); card
``docs/experiments/swing-mvp1/cards/swing-b1-daily-ma.md``; issue #1064.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Final

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import interval_duration
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, SignalSide, StrategySignal
from hermes.domain.strategy.swing.engines.atr import AtrEngine

__all__ = ["ATR_PERIOD", "MA_BREAK", "PROTECTIVE_STOP", "DailyMaAdapter"]

ATR_PERIOD: Final = 22
"""The daily ATR the protective stop is measured in (batch §C2)."""

PROTECTIVE_STOP: Final = "protective_stop"
MA_BREAK: Final = "ma_break"

_1D_BUFFER_CAP: Final = 300
_MIN_SMA_PERIOD: Final = 2


class DailyMaAdapter:
    """Trade the daily close against its SMA, executed on the 00:00 UTC 4h bar.

    Args:
        sma_period: The SMA length in daily closes (base 100).
        stop_atr_mult: The protective stop distance in daily ATR(22)s (base 3.0).

    Raises:
        ValueError: ``sma_period`` below 2 or ``stop_atr_mult`` not positive.
    """

    def __init__(
        self,
        *,
        sma_period: int = 100,
        stop_atr_mult: Decimal = Decimal("3.0"),
    ) -> None:
        if sma_period < _MIN_SMA_PERIOD:
            raise ValueError(f"sma_period must be at least {_MIN_SMA_PERIOD}, got {sma_period}")
        if stop_atr_mult <= 0:
            raise ValueError(f"stop_atr_mult must be positive, got {stop_atr_mult}")
        self._sma_period = sma_period
        self._stop_atr_mult = stop_atr_mult
        self._bars_1d: list[Bar] = []
        self._in_position = False
        self._entry_time: datetime | None = None
        self._entry_price: Decimal | None = None
        self._stop_in_force: Decimal | None = None
        self._raised_through: datetime | None = None
        self._exited_since_below = False

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Process one closed bar; signal only on a 4h bar.

        Args:
            bar: A fully closed bar. Daily bars fill the buffer; other
                intervals but 4h are ignored.
            portfolio_state: Unused; the batch's caps are the kernel's.

        Returns:
            An entry or exit signal, or ``None``.
        """
        if bar.interval is KlineInterval.D1:
            self._append_daily(bar)
            return None
        if bar.interval is not KlineInterval.H4:
            return None
        if self._in_position:
            return self._evaluate_exit(bar)
        return self._evaluate_entry(bar)

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Fill the daily buffer from history without evaluating any bar.

        Args:
            bars: Closed bars in delivery order; only daily bars are kept.
            open_position: The position held, or ``None`` for flat.
        """
        for bar in bars:
            if bar.interval is KlineInterval.D1:
                self._append_daily(bar)
        self.restore_position(open_position)

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Set the position to what the portfolio holds; the buffer is untouched.

        The stop starts at the higher of ``open_position.last_stop`` and the
        entry price minus ``stop_atr_mult`` x ATR(22) of the daily closes by
        the entry, and every daily close since the entry raises it before the
        next bar is tested. So a retried exit or a restart holds the same stop
        the position had: each level is recomputed from the same daily bars.

        Args:
            open_position: The position held, or ``None`` for flat.
        """
        if open_position is None:
            self._in_position = False
            self._entry_time = None
            self._entry_price = None
            self._stop_in_force = None
            self._raised_through = None
            return
        stops = [
            open_position.last_stop,
            self._entry_stop(open_position.entry_price, open_position.entry_time),
        ]
        known = [s for s in stops if s is not None]
        self._raised_through = open_position.entry_time
        self._in_position = True
        self._entry_time = open_position.entry_time
        self._entry_price = open_position.entry_price
        self._stop_in_force = max(known) if known else None

    def _append_daily(self, bar: Bar) -> None:
        self._bars_1d.append(bar)
        if len(self._bars_1d) > _1D_BUFFER_CAP:
            self._bars_1d = self._bars_1d[-_1D_BUFFER_CAP:]

    def _closed_daily(self, as_of: datetime) -> list[Bar]:
        return [b for b in self._bars_1d if b.close_time <= as_of]

    def _sma(self, closes: Sequence[Decimal]) -> Decimal | None:
        if len(closes) < self._sma_period:
            return None
        window = closes[-self._sma_period :]
        return sum(window, Decimal(0)) / len(window)

    def _daily_decision(self, bar: Bar) -> tuple[bool, bool, bool] | None:
        """Return (above, below, previous at or below) for the daily close ending at ``bar``.

        ``None`` unless ``bar`` ends the UTC day, the daily bar closing at the
        same instant is buffered, and there is enough history for both SMAs.
        """
        # Exchange bars close 1 ms before the boundary, so the day end is open_time + interval.
        end = bar.open_time + interval_duration(bar.interval)
        if end != end.replace(hour=0, minute=0, second=0, microsecond=0):
            return None
        daily = self._closed_daily(bar.close_time)
        if not daily or daily[-1].close_time != bar.close_time:
            return None
        closes = [b.close for b in daily]
        sma_now = self._sma(closes)
        sma_before = self._sma(closes[:-1])
        if sma_now is None or sma_before is None:
            return None
        close, previous = closes[-1], closes[-2]
        return close > sma_now, close < sma_now, previous <= sma_before

    def _daily_atr(self, as_of: datetime) -> Decimal | None:
        return AtrEngine.value(self._closed_daily(as_of), period=ATR_PERIOD)

    def _entry_stop(self, reference: Decimal, as_of: datetime) -> Decimal | None:
        atr = self._daily_atr(as_of)
        return None if atr is None else reference - self._stop_atr_mult * atr

    def _evaluate_entry(self, bar: Bar) -> StrategySignal | None:
        decision = self._daily_decision(bar)
        if decision is None:
            return None
        above, _, previous_at_or_below = decision
        if not above:
            # This close is at or below the SMA: an exit before it no longer allows a re-entry.
            self._exited_since_below = False
            return None
        if not (previous_at_or_below or self._exited_since_below):
            return None
        atr = self._daily_atr(bar.close_time)
        if atr is None:
            return None
        stop = bar.close - self._stop_atr_mult * atr
        if stop >= bar.close or stop <= 0:
            return None
        self._in_position = True
        self._entry_time = bar.close_time
        self._entry_price = bar.close
        self._stop_in_force = stop
        self._raised_through = bar.close_time
        self._exited_since_below = False
        return StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=bar.close,
            stop_price=stop,
            atr_context=atr,
            signal_time=bar.close_time,
        )

    def _evaluate_exit(self, bar: Bar) -> StrategySignal | None:
        # The stop tested on this bar is the one in force when it opened.
        self._raise_stop(through=bar.open_time)
        stop = self._stop_in_force
        reason: str | None = None
        if stop is not None and bar.low <= stop:
            reason = PROTECTIVE_STOP
        else:
            decision = self._daily_decision(bar)
            if decision is not None and decision[1]:
                reason = MA_BREAK
        if reason is None:
            self._raise_stop(through=bar.close_time)
            return None
        atr = self._daily_atr(bar.close_time)
        entry_price = self._entry_price
        self.restore_position(None)
        self._exited_since_below = True
        return StrategySignal(
            kind=SignalKind.EXIT,
            side=SignalSide.LONG,
            entry_price=entry_price,
            stop_price=stop if stop is not None else bar.close,
            atr_context=atr if atr is not None else Decimal(1),
            signal_time=bar.close_time,
            exit_reason=reason,
        )

    def _raise_stop(self, *, through: datetime) -> None:
        """Raise the stop to each daily close's level, for daily closes up to ``through``."""
        since = self._raised_through
        for daily_index, daily in enumerate(self._bars_1d):
            if daily.close_time > through or (since is not None and daily.close_time <= since):
                continue
            closed = self._bars_1d[: daily_index + 1]
            atr = AtrEngine.value(closed, period=ATR_PERIOD)
            if atr is None:
                continue
            level = daily.close - self._stop_atr_mult * atr
            current = self._stop_in_force
            self._stop_in_force = level if current is None else max(current, level)
        if since is None or through > since:
            self._raised_through = through
