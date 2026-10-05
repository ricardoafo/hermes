"""Tests for the per-bar stop record of SwingStrategyAdapter (#388).

The record is read-only: it must report exactly the chandelier stop the
pre-registered §5.2 rule evaluated on every bar a position is held, and neither
recording it nor restoring the last stop may change which bars signal.

Reference: pre-registration-v1.md §5.2, §11; 388-stop-marks change.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, StrategySignal
from hermes.domain.strategy.stop_mark import StopRecord
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.strategy.swing.engines.atr import AtrEngine
from hermes.domain.strategy.swing.engines.chandelier import ChandelierEngine
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_D1_BASE = datetime(2024, 1, 1, tzinfo=UTC)
_DAILY_SEEDS = 12
_H4_BASE = _D1_BASE + timedelta(days=_DAILY_SEEDS)
_CHANDELIER = 5
_MULT = Decimal("3.0")
_RAMP = [Decimal("1000") + Decimal("15") * i for i in range(30)]


def _bar(close: Decimal, index: int, interval: KlineInterval = KlineInterval.H4) -> Bar:
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
    adapter = SwingStrategyAdapter(
        donchian_period=5,
        adx_period=5,
        adx_threshold=Decimal("10"),
        ema_period=10,
        chandelier_period=_CHANDELIER,
        chandelier_atr_mult=_MULT,
        time_stop_days=90,
        atr_sizing_period=5,
    )
    for i in range(_DAILY_SEEDS):
        adapter.on_bar(_bar(Decimal("900") + Decimal("10") * i, i, KlineInterval.D1), _state())
    return adapter


def _path(steps: list[Decimal]) -> list[Bar]:
    closes = list(_RAMP)
    for step in steps:
        closes.append(max(closes[-1] + step, Decimal("10")))
    return [_bar(close, i) for i, close in enumerate(closes)]


def _run(
    adapter: SwingStrategyAdapter, bars: list[Bar]
) -> list[tuple[StrategySignal | None, StopRecord | None]]:
    out: list[tuple[StrategySignal | None, StopRecord | None]] = []
    for bar in bars:
        signal = adapter.on_bar(bar, _state())
        out.append((signal, adapter.last_stop_record))
    return out


def _expected(bars: list[Bar], entry_time: datetime, previous: Decimal | None) -> StopRecord:
    """Compute the record independently from the bars the strategy had seen."""
    stop = ChandelierEngine.trailing_stop(bars, period=_CHANDELIER, atr_mult=_MULT)
    atr = AtrEngine.value(bars, period=_CHANDELIER)
    assert stop is not None
    assert atr is not None
    return StopRecord(
        entry_time=entry_time,
        stop_price=stop,
        chandelier_level=stop,
        atr=atr,
        high_since_entry=max(b.high for b in bars if b.close_time >= entry_time),
        stop_moved=None if previous is None else stop != previous,
    )


_steps = st.lists(
    st.decimals(min_value=Decimal("-60"), max_value=Decimal("40"), places=2),
    min_size=5,
    max_size=50,
)


class TestRecordFollowsThePosition:
    """One record per bar while a position is held, equal to the §5.2 stop on those bars."""

    @given(steps=_steps)
    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    def test_every_held_bar_records_the_chandelier_stop(self, steps: list[Decimal]) -> None:
        """Entry bar to exit bar each record the stop computed on the same bars; flat bars none."""
        bars = _path(steps)
        entry_time: datetime | None = None
        previous: Decimal | None = None
        for i, (signal, record) in enumerate(_run(_adapter(), bars)):
            seen = bars[: i + 1]
            if entry_time is None and signal is not None and signal.kind is SignalKind.ENTRY:
                entry_time, previous = bars[i].close_time, None
            if entry_time is None:
                assert record is None
                continue
            expected = _expected(seen, entry_time, previous)
            assert record == expected
            previous = expected.stop_price
            if signal is not None and signal.kind is SignalKind.EXIT:
                entry_time = None

    def test_the_entry_bar_records_the_signal_stop(self) -> None:
        """The entry bar's record carries the stop the entry signal carries, with no move yet."""
        for signal, record in _run(_adapter(), _path([])):
            if signal is not None:
                assert record is not None
                assert (record.stop_price, record.stop_moved) == (signal.stop_price, None)
                return
        raise AssertionError("the ramp must produce an entry")

    def test_exit_bar_records_the_stop_that_was_hit(self) -> None:
        """A chandelier exit's bar records the stop the exit signal names."""
        bars = _path([Decimal("-300")])
        signal, record = _run(_adapter(), bars)[-1]
        assert signal is not None
        assert signal.exit_reason == "chandelier"
        assert record is not None
        assert record.stop_price == signal.stop_price

    def test_daily_bar_while_held_leaves_no_record(self) -> None:
        """A daily bar is not a stop evaluation, even with a position open."""
        adapter = _adapter()
        _run(adapter, _path([]))
        assert adapter.last_stop_record is not None

        adapter.on_bar(_bar(Decimal("1300"), 40, KlineInterval.D1), _state())

        assert adapter.last_stop_record is None


class TestRestoredStop:
    """The stop seeded at restart only feeds ``stop_moved``; it never changes the stop."""

    def _seeded(self, last_stop: Decimal | None, *, entry_index: int) -> SwingStrategyAdapter:
        adapter = _adapter()
        adapter.seed(
            _path([])[:-1],
            open_position=OpenPosition(
                entry_time=_bar(Decimal("1"), entry_index).close_time,
                entry_price=Decimal("1200"),
                last_stop=last_stop,
            ),
        )
        return adapter

    def test_restored_stop_decides_stop_moved_on_the_next_bar(self) -> None:
        """With a stored stop the first bar after a restart says whether it moved."""
        bars = _path([])
        stop = ChandelierEngine.trailing_stop(bars, period=_CHANDELIER, atr_mult=_MULT)
        assert stop is not None
        for stored, moved in ((stop, False), (stop - Decimal("1"), True)):
            adapter = self._seeded(stored, entry_index=20)
            adapter.on_bar(bars[-1], _state())
            record = adapter.last_stop_record
            assert record is not None
            assert (record.stop_price, record.stop_moved) == (stop, moved)

    def test_no_stored_stop_leaves_stop_moved_unknown(self) -> None:
        """A restart without a stored stop records ``stop_moved`` as unknown, not as moved."""
        adapter = self._seeded(None, entry_index=20)
        adapter.on_bar(_path([])[-1], _state())
        record = adapter.last_stop_record
        assert record is not None
        assert record.stop_moved is None

    def test_entry_older_than_the_buffer_has_no_high_since_entry(self) -> None:
        """When warm-up history starts after the entry bar, the high since entry is unknown."""
        adapter = self._seeded(None, entry_index=-3)
        adapter.on_bar(_path([])[-1], _state())
        record = adapter.last_stop_record
        assert record is not None
        assert record.high_since_entry is None

    @given(
        steps=_steps,
        last_stop=st.one_of(
            st.none(), st.decimals(min_value=Decimal("1"), max_value=Decimal("5000"), places=2)
        ),
    )
    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    def test_restored_stop_never_changes_a_signal(
        self, steps: list[Decimal], last_stop: Decimal | None
    ) -> None:
        """Signals after a restart are identical whatever stop was stored (§11 parity)."""
        bars = _path(steps)
        signals: list[list[StrategySignal | None]] = []
        for stored in (None, last_stop):
            adapter = self._seeded(stored, entry_index=20)
            signals.append([adapter.on_bar(bar, _state()) for bar in bars[len(_RAMP) - 1 :]])
        assert signals[0] == signals[1]
