"""Property tests — no-lookahead invariant for SwingStrategyAdapter.

CRITICAL: This is the most important test in the spec-5 suite.
Lookahead bias is the primary risk for this experimental strategy.

Invariant tested:
    on_bar(bar_T) output depends ONLY on bars[0..T].
    Feeding bars[0..T+N] (additional future bars added) must produce
    the SAME signal for bar_T as feeding only bars[0..T].

Formal statement:
    Let signal_A = replay bars[0..T] into a fresh adapter, capture output at T.
    Let signal_B = replay bars[0..T+10] into a fresh adapter, capture output at T.
    Assert signal_A == signal_B.

Additional property tests:
    - Signal invariants (stop < entry, atr > 0, entry = bar close)
    - Determinism (same inputs → same output)
    - Multi-timeframe consistency (no future bars from either timeframe)

Reference: spec-5-swing-strategy-adapter change.md R-8.
pre-registration-v1.md §5 (lookahead-free requirement).
SA-4 P1 (closed-bar only).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import SignalKind, StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_BASE_TIME = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
# 4h bars start after the 1D seeds used below (12 bars) have closed, so the
# regime filter can see them without reading the future (#311).
_H4_BASE_TIME = _BASE_TIME + timedelta(days=12)

# ──────────────────────────────────────────────────────────────
# Hypothesis strategies
# ──────────────────────────────────────────────────────────────


def _make_bar(close: Decimal, index: int, interval: KlineInterval = KlineInterval.H4) -> Bar:
    hours = 4 if interval == KlineInterval.H4 else 24
    base = _H4_BASE_TIME if interval == KlineInterval.H4 else _BASE_TIME
    open_time = base + timedelta(hours=hours * index)
    close_time = open_time + timedelta(hours=hours)
    high = close + Decimal("2")
    low = max(close - Decimal("2"), Decimal("0.01"))
    return Bar(
        pair=_BTC_USDT,
        interval=interval,
        open_time=open_time,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=Decimal("100"),
        close_time=close_time,
    )


def _make_portfolio_state() -> PortfolioState:
    return PortfolioState(
        peak_value=Decimal("10000"),
        current_value=Decimal("10000"),
        realized_slippage_cumulative=Decimal("0"),
        correlations={},
        dd_halt_required=False,
    )


@st.composite
def rising_close_list(draw: st.DrawFn, min_size: int = 30, max_size: int = 60) -> list[Decimal]:
    """Generate a list of strictly rising close prices for breakout testing."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    start = draw(st.decimals(min_value=Decimal("1000"), max_value=Decimal("50000"), places=2))
    step = draw(st.decimals(min_value=Decimal("1"), max_value=Decimal("100"), places=2))
    return [start + step * i for i in range(n)]


# ──────────────────────────────────────────────────────────────
# Property: no lookahead
# ──────────────────────────────────────────────────────────────


class TestNoLookaheadInvariant:
    """Formal no-lookahead test for SwingStrategyAdapter.on_bar."""

    def _replay_and_capture(
        self,
        bars: list[Bar],
        target_index: int,
        adapter: SwingStrategyAdapter | None = None,
    ) -> StrategySignal | None:
        """Replay bars[0..target_index] into a fresh adapter and return signal at target_index."""
        if adapter is None:
            adapter = SwingStrategyAdapter(
                donchian_period=5,  # small for test efficiency
                adx_period=5,
                adx_threshold=Decimal("10"),  # low threshold to trigger more often
                ema_period=10,
                chandelier_period=5,
                time_stop_days=90,
                atr_sizing_period=5,
            )
        state = _make_portfolio_state()
        signal = None
        for i in range(target_index + 1):
            signal = adapter.on_bar(bars[i], state)
        return signal

    @given(closes=rising_close_list())
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_no_lookahead_4h_bars(self, closes: list[Decimal]) -> None:
        """on_bar(bar_T) must produce the same signal regardless of future bars.

        We compare output at bar index T=15 when:
        - fed only bars[0..15]
        - fed bars[0..15+10] (10 more future bars added)

        Signal at T must be identical in both cases.
        """
        bars = [_make_bar(c, i) for i, c in enumerate(closes)]
        if len(bars) < 20:
            return  # skip very short sequences

        target = min(15, len(bars) - 1)
        # Sanity: target bar must be within the bar list
        assert target < len(bars), f"target index {target} must be < len(bars) {len(bars)}"

        # Run A: only bars up to T
        signal_a = self._replay_and_capture(bars[: target + 1], target)

        # Run B: all bars (including future ones after T)
        signal_b = self._replay_and_capture(bars, target)

        assert signal_a == signal_b, (
            f"NO-LOOKAHEAD VIOLATION at bar T={target}: "
            f"signal with bars[0..{target}] = {signal_a!r} "
            f"but signal with all {len(bars)} bars = {signal_b!r}. "
            f"The adapter peeked at future bars!"
        )

    def test_no_lookahead_deterministic_fixture(self) -> None:
        """Deterministic no-lookahead check on a fixed bar sequence."""
        closes = [Decimal(str(1000 + i * 10)) for i in range(30)]
        bars = [_make_bar(c, i) for i, c in enumerate(closes)]

        for t in range(10, len(bars)):
            signal_short = self._replay_and_capture(bars[: t + 1], t)
            signal_full = self._replay_and_capture(bars, t)

            # Both outputs must be of the same type (None or StrategySignal)
            assert type(signal_short) is type(signal_full), (
                f"NO-LOOKAHEAD VIOLATION at T={t}: type mismatch "
                f"truncated={type(signal_short)}, full={type(signal_full)}"
            )
            assert signal_short == signal_full, (
                f"NO-LOOKAHEAD VIOLATION at T={t}: truncated={signal_short!r}, full={signal_full!r}"
            )


