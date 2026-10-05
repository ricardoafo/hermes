"""SwingStrategyAdapter — StrategyPort implementation for swing-MVP1.

Coordinates five technical engines against pre-registration v1 §5 parameters
to produce entry and exit signals for the swing-MVP1 paper-trading experiment.

Entry conditions (ALL three must fire simultaneously on 4h bar close, §5.1):
    1. Donchian(20) breakout: bar.close > highest_high(prior 20 bars, 4h)
    2. ADX(14) ≥ 25 (4h)
    3. 1D close > EMA(200) (last fully closed 1D bar)

Exit conditions (first triggered wins, §5.2):
    A. Chandelier stop: current 4h bar's low <= highest_high(22) - 3*ATR(22)
    B. Time stop: 90 calendar days from entry bar close_time
    C. Regime break: 1D close < EMA(200) on a new 1D bar

Order type (§5.3):
    Entry: market order at bar close of signal bar (entry_price = bar.close)

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
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, SignalSide, StrategySignal
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

    @property
    def last_entry_check(self) -> EntryCheck | None:
        """Return the entry evaluation of the latest bar, or ``None``.

        ``None`` when the latest bar was a daily bar, was evaluated for an exit
        (a position was open), or no bar has been evaluated yet. Read-only: nothing in
        the signal path reads it back (#656).
        """
        return self._last_entry_check

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
        derived from the bars. With ``None`` the adapter ends flat.

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
        (#706). The Chandelier stop is recomputed from the bar buffers on
        every bar and the time stop from ``entry_time``, so no exit state is
        lost.

        Args:
            open_position: Position held, or ``None`` for flat.
        """
        if open_position is None:
            self._in_position = False
            self._entry_time = None
            self._entry_price = None
        else:
            self._in_position = True
            self._entry_time = open_position.entry_time
            self._entry_price = open_position.entry_price

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
            A. Chandelier stop: bar.low ≤ chandelier_stop
            B. Time stop: 90 days from entry
            C. Regime break: 1D close < EMA(200)

        Returns an EXIT signal when any condition fires.
        """
        assert self._entry_time is not None, (  # noqa: S101 — invariant
            "SwingStrategyAdapter: _entry_time must be set when in_position=True"
        )

        stop = ChandelierEngine.trailing_stop(
            self._bars_4h,
            period=self._chandelier_period,
            atr_mult=self._chandelier_atr_mult,
        )
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
            return None

        # Reset position state
        self._in_position = False
        self._entry_time = None
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
