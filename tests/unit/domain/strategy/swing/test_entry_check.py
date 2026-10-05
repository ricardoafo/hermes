"""Tests for the per-condition entry record of SwingStrategyAdapter (#656).

The record is read-only: it must report exactly what the pre-registered §5.1
rule evaluated, and recording it must not change which bars signal.

Reference: pre-registration-v1.md §5.1, §11; ADR-012 D-06.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind
from hermes.domain.strategy.swing.adapter import EntryCheck, SwingStrategyAdapter
from hermes.domain.strategy.swing.engines.adx import AdxEngine
from hermes.domain.strategy.swing.engines.donchian import DonchianEngine
from hermes.domain.strategy.swing.engines.ema import EmaRegimeFilter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_D1_BASE = datetime(2024, 1, 1, tzinfo=UTC)
_DAILY_SEEDS = 12
_H4_BASE = _D1_BASE + timedelta(days=_DAILY_SEEDS)

_NOTHING_HELD = EntryCheck(breakout=False, momentum=False, regime=False)
"""A first 4h bar has no channel, no ADX and no daily close behind it."""

_DONCHIAN = 5
_ADX = 5
_ADX_THRESHOLD = Decimal("10")
_EMA = 10


def _bar(close: Decimal, index: int, interval: KlineInterval) -> Bar:
    hours = 4 if interval == KlineInterval.H4 else 24
    base = _H4_BASE if interval == KlineInterval.H4 else _D1_BASE
    open_time = base + timedelta(hours=hours * index)
    return Bar(
        pair=_BTC_USDT,
        interval=interval,
        open_time=open_time,
        open=close,
        high=close + Decimal("2"),
        low=max(close - Decimal("2"), Decimal("0.01")),
        close=close,
        volume=Decimal("100"),
        close_time=open_time + timedelta(hours=hours),
    )


def _state() -> PortfolioState:
    return PortfolioState(
        peak_value=Decimal("10000"),
        current_value=Decimal("10000"),
        realized_slippage_cumulative=Decimal("0"),
        correlations={},
        dd_halt_required=False,
    )


def _adapter() -> SwingStrategyAdapter:
    return SwingStrategyAdapter(
        donchian_period=_DONCHIAN,
        adx_period=_ADX,
        adx_threshold=_ADX_THRESHOLD,
        ema_period=_EMA,
        chandelier_period=5,
        time_stop_days=90,
        atr_sizing_period=5,
    )


def _seed_daily(adapter: SwingStrategyAdapter, closes: list[Decimal]) -> list[Bar]:
    daily = [_bar(c, i, KlineInterval.D1) for i, c in enumerate(closes)]
    for bar in daily:
        adapter.on_bar(bar, _state())
    return daily


def _short_circuit_rule(h4: list[Bar], daily: list[Bar], as_of: datetime) -> bool:
    """Apply the §5.1 rule as the adapter did before #656, stopping at the first miss."""
    if not DonchianEngine.high_break(h4, period=_DONCHIAN):
        return False
    adx = AdxEngine.value(h4, period=_ADX)
    if adx is None or adx < _ADX_THRESHOLD:
        return False
    closed = [d for d in daily if d.close_time <= as_of]
    return bool(closed) and EmaRegimeFilter.above_ema(closed, period=_EMA)


def _expected_check(h4: list[Bar], daily: list[Bar], as_of: datetime) -> EntryCheck:
    adx = AdxEngine.value(h4, period=_ADX)
    closed = [d for d in daily if d.close_time <= as_of]
    return EntryCheck(
        breakout=DonchianEngine.high_break(h4, period=_DONCHIAN),
        momentum=adx is not None and adx >= _ADX_THRESHOLD,
        regime=bool(closed) and EmaRegimeFilter.above_ema(closed, period=_EMA),
    )


_steps = st.lists(
    st.decimals(min_value=Decimal("-40"), max_value=Decimal("40"), places=2),
    min_size=20,
    max_size=60,
)


