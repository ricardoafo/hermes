"""Tests for the pre-registration §6.2 execution book shared by backtest and serve.

Invariants protected here: the touch of the book sits at the pre-registered
execution price for each signal kind (§5.3, §6.2), and the backtest replay
uses the very same class serve imports (one implementation, ADR-011 D-01).

Reference: openspec/changes/353-fill-kernel-in-serve/change.md R-2;
backtest-driver-mvp1-phase2 change.md A-2.1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from hermes.application.backtest.replay import ExecutionModel as ReplayExecutionModel
from hermes.application.execution.prereg_book import ExecutionModel
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import close_time_of
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_OPEN = datetime(2026, 10, 4, tzinfo=UTC)
_BAR = Bar(
    pair=_BTC,
    interval=KlineInterval.H4,
    open_time=_OPEN,
    open=Decimal("100"),
    high=Decimal("101"),
    low=Decimal("99"),
    close=Decimal("100"),
    volume=Decimal("1000"),
    close_time=close_time_of(_OPEN, KlineInterval.H4),
)


def _signal(kind: SignalKind, reason: str | None = None) -> StrategySignal:
    return StrategySignal(
        kind=kind,
        side=SignalSide.LONG,
        entry_price=Decimal("100") if kind is SignalKind.ENTRY else None,
        stop_price=Decimal("90"),
        atr_context=Decimal("2"),
        signal_time=_BAR.close_time,
        exit_reason=reason,
    )


def test_entry_book_asks_start_five_bps_above_the_close() -> None:
    """§6.2: entry at close x 1.0005."""
    book = ExecutionModel().book(_signal(SignalKind.ENTRY), _BAR)
    assert book is not None
    assert book.asks[0].price == Decimal("100.05")


def test_chandelier_exit_book_bids_start_fifteen_bps_below_the_stop() -> None:
    """§6.2: stop exit at stop x 0.9985."""
    book = ExecutionModel().book(_signal(SignalKind.EXIT, "chandelier"), _BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("89.865")


def test_regime_exit_book_bids_start_five_bps_below_the_close() -> None:
    """A non-stop exit sells at close x 0.9995."""
    book = ExecutionModel().book(_signal(SignalKind.EXIT, "regime_break"), _BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("99.95")


def test_backtest_replay_uses_the_same_execution_model_class() -> None:
    """One §6.2 implementation: the replay's name is this class."""
    assert ReplayExecutionModel is ExecutionModel
