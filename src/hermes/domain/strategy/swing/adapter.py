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
    Both buffers only hold bars whose ``close_time`` ≤ current bar's
    ``close_time``.  The current signal bar IS included in the 4h buffer
    before evaluation (the bar has already closed).

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

from datetime import datetime, timedelta
from decimal import Decimal

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.strategy.swing.engines.adx import AdxEngine
from hermes.domain.strategy.swing.engines.atr import AtrEngine
from hermes.domain.strategy.swing.engines.chandelier import ChandelierEngine
from hermes.domain.strategy.swing.engines.donchian import DonchianEngine
from hermes.domain.strategy.swing.engines.ema import EmaRegimeFilter

__all__ = ["SwingStrategyAdapter"]

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
        if bar.interval == KlineInterval.H4:
            return self._on_4h_bar(bar)
        if bar.interval == KlineInterval.D1:
            self._update_1d_buffer(bar)
            return None
        # Unknown interval — ignore
        return None

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

    def _on_4h_bar(self, bar: Bar) -> StrategySignal | None:
        """Handle a closed 4h bar: update buffer, then evaluate signals."""
        self._update_4h_buffer(bar)

        if self._in_position:
            return self._evaluate_exit(bar)
        return self._evaluate_entry(bar)

    def _entry_conditions_met(self) -> bool:
        """Return True when all three entry conditions fire simultaneously (pre-reg §5.1)."""
        # Condition 1: Donchian(20) breakout
        if not DonchianEngine.high_break(self._bars_4h, period=self._donchian_period):
            return False
        # Condition 2: ADX(14) >= threshold
        adx = AdxEngine.value(self._bars_4h, period=self._adx_period)
        if adx is None or adx < self._adx_threshold:
            return False
        # Condition 3: 1D close > EMA(200)
        return bool(
            self._bars_1d and EmaRegimeFilter.above_ema(self._bars_1d, period=self._ema_period)
        )

    def _evaluate_entry(self, bar: Bar) -> StrategySignal | None:
        """Check whether all three entry conditions fire on this 4h bar.

        Returns an ENTRY signal when Donchian(20) AND ADX(14)>=25 AND
        1D EMA(200) regime all fire simultaneously (pre-reg §5.1).
        """
        if not self._entry_conditions_met():
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
        elif self._bars_1d and not EmaRegimeFilter.above_ema(
            self._bars_1d, period=self._ema_period
        ):
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