class TestRecordMatchesTheRule:
    """The record names each §5.1 condition exactly as the strategy evaluated it."""

    @given(steps=_steps, daily_step=st.decimals(min_value=-5, max_value=5, places=1))
    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    def test_each_flat_4h_bar_records_every_condition(
        self, steps: list[Decimal], daily_step: Decimal
    ) -> None:
        """While flat, every 4h bar's record equals each condition computed on the same bars."""
        adapter = _adapter()
        daily = _seed_daily(
            adapter, [Decimal("1000") + daily_step * i for i in range(_DAILY_SEEDS)]
        )
        h4: list[Bar] = []
        close = Decimal("1000")
        for i, step in enumerate(steps):
            close = max(close + step, Decimal("10"))
            bar = _bar(close, i, KlineInterval.H4)
            h4.append(bar)
            signal = adapter.on_bar(bar, _state())
            assert adapter.last_entry_check == _expected_check(h4, daily, bar.close_time)
            if signal is not None:
                break

    @given(steps=_steps, daily_step=st.decimals(min_value=-5, max_value=5, places=1))
    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    def test_recording_keeps_the_short_circuit_decision(
        self, steps: list[Decimal], daily_step: Decimal
    ) -> None:
        """Evaluating all three conditions decides exactly what the first-miss rule decided."""
        adapter = _adapter()
        daily = _seed_daily(
            adapter, [Decimal("1000") + daily_step * i for i in range(_DAILY_SEEDS)]
        )
        h4: list[Bar] = []
        close = Decimal("1000")
        for i, step in enumerate(steps):
            close = max(close + step, Decimal("10"))
            bar = _bar(close, i, KlineInterval.H4)
            h4.append(bar)
            adapter.on_bar(bar, _state())
            check = adapter.last_entry_check
            assert check is not None
            assert check.all_met is _short_circuit_rule(h4, daily, bar.close_time)
            if check.all_met:
                break


class TestWhenThereIsNoRecord:
    """Bars that are not an entry check leave no record, so no stale answer is logged."""

    def test_daily_bar_clears_the_record(self) -> None:
        """A daily bar after a 4h check leaves ``last_entry_check`` empty."""
        adapter = _adapter()
        adapter.on_bar(_bar(Decimal("1000"), 0, KlineInterval.H4), _state())
        assert adapter.last_entry_check == _NOTHING_HELD

        adapter.on_bar(_bar(Decimal("1000"), _DAILY_SEEDS, KlineInterval.D1), _state())

        assert adapter.last_entry_check is None

    def test_exit_check_while_in_position_leaves_no_record(self) -> None:
        """With a position open the 4h bar is an exit check, so there is no entry record."""
        adapter = _adapter()
        adapter.on_bar(_bar(Decimal("1000"), 0, KlineInterval.H4), _state())
        assert adapter.last_entry_check == _NOTHING_HELD
        adapter.seed([], open_position=_open_position())

        adapter.on_bar(_bar(Decimal("1001"), 1, KlineInterval.H4), _state())

        assert adapter.last_entry_check is None


class TestRecordOnASignal:
    """An entry signal is only ever emitted on a bar whose record says all three held."""

    def test_rising_market_entry_bar_records_all_three(self) -> None:
        """The bar that emits the entry records breakout, momentum and regime as held."""
        adapter = _adapter()
        _seed_daily(adapter, [Decimal("900") + Decimal("10") * i for i in range(_DAILY_SEEDS)])
        signal = None
        for i in range(40):
            signal = adapter.on_bar(
                _bar(Decimal("1000") + Decimal("15") * i, i, KlineInterval.H4), _state()
            )
            if signal is not None:
                break

        assert signal is not None
        assert signal.kind is SignalKind.ENTRY
        assert adapter.last_entry_check == EntryCheck(breakout=True, momentum=True, regime=True)


def _open_position() -> OpenPosition:
    return OpenPosition(entry_time=_H4_BASE + timedelta(hours=4), entry_price=Decimal("1000"))