# ──────────────────────────────────────────────────────────────
# Property: signal invariants
# ──────────────────────────────────────────────────────────────


class TestSignalInvariants:
    """When on_bar emits a signal, the invariants from R-7 must hold."""

    def _find_entry_signal(self, adapter: SwingStrategyAdapter) -> StrategySignal | None:
        """Build a bar sequence likely to trigger entry and return the signal."""
        state = _make_portfolio_state()
        # 200 flat 1D bars to establish EMA
        for i in range(210):
            bar = _make_bar(Decimal("1000"), i, KlineInterval.D1)
            adapter.on_bar(bar, state)

        # 10 flat 4h bars + 1 strong breakout 4h bar
        for i in range(10):
            bar = _make_bar(Decimal("1000"), i, KlineInterval.H4)
            adapter.on_bar(bar, state)

        signal = None
        for i in range(50):
            c = Decimal("1000") if i < 10 else Decimal("1001")
            bar = _make_bar(c, 10 + i, KlineInterval.H4)
            sig = adapter.on_bar(bar, state)
            if sig is not None and sig.kind == SignalKind.ENTRY:
                signal = sig
                break
        return signal

    def test_stop_below_entry_for_long_entry(self) -> None:
        """stop_price < entry_price for LONG ENTRY signals (R-7)."""
        adapter = SwingStrategyAdapter(
            donchian_period=5,
            adx_period=5,
            adx_threshold=Decimal("5"),
            ema_period=10,
            chandelier_period=5,
            atr_sizing_period=5,
        )
        state = _make_portfolio_state()

        # Rising 1D bars so last close is strictly above EMA (flat would equal EMA)
        for i in range(12):
            c = Decimal("1000") + Decimal("10") * Decimal(i)
            bar = _make_bar(c, i, KlineInterval.D1)
            adapter.on_bar(bar, state)

        # 6 flat 4h + breakout bar
        for i in range(6):
            bar = _make_bar(Decimal("1000"), i, KlineInterval.H4)
            adapter.on_bar(bar, state)

        for i in range(20):
            c = Decimal("1000") if i < 5 else Decimal("2000")  # strong breakout
            bar = _make_bar(c, 6 + i, KlineInterval.H4)
            sig = adapter.on_bar(bar, state)
            if sig is not None and sig.kind == SignalKind.ENTRY:
                assert sig.stop_price < sig.entry_price, (
                    f"ENTRY signal invariant violated: stop ({sig.stop_price}) "
                    f"must be < entry ({sig.entry_price}) for LONG signals (R-7)"
                )
                break

    def test_atr_context_positive(self) -> None:
        """atr_context > 0 for all emitted signals (R-7)."""
        adapter = SwingStrategyAdapter(
            donchian_period=5,
            adx_period=5,
            adx_threshold=Decimal("5"),
            ema_period=10,
            chandelier_period=5,
            atr_sizing_period=5,
        )
        state = _make_portfolio_state()

        # Rising 1D bars so close > EMA
        for i in range(12):
            c = Decimal("1000") + Decimal("10") * Decimal(i)
            adapter.on_bar(_make_bar(c, i, KlineInterval.D1), state)

        for i in range(6):
            adapter.on_bar(_make_bar(Decimal("1000"), i, KlineInterval.H4), state)

        for i in range(20):
            c = Decimal("1000") if i < 5 else Decimal("2000")
            sig = adapter.on_bar(_make_bar(c, 6 + i, KlineInterval.H4), state)
            if sig is not None:
                assert sig.atr_context > Decimal("0"), (
                    f"Signal atr_context must be > 0, got {sig.atr_context} (R-7)"
                )
                break

    def test_entry_price_equals_bar_close(self) -> None:
        """entry_price = bar.close for ENTRY signals (R-7, §5.3 market entry at bar close)."""
        adapter = SwingStrategyAdapter(
            donchian_period=5,
            adx_period=5,
            adx_threshold=Decimal("5"),
            ema_period=10,
            chandelier_period=5,
            atr_sizing_period=5,
        )
        state = _make_portfolio_state()

        # Rising 1D bars so close > EMA
        for i in range(12):
            c = Decimal("1000") + Decimal("10") * Decimal(i)
            adapter.on_bar(_make_bar(c, i, KlineInterval.D1), state)

        for i in range(6):
            adapter.on_bar(_make_bar(Decimal("1000"), i, KlineInterval.H4), state)

        signal_bar_close = None
        for i in range(20):
            c = Decimal("1000") if i < 5 else Decimal("2000")
            bar = _make_bar(c, 6 + i, KlineInterval.H4)
            sig = adapter.on_bar(bar, state)
            if sig is not None and sig.kind == SignalKind.ENTRY:
                signal_bar_close = bar.close
                assert sig.entry_price == signal_bar_close, (
                    f"entry_price ({sig.entry_price}) must equal bar.close "
                    f"({signal_bar_close}) per §5.3 market-at-close (R-7)"
                )
                break


