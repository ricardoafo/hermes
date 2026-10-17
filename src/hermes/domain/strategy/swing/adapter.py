"""SwingStrategyAdapter — StrategyPort implementation for swing-MVP1.

Coordinates five technical engines against pre-registration v1 §5 parameters
to produce entry and exit signals for the swing-MVP1 paper-trading experiment.

Entry conditions (ALL three must fire simultaneously on 4h bar close, §5.1):
    1. Donchian(20) breakout: bar.close > highest_high(prior 20 bars, 4h)
    2. ADX(14) ≥ 25 (4h)
    3. 1D close > EMA(200) (last fully closed 1D bar)

Exit conditions (first triggered wins, §5.2):
    A. Chandelier stop: current 4h bar's low <= the stop in force. The stop is
       posted at the entry fill and moved at each daily close to the
       highest_high(22) - 3*ATR(22) level of the 4h bars closed by then; it
       never moves down and the bar it is tested on never moves it (§5.3, #785)
    B. Time stop: 90 calendar days from entry bar close_time
    C. Regime break: 1D close < EMA(200) on a new 1D bar

Order type (§5.3):
    Entry: market order at bar close of signal bar (entry_price = bar.close)
    Exit: on-exchange stop-market at the stop in force; the fill price (the
    lower of the stop and the bar's open) is the execution model's job

Multi-timeframe coordination (D-1):
    - ``on_bar`` dispatches by ``bar.interval``.
    - 4h bars feed the Donchian / ADX / Chandelier / ATR engines.
    - 1D bars update the EMA(200) regime buffer.
    - Signal evaluation only fires on 4h bar arrival.
    - 1D bar arrival updates the 1D buffer silently (returns ``None``).

No-lookahead contract (SA-4 P1, R-8):
    Signal evaluation only reads bars whose ``close_time`` ≤ the current 4h
    bar's ``close_time``.  The current signal bar IS included in the 4h buffer
    before evaluation (the bar has already closed).  The adapter enforces this
    for the 1D buffer itself rather than trusting delivery order: a 1D bar
    that arrives before it has closed relative to the signal bar (e.g. a
    caller merging streams by ``open_time``) stays invisible to the regime
    filter until a 4h bar closing at or after it is evaluated (issue #311).

State transitions:
    - FLAT → LONG: entry signal
    - LONG → FLAT: exit signal (chandelier / time-stop / regime-break)
    - LONG → LONG: n/a (only one concurrent position per pair in MVP1)

Domain purity: no I/O, no async.  Depends only on domain value objects
and engine modules.

Reference: pre-registration-v1.md §5 (all subsections).
spec-5-swing-strategy-adapter change.md D-1 through D-4.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import interval_duration
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, SignalSide, StrategySignal
from hermes.domain.strategy.stop_mark import StopRecord
from hermes.domain.strategy.swing.engines.adx import AdxEngine
from hermes.domain.strategy.swing.engines.atr import AtrEngine
from hermes.domain.strategy.swing.engines.chandelier import ChandelierEngine
from hermes.domain.strategy.swing.engines.donchian import DonchianEngine
from hermes.domain.strategy.swing.engines.ema import EmaRegimeFilter

__all__ = ["EntryCheck", "SwingStrategyAdapter", "swing_parameters"]

# Pre-reg §5.1 parameters
_DONCHIAN_PERIOD = 20
_ADX_PERIOD = 14
_ADX_THRESHOLD = Decimal("25")
_EMA_PERIOD = 200

# Pre-reg §5.2 parameters
_CHANDELIER_PERIOD = 22
_CHANDELIER_ATR_MULT = Decimal("3.0")
_TIME_STOP_DAYS = 90

# ATR sizing helper (§5.4)
_ATR_SIZING_PERIOD = 14

# Buffer size cap — keep only as much history as the longest lookback needs
# Chandelier = 22+1 bars minimum; EMA = 200+1 bars minimum;
# ADX = 2*14+1 = 29 bars minimum.  Add a margin of 50.
# The EMA is seeded from the oldest buffered 1d closes, so its value depends
# on warm-up length until the 1d buffer is full: every 1d warm-up floor is
# _1D_BUFFER_CAP, not the EMA period (#880).
_4H_BUFFER_CAP = 300
_1D_BUFFER_CAP = 300


@dataclass(frozen=True, slots=True)
class EntryCheck:
    """Which of the three §5.1 entry conditions held on one 4h bar.

    Booleans only: the record explains "why no signal" without carrying a
    price or an indicator value, so it stays outcome-blind (ADR-012 D-06).

    Fields:
        breakout: The close is above the highest high of the prior 20 bars.
        momentum: ADX(14) is at least 25; ``False`` while ADX has too little history.
        regime: The last daily close is above its EMA(200).
    """

    breakout: bool
    momentum: bool
    regime: bool

    @property
    def all_met(self) -> bool:
        """Return True when all three conditions hold, the §5.1 entry rule."""
        return self.breakout and self.momentum and self.regime


def swing_parameters() -> dict[str, str]:
    """Return the pre-registered §5 parameters the adapter trades with.

    The certification gates fingerprint this mapping: changing any value here
    is a §11 deviation and sends the strategy back to UNTRUSTED (D-8 guardrail).

    Returns:
        Parameter name to its value rendered as a string.
    """
    return {
        "donchian_period": str(_DONCHIAN_PERIOD),
        "adx_period": str(_ADX_PERIOD),
        "adx_threshold": str(_ADX_THRESHOLD),
        "ema_period": str(_EMA_PERIOD),
        "chandelier_period": str(_CHANDELIER_PERIOD),
        "chandelier_atr_mult": str(_CHANDELIER_ATR_MULT),
        "time_stop_days": str(_TIME_STOP_DAYS),
        "atr_sizing_period": str(_ATR_SIZING_PERIOD),
    }


class SwingStrategyAdapter:
    """StrategyPort adapter implementing the swing-MVP1 strategy.

    Maintains two internal bar buffers — ``_bars_4h`` and ``_bars_1d`` —
    and routes incoming bars by interval.  Entry/exit evaluation fires only
    on 4h bar arrival.

    Args:
        donchian_period: Donchian lookback (pre-reg §5.1 = 20).
        adx_period: ADX period (pre-reg §5.1 = 14).
        adx_threshold: Minimum ADX for entry (pre-reg §5.1 = 25).
        ema_period: EMA regime filter period (pre-reg §5.1 = 200).
        chandelier_period: Chandelier lookback + ATR period (pre-reg §5.2 = 22).
        chandelier_atr_mult: ATR multiplier (pre-reg §5.2 = 3.0).
        time_stop_days: Calendar-day time stop (pre-reg §5.2 = 90).

    Reference: pre-registration-v1.md §5.
    """

    def __init__(
        self,
        *,
        donchian_period: int = _DONCHIAN_PERIOD,
        adx_period: int = _ADX_PERIOD,
        adx_threshold: Decimal = _ADX_THRESHOLD,
        ema_period: int = _EMA_PERIOD,
        chandelier_period: int = _CHANDELIER_PERIOD,
        chandelier_atr_mult: Decimal = _CHANDELIER_ATR_MULT,
        time_stop_days: int = _TIME_STOP_DAYS,
        atr_sizing_period: int = _ATR_SIZING_PERIOD,
    ) -> None:
        self._donchian_period = donchian_period
        self._adx_period = adx_period
        self._adx_threshold = adx_threshold
        self._ema_period = ema_period
        self._chandelier_period = chandelier_period
        self._chandelier_atr_mult = chandelier_atr_mult
        self._time_stop_days = time_stop_days
        self._atr_sizing_period = atr_sizing_period

        # Bar history buffers — ascending open_time order
        self._bars_4h: list[Bar] = []
        self._bars_1d: list[Bar] = []

        # Position state
        self._in_position: bool = False
        self._entry_time: datetime | None = None
        self._entry_price: Decimal | None = None
        self._last_entry_check: EntryCheck | None = None
        self._stop_in_force: Decimal | None = None
        self._posted_level: Decimal | None = None
        self._posted_atr: Decimal | None = None
        self._posted_through: datetime | None = None
        self._last_stop_record: StopRecord | None = None

    @property
    def last_entry_check(self) -> EntryCheck | None:
        """Return the entry evaluation of the latest bar, or ``None``.

        ``None`` when the latest bar was a daily bar, was evaluated for an exit
        (a position was open), or no bar has been evaluated yet. Read-only: nothing in
        the signal path reads it back (#656).
        """
        return self._last_entry_check

    @property
    def last_stop_record(self) -> StopRecord | None:
        """Return the stop evaluated on the latest bar while a position was held, or ``None``.

        Set on the entry bar, on each 4h bar while the position is open, and on
        the exit bar. ``None`` on daily bars, while flat, and on a held bar
        whose chandelier stop has too little history. Read-only: nothing in the
        signal path reads it back (#388).
        """
        return self._last_stop_record

    # ------------------------------------------------------------------
    # StrategyPort implementation
    # ------------------------------------------------------------------

    def on_bar(
        self,
        bar: Bar,
        portfolio_state: PortfolioState,
    ) -> StrategySignal | None:
        """Process one closed bar and optionally emit a trading signal.

        Dispatches by ``bar.interval``:
        - ``H4``: update 4h buffer, evaluate entry/exit.
        - ``D1``: update 1D buffer, return ``None`` (regime update only).
        - Other intervals: ignored, return ``None``.

        Args:
            bar: A fully closed OHLCV bar.
            portfolio_state: Current portfolio snapshot (not used in MVP1
                beyond the correlation check which is deferred to SA-9).

        Returns:
            A :class:`~hermes.domain.strategy.signal.StrategySignal` on
            entry or exit, or ``None``.
        """
        self._last_entry_check = None
        self._last_stop_record = None
        if bar.interval == KlineInterval.H4:
            return self._on_4h_bar(bar)
        if bar.interval == KlineInterval.D1:
            self._update_1d_buffer(bar)
            return None
        # Unknown interval — ignore
        return None

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Fill the bar buffers from history without evaluating any bar.

        Replaying history through :meth:`on_bar` would open positions that
        never existed. This method only appends to the buffers ``on_bar``
        fills, with the same caps, and never calls the entry or exit
        evaluation, so it can never emit a signal.

        Position state is set only from ``open_position``; it is never
        derived from the bars. With ``None`` the adapter ends flat. The
        position's ``last_stop`` is the stop in force before the restart: the
        next stop never falls below it (#680).

        Args:
            bars: Closed bars in delivery order (ascending ``close_time``,
                the daily bar before the 4h bar at equal close time). Bars of
                other intervals are ignored.
            open_position: Position to restore, or ``None`` for flat.
        """
        for bar in bars:
            if bar.interval == KlineInterval.H4:
                self._update_4h_buffer(bar)
            elif bar.interval == KlineInterval.D1:
                self._update_1d_buffer(bar)
        self.restore_position(open_position)

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Set the position state to what the portfolio holds; buffers untouched.

        The adapter marks itself long when it emits an entry and flat when it
        emits an exit. When that entry is not filled the caller passes
        ``None`` here, so the adapter is flat again as if the entry never
        fired (#707). When that exit is not filled the caller passes the
        position still held, so the exit rules run again on the next bar
        (#706). The time stop counts from ``entry_time``. The ratcheted stop
        (#680) is the larger of ``open_position.last_stop`` and the stop this
        adapter recorded for the same entry on the bar whose exit failed, so a
        retry never trails below a stop already in force (pre-registration
        §5.3), whichever caller restores it.

        An adapter that did not record that entry itself (a restart) posts
        again, before the next bar is tested, the entry level and every daily
        close level since the entry that its 4h buffer holds (#785). Posting
        only ever raises the stop, so a level already in force changes nothing.

        Args:
            open_position: Position held, or ``None`` for flat.
        """
        if open_position is None:
            self._in_position = False
            self._entry_time = None
            self._entry_price = None
            self._stop_in_force = None
            self._forget_posting()
            return
        stops = [open_position.last_stop]
        record = self._last_stop_record
        recorded_here = record is not None and record.entry_time == open_position.entry_time
        if record is not None and recorded_here:
            stops.append(record.stop_price)
        else:
            self._forget_posting()
        known = [s for s in stops if s is not None]
        self._in_position = True
        self._entry_time = open_position.entry_time
        self._entry_price = open_position.entry_price
        self._stop_in_force = max(known) if known else None

    def _forget_posting(self) -> None:
        """Drop the posted level so the next held bar posts again from the entry."""
        self._posted_level = None
        self._posted_atr = None
        self._posted_through = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_4h_buffer(self, bar: Bar) -> None:
        """Append bar to the 4h buffer, evicting old entries."""
        self._bars_4h.append(bar)
        if len(self._bars_4h) > _4H_BUFFER_CAP:
            self._bars_4h = self._bars_4h[-_4H_BUFFER_CAP:]

    def _update_1d_buffer(self, bar: Bar) -> None:
        """Append bar to the 1D buffer, evicting old entries."""
        self._bars_1d.append(bar)
        if len(self._bars_1d) > _1D_BUFFER_CAP:
            self._bars_1d = self._bars_1d[-_1D_BUFFER_CAP:]

    def _closed_1d_bars(self, as_of: datetime) -> list[Bar]:
        """Return buffered 1D bars that had closed by ``as_of``."""
        return [b for b in self._bars_1d if b.close_time <= as_of]

    def _regime_above_ema(self, as_of: datetime) -> bool:
        """Return True when the last 1D bar closed by ``as_of`` is above EMA(period)."""
        closed = self._closed_1d_bars(as_of)
        return bool(closed) and EmaRegimeFilter.above_ema(closed, period=self._ema_period)

    def _on_4h_bar(self, bar: Bar) -> StrategySignal | None:
        """Handle a closed 4h bar: update buffer, then evaluate signals."""
        self._update_4h_buffer(bar)

        if self._in_position:
            return self._evaluate_exit(bar)
        return self._evaluate_entry(bar)

    def _entry_check(self, as_of: datetime) -> EntryCheck:
        """Evaluate each of the three entry conditions (pre-reg §5.1).

        All three are evaluated so the record names every condition that held;
        each is a pure read of the buffers, so evaluating all of them decides
        exactly what the short-circuit form decided.
        """
        breakout = DonchianEngine.high_break(self._bars_4h, period=self._donchian_period)
        adx = AdxEngine.value(self._bars_4h, period=self._adx_period)
        momentum = adx is not None and adx >= self._adx_threshold
        return EntryCheck(
            breakout=breakout, momentum=momentum, regime=self._regime_above_ema(as_of)
        )

    def _entry_conditions_met(self, as_of: datetime) -> bool:
        """Return True when all three entry conditions fire simultaneously (pre-reg §5.1)."""
        check = self._entry_check(as_of)
        self._last_entry_check = check
        return check.all_met

    def _evaluate_entry(self, bar: Bar) -> StrategySignal | None:
        """Check whether all three entry conditions fire on this 4h bar.

        Returns an ENTRY signal when Donchian(20) AND ADX(14)>=25 AND
        1D EMA(200) regime all fire simultaneously (pre-reg §5.1).
        """
        if not self._entry_conditions_met(bar.close_time):
            return None

        # All conditions met — compute stop and ATR for signal
        stop = ChandelierEngine.trailing_stop(
            self._bars_4h,
            period=self._chandelier_period,
            atr_mult=self._chandelier_atr_mult,
        )
        atr = AtrEngine.value(self._bars_4h, period=self._atr_sizing_period)

        entry_price = bar.close

        # Guard: stop and ATR must be computable, and stop must be below entry (R-7)
        if stop is None or atr is None or stop >= entry_price:
            return None

        self._in_position = True
        self._entry_time = bar.close_time
        self._entry_price = entry_price
        # §5.3 posts the stop "immediately after entry fill". The market entry
        # fills at this bar's close, so the bars closed by then, this one
        # included, are what the stop can be computed from: the entry level is
        # the signal's stop. A new position never inherits an earlier stop.
        self._stop_in_force = None
        self._post(self._bars_4h)
        self._posted_through = bar.close_time
        self._record_stop(previous=None)

        return StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=entry_price,
            stop_price=stop,
            atr_context=atr,
            signal_time=bar.close_time,
            exit_reason=None,
            f_star_estimate=None,
        )

    def _evaluate_exit(self, bar: Bar) -> StrategySignal | None:
        """Check whether any exit condition fires on this 4h bar.

        Exit conditions in priority order (pre-reg §5.2):
            A. Chandelier stop: bar.low ≤ the stop in force, posted at the
               entry or the latest daily close before this bar (§5.3, #785)
            B. Time stop: 90 days from entry
            C. Regime break: 1D close < EMA(200)

        Returns an EXIT signal when any condition fires.
        """
        assert self._entry_time is not None, (  # noqa: S101 — invariant
            "SwingStrategyAdapter: _entry_time must be set when in_position=True"
        )

        previous = self._stop_in_force
        # The stop resting during this bar was posted before it opened (§5.3).
        self._post_daily_levels(through=bar.open_time)
        stop = self._stop_in_force
        atr = AtrEngine.value(self._bars_4h, period=self._atr_sizing_period)

        # Fallback ATR when buffer is freshly seeded (should not happen post-entry)
        if atr is None:
            atr = Decimal("1")

        exit_reason: str | None = None

        # A: Chandelier stop (price touched stop level)
        if stop is not None and bar.low <= stop:
            exit_reason = "chandelier"

        # B: Time stop (90 calendar days)
        elif bar.close_time >= self._entry_time + timedelta(days=self._time_stop_days):
            exit_reason = "time_stop"
            stop = stop if stop is not None else bar.close - atr  # best-effort

        # C: Regime break (1D close < EMA(200))
        elif self._closed_1d_bars(bar.close_time) and not self._regime_above_ema(bar.close_time):
            exit_reason = "regime_break"
            stop = stop if stop is not None else bar.close - atr

        if exit_reason is None:
            # Still held: a daily close at this bar's end moves the stop for the next bar.
            self._post_daily_levels(through=bar.close_time)
            self._record_stop(previous)
            return None

        self._record_stop(previous)
        # Reset position state
        self._in_position = False
        self._entry_time = None
        self._stop_in_force = None
        entry_price_saved = self._entry_price
        self._entry_price = None

        # For exit signals, entry_price carries the original entry reference
        if stop is None:
            stop = bar.close - atr  # last-resort fallback

        return StrategySignal(
            kind=SignalKind.EXIT,
            side=SignalSide.LONG,
            entry_price=entry_price_saved,
            stop_price=stop,
            atr_context=atr,
            signal_time=bar.close_time,
            exit_reason=exit_reason,
            f_star_estimate=None,
        )

    def _post(self, bars: Sequence[Bar]) -> None:
        """Post the chandelier level of ``bars`` as the stop, never below the one in force (#680).

        A long stop only moves up while the position is open (pre-reg §5.3,
        SA-7): the higher of the previous stop in force and the new level. With
        no previous stop (the entry, or a restart with none stored) it is the
        level itself. Too little history posts nothing.
        """
        level = ChandelierEngine.trailing_stop(
            bars, period=self._chandelier_period, atr_mult=self._chandelier_atr_mult
        )
        if level is None:
            return
        atr = AtrEngine.value(bars, period=self._chandelier_period)
        assert atr is not None, (  # noqa: S101 — invariant
            "SwingStrategyAdapter: a computed chandelier stop implies a computable ATR"
        )
        previous = self._stop_in_force
        self._stop_in_force = level if previous is None else max(previous, level)
        self._posted_level = level
        self._posted_atr = atr

    def _post_daily_levels(self, *, through: datetime) -> None:
        """Post every level not yet posted whose 4h bar closed by ``through`` (#785).

        Pre-registration §5.3 moves the stop "daily": a level is posted when a
        UTC day closes, from the 4h bars closed by then, and holds until the
        next daily close. The 4h bar ending the day is the last bar that day's
        level sees; the daily boundary is where the 1D bar closes. Without a
        posting (a restart) the entry bar's level is posted first, when the
        buffer still holds it. Levels post in time order, so each posting
        ratchets on the one before.
        """
        entry_time = self._entry_time
        assert entry_time is not None, (  # noqa: S101 — invariant
            "SwingStrategyAdapter: a stop is posted only while a position is held"
        )
        since = self._posted_through
        pending: list[int] = []
        for index in range(len(self._bars_4h) - 1, -1, -1):
            bar = self._bars_4h[index]
            if bar.close_time < entry_time or (since is not None and bar.close_time <= since):
                break
            if bar.close_time <= through and (bar.close_time == entry_time or _ends_a_day(bar)):
                pending.append(index)
        for index in reversed(pending):
            self._post(self._bars_4h[: index + 1])
        if since is None or through > since:
            self._posted_through = through

    def _record_stop(self, previous: Decimal | None) -> None:
        """Record the stop in force after this bar and the level it was posted from (#388, #680).

        ``previous`` is the stop in force before this bar. ``chandelier_level``
        and ``atr`` are those of the latest posting (the entry or a daily
        close), before the ratchet. No record while no stop is posted.
        """
        entry_time = self._entry_time
        stop, level, atr = self._stop_in_force, self._posted_level, self._posted_atr
        if entry_time is None or stop is None or level is None or atr is None:
            return
        reaches_entry = self._bars_4h[0].close_time <= entry_time
        held = [b.high for b in self._bars_4h if b.close_time >= entry_time]
        self._last_stop_record = StopRecord(
            entry_time=entry_time,
            stop_price=stop,
            chandelier_level=level,
            atr=atr,
            high_since_entry=max(held) if reaches_entry else None,
            stop_moved=None if previous is None else stop != previous,
        )


def _ends_a_day(bar: Bar) -> bool:
    """Return True when ``bar`` is the last bar of its UTC day (it ends where a 1D bar closes)."""
    end = bar.open_time + interval_duration(bar.interval)
    return end == end.replace(hour=0, minute=0, second=0, microsecond=0)
