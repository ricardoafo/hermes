"""Tests for ``SwingStrategyAdapter.restore_position`` after an unfilled signal (#707, #706).

Invariants protected here:

- an adapter that emitted an entry and is restored to flat signals no exit on
  later bars, even a crash far below any Chandelier stop;
- once flat again it emits a new entry on the next qualifying bar;
- restoring touches no bar history: the next evaluation equals that of an
  adapter that never saw the refused entry;
- a restored open position exits with the restored entry price;
- after its exit is declined and the position restored, the adapter exits
  again on the next qualifying bar, with the same entry price (#706);
- the time stop counts from the restored entry time, not from the restore.

Reference: issues #707 and #706; pre-registration-v1 §5.2 (exits), §6.3 (a
rejected trade is skipped).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_BASE = datetime(2024, 1, 1, tzinfo=UTC)
_H4_BASE = _BASE + timedelta(days=15)
_STATE = PortfolioState(
    peak_value=Decimal("10000"),
    current_value=Decimal("10000"),
    realized_slippage_cumulative=Decimal("0"),
    correlations={},
    dd_halt_required=False,
)


def _bar(close: str, index: int, *, daily: bool = False, low: str | None = None) -> Bar:
    """Build a closed bar; 4h bars start after every 1d bar has closed."""
    span = timedelta(days=1) if daily else timedelta(hours=4)
    open_time = (_BASE if daily else _H4_BASE) + span * index
    price = Decimal(close)
    return Bar(
        pair=_PAIR,
        interval=KlineInterval.D1 if daily else KlineInterval.H4,
        open_time=open_time,
        open=price,
        high=price + Decimal("2"),
        low=Decimal(low) if low is not None else price - Decimal("2"),
        close=price,
        volume=Decimal("100"),
        close_time=open_time + span,
    )


def _adapter() -> SwingStrategyAdapter:
    """Return a fast adapter with small periods, as the integration tests do."""
    return SwingStrategyAdapter(
        donchian_period=5,
        adx_period=5,
        adx_threshold=Decimal("5"),
        ema_period=10,
        chandelier_period=5,
        chandelier_atr_mult=Decimal("3.0"),
        atr_sizing_period=5,
    )


def _history() -> list[Bar]:
    """Rising 1d bars, then eleven flat 4h bars: no signal yet."""
    daily = [_bar(str(1000 + 200 * i // 11), i, daily=True) for i in range(12)]
    return [*daily, *(_bar("1000", i) for i in range(11))]


def _entered_adapter() -> SwingStrategyAdapter:
    """Return an adapter that has just emitted the breakout entry at bar 11."""
    adapter = _adapter()
    for bar in _history():
        assert adapter.on_bar(bar, _STATE) is None
    signal = adapter.on_bar(_bar("2000", 11), _STATE)
    assert isinstance(signal, StrategySignal)
    assert signal.kind is SignalKind.ENTRY
    return adapter


def test_refused_entry_restored_to_flat_signals_no_exit_on_a_crash() -> None:
    """Restored to flat after its entry, the adapter emits nothing on a crash bar."""
    adapter = _entered_adapter()
    adapter.restore_position(None)

    later = [
        adapter.on_bar(_bar("2000", 12), _STATE),
        adapter.on_bar(_bar("2000", 13), _STATE),
        adapter.on_bar(_bar("500", 14, low="100"), _STATE),
    ]

    assert later == [None, None, None]


def test_without_restore_the_crash_bar_signals_an_exit() -> None:
    """Control: the same adapter left long exits on the crash (the #707 symptom)."""
    adapter = _entered_adapter()

    signal = adapter.on_bar(_bar("500", 12, low="100"), _STATE)

    assert isinstance(signal, StrategySignal)
    assert (signal.kind, signal.exit_reason) == (SignalKind.EXIT, "chandelier")


def test_refused_entry_restored_to_flat_enters_again_on_the_next_breakout() -> None:
    """Flat again, the adapter emits a new entry at the next breakout's close."""
    adapter = _entered_adapter()
    adapter.restore_position(None)

    signal = adapter.on_bar(_bar("3000", 12), _STATE)

    assert isinstance(signal, StrategySignal)
    assert (signal.kind, signal.entry_price, signal.signal_time) == (
        SignalKind.ENTRY,
        Decimal("3000"),
        _bar("3000", 12).close_time,
    )


def test_restore_keeps_history_so_the_next_bar_matches_a_never_entered_adapter() -> None:
    """Restoring touches no buffer: the next bar evaluates as if the entry was never long."""
    restored = _entered_adapter()
    restored.restore_position(None)
    control = _adapter()
    control.seed([*_history(), _bar("2000", 11)])

    assert restored.on_bar(_bar("3000", 12), _STATE) == control.on_bar(_bar("3000", 12), _STATE)


def test_restore_with_an_open_position_exits_with_that_entry_price() -> None:
    """A restored position (the #706 path) exits on the crash carrying its entry price."""
    adapter = _adapter()
    adapter.seed([*_history(), _bar("2000", 11)])
    adapter.restore_position(
        OpenPosition(entry_time=_bar("2000", 11).close_time, entry_price=Decimal("1999"))
    )

    signal = adapter.on_bar(_bar("500", 12, low="100"), _STATE)

    assert isinstance(signal, StrategySignal)
    assert (signal.kind, signal.entry_price) == (SignalKind.EXIT, Decimal("1999"))


def test_declined_exit_restored_exits_again_on_the_next_crash() -> None:
    """Exit emitted, declined and restored: the next crash bar exits again at the entry price."""
    adapter = _entered_adapter()
    entry = OpenPosition(entry_time=_bar("2000", 11).close_time, entry_price=Decimal("2000"))
    first = adapter.on_bar(_bar("500", 12, low="100"), _STATE)
    adapter.restore_position(entry)

    retry = adapter.on_bar(_bar("500", 13, low="100"), _STATE)

    assert isinstance(first, StrategySignal)
    assert isinstance(retry, StrategySignal)
    assert (retry.kind, retry.exit_reason, retry.entry_price) == (
        SignalKind.EXIT,
        "chandelier",
        first.entry_price,
    )


def test_time_stop_counts_from_the_restored_entry_time() -> None:
    """A restored entry 90 days back hits the time stop; the same bar a day after entry does not."""
    bar = _bar("2000", 12)
    old = _adapter()
    old.seed([*_history(), _bar("2000", 11)])
    old.restore_position(
        OpenPosition(entry_time=bar.close_time - timedelta(days=90), entry_price=Decimal("2000"))
    )
    recent = _adapter()
    recent.seed([*_history(), _bar("2000", 11)])
    recent.restore_position(
        OpenPosition(entry_time=_bar("2000", 11).close_time, entry_price=Decimal("2000"))
    )

    old_signal = old.on_bar(bar, _STATE)

    assert isinstance(old_signal, StrategySignal)
    assert (old_signal.exit_reason, recent.on_bar(bar, _STATE)) == ("time_stop", None)