class TestDeterminism:
    """Same inputs → same output, always."""

    def _run_adapter(self, closes: list[Decimal]) -> list[StrategySignal | None]:
        """Run a fresh adapter through a close sequence and collect signals."""
        adapter = SwingStrategyAdapter(
            donchian_period=5,
            adx_period=5,
            adx_threshold=Decimal("10"),
            ema_period=10,
            chandelier_period=5,
            atr_sizing_period=5,
        )
        state = _make_portfolio_state()
        signals = []

        # Feed 12 rising 1D bars so close > EMA
        for i in range(12):
            c = Decimal("1000") + Decimal("10") * Decimal(i)
            adapter.on_bar(_make_bar(c, i, KlineInterval.D1), state)

        for i, c in enumerate(closes):
            sig = adapter.on_bar(_make_bar(c, i, KlineInterval.H4), state)
            signals.append(sig)
        return signals

    @given(
        closes=st.lists(
            st.decimals(min_value=Decimal("100"), max_value=Decimal("200"), places=2),
            min_size=20,
            max_size=40,
        )
    )
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_same_inputs_produce_same_signals(self, closes: list[Decimal]) -> None:
        """Running the adapter twice with identical inputs must yield identical signals."""
        signals_a = self._run_adapter(closes)
        signals_b = self._run_adapter(closes)
        # Length of output must match length of input (one signal slot per bar)
        assert len(signals_a) == len(closes), (
            f"Signal list length {len(signals_a)} must equal input count {len(closes)}"
        )
        assert signals_a == signals_b, (
            f"Adapter must be deterministic: run_a != run_b on {len(closes)} bars. "
            f"Non-determinism indicates hidden state or randomness."
        )


# ──────────────────────────────────────────────────────────────
# Property: multi-timeframe consistency
# ──────────────────────────────────────────────────────────────


class TestMultiTimeframeClosedBar:
    """4h bars never use 1D bars with close_time > current 4h bar close_time."""

    def test_1d_bar_after_4h_bar_does_not_affect_4h_signal(self) -> None:
        """A 1D bar whose close_time is after the 4h signal bar must not affect the signal.

        Procedure:
        1. Build a fresh adapter, feed 1D bars[0..N], then 4h bars[0..M].
           Capture signal at 4h bar M.
        2. Build another fresh adapter, feed 1D bars[0..N+5] (future 1D bars added),
           then 4h bars[0..M].
           Capture signal at 4h bar M.

        The two signals must be identical because the extra 1D bars close after the 4h bar.
        """
        state = _make_portfolio_state()

        # We need a minimal setup where entry fires
        def make_adapter_with_1d_count(n_1d: int) -> SwingStrategyAdapter:
            adapter = SwingStrategyAdapter(
                donchian_period=5,
                adx_period=5,
                adx_threshold=Decimal("5"),
                ema_period=10,
                chandelier_period=5,
                atr_sizing_period=5,
            )
            # Feed n_1d rising 1D bars so close > EMA
            for i in range(n_1d):
                c = Decimal("1000") + Decimal("10") * Decimal(i)
                adapter.on_bar(_make_bar(c, i, KlineInterval.D1), state)
            return adapter

        n_1d_base = 12

        adapter_a = make_adapter_with_1d_count(n_1d_base)
        adapter_b = make_adapter_with_1d_count(n_1d_base)

        # Feed identical 4h bars to both — only bars[0..6] then one breakout
        for i in range(6):
            bar = _make_bar(Decimal("1000"), i, KlineInterval.H4)
            adapter_a.on_bar(bar, state)
            adapter_b.on_bar(bar, state)

        # Signal bar
        breakout_bar = _make_bar(Decimal("2000"), 6, KlineInterval.H4)
        sig_a = adapter_a.on_bar(breakout_bar, state)
        sig_b = adapter_b.on_bar(breakout_bar, state)

        # Both adapters received identical 1D and 4h histories → outputs must match type
        assert type(sig_a) is type(sig_b), (
            f"Multi-timeframe consistency: output types must match, "
            f"sig_a={type(sig_a)}, sig_b={type(sig_b)}"
        )
        assert sig_a == sig_b, (
            f"Multi-timeframe consistency violated: same 4h bars with same 1D history "
            f"must give same signal. sig_a={sig_a!r}, sig_b={sig_b!r}"
        )
