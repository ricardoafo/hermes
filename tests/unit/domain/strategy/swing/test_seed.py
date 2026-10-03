"""Tests for ``SwingStrategyAdapter.seed`` — warm-up without evaluation.

Invariants protected here:

- seeding fills the buffers but never evaluates: history that satisfies the
  entry conditions leaves the adapter flat and emits nothing;
- the only way to start in a position is the ``open_position`` argument, and
  a restored position gets its exit with the restored entry price;
- the next live bar after a seed still evaluates;
- seeding applies the same buffer caps as ``on_bar``;
- a seeded adapter and one fed the same quiet history through ``on_bar``
  evaluate the next bar identically.

Reference: 309-serve-bar-loop change.md D-6, R-4 (A-4.1).
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


def _bar(
    close: str,
    index: int,
    interval: KlineInterval = KlineInterval.H4,
    *,
    low: str | None = None,
) -> Bar:
    """Build a closed bar; 4h bars start after every 1d bar has closed."""
    hours = 4 if interval is KlineInterval.H4 else 24
    base = _H4_BASE if interval is KlineInterval.H4 else _BASE
    open_time = base + timedelta(hours=hours * index)
    price = Decimal(close)
    return Bar(
        pair=_PAIR,
        interval=interval,
        open_time=open_time,
        open=price,
        high=price + Decimal("2"),
        low=Decimal(low) if low is not None else price - Decimal("2"),
        close=price,
        volume=Decimal("100"),
        close_time=open_time + timedelta(hours=hours),
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


def _daily_bars() -> list[Bar]:
    """Twelve rising 1d bars, so the last close is above the EMA."""
    return [_bar(str(1000 + 200 * i // 11), i, KlineInterval.D1) for i in range(12)]


def _quiet_4h() -> list[Bar]:
    """Eleven flat 4h bars: no breakout."""
    return [_bar("1000", i) for i in range(11)]


def _breakout() -> Bar:
    """Return the 4h bar that meets all three entry conditions after the quiet run."""
    return _bar("2000", 11)


def _crash() -> Bar:
    """Return a 4h bar whose low is far below any Chandelier stop."""
    return _bar("500", 12, low="100")


def _qualifying_history() -> list[Bar]:
    """History in delivery order whose last bar is an entry breakout."""
    return [*_daily_bars(), *_quiet_4h(), _breakout()]


def test_seed_history_that_qualifies_for_entry_returns_no_signal_and_stays_flat() -> None:
    """Seeding entry-qualifying history opens no position: a later crash exits nothing."""
    control = _adapter()
    control_signals = [control.on_bar(bar, _STATE) for bar in _qualifying_history()]
    assert [s.kind for s in control_signals if s is not None] == [SignalKind.ENTRY]

    seeded = _adapter()
    seeded.seed(_qualifying_history())

    assert seeded.on_bar(_crash(), _STATE) is None


def test_seed_with_open_position_restores_it_and_exits_with_the_restored_entry_price() -> None:
    """A restored position emits one EXIT carrying the restored entry price."""
    adapter = _adapter()
    adapter.seed(
        _qualifying_history(),
        open_position=OpenPosition(entry_time=_breakout().close_time, entry_price=Decimal("2000")),
    )

    signal = adapter.on_bar(_crash(), _STATE)

    assert isinstance(signal, StrategySignal)
    assert (signal.kind, signal.entry_price, signal.exit_reason) == (
        SignalKind.EXIT,
        Decimal("2000"),
        "chandelier",
    )


def test_seed_then_next_live_bar_still_evaluates() -> None:
    """After seeding quiet history, the breakout bar fed live emits the entry."""
    adapter = _adapter()
    adapter.seed([*_daily_bars(), *_quiet_4h()])

    signal = adapter.on_bar(_breakout(), _STATE)

    assert isinstance(signal, StrategySignal)
    assert (signal.kind, signal.entry_price) == (SignalKind.ENTRY, Decimal("2000"))


def test_seed_matches_on_bar_replay_when_the_replay_emitted_no_signal() -> None:
    """Seed and a silent on_bar replay give the same next evaluation."""
    history = [*_daily_bars(), *_quiet_4h()]
    replayed = _adapter()
    for bar in history:
        assert replayed.on_bar(bar, _STATE) is None
    seeded = _adapter()
    seeded.seed(history)

    assert seeded.on_bar(_breakout(), _STATE) == replayed.on_bar(_breakout(), _STATE)


def test_seed_applies_the_buffer_caps() -> None:
    """Seeding 400 4h bars keeps only the newest 300, as on_bar does."""
    adapter = _adapter()
    adapter.seed([_bar("1000", i) for i in range(400)])

    kept = adapter._bars_4h

    assert (len(kept), kept[0].open_time, kept[-1].open_time) == (
        300,
        _bar("1000", 100).open_time,
        _bar("1000", 399).open_time,
    )


def test_seed_without_open_position_resets_a_previous_position() -> None:
    """Re-seeding with None leaves the adapter flat even after a restored position."""
    adapter = _adapter()
    adapter.seed(
        _qualifying_history(),
        open_position=OpenPosition(entry_time=_breakout().close_time, entry_price=Decimal("2000")),
    )
    adapter.seed([])

    signals = [s for s in [adapter.on_bar(_crash(), _STATE)] if s is not None]
    assert signals == []


def test_seed_ignores_bars_of_other_intervals() -> None:
    """A 1h bar in the seed set reaches neither buffer."""
    adapter = _adapter()
    odd = Bar(
        pair=_PAIR,
        interval=KlineInterval.H1,
        open_time=_BASE,
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
        close_time=_BASE + timedelta(hours=1),
    )
    adapter.seed([odd])

    assert (adapter._bars_4h, adapter._bars_1d) == ([], [])
