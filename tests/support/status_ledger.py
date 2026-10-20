"""Build small ledger files for the ``hermes status`` tests.

The shadow universe (BTC, ETH, SOL against USDT at 4h and 1d) matches
``config/market_data.yaml``, so a ledger built here reads as "every stream
current" when each stream's last close is the latest boundary before ``now``.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

BTC: Final = Pair(base=Currency.BTC, quote=Currency.USDT)
ETH: Final = Pair(base=Currency.ETH, quote=Currency.USDT)
SOL: Final = Pair(base=Currency.SOL, quote=Currency.USDT)
PAIRS: Final = (BTC, ETH, SOL)
INTERVALS: Final = (KlineInterval.H4, KlineInterval.D1)
# Saturday 3 Oct 2026, 16:10 UTC (18:10 Madrid): the 16:00 4h bar is in.
NOW: Final = datetime(2026, 10, 3, 16, 10, tzinfo=UTC)
LAST_4H: Final = datetime(2026, 10, 3, 16, tzinfo=UTC)
LAST_1D: Final = datetime(2026, 10, 3, tzinfo=UTC)
_ONE_MS: Final = timedelta(milliseconds=1)
_CID: Final = "01J9Z8T3M5BQR2FVKN4XWY6PH1"
_STRATEGY: Final = StrategyId("swing-donchian-4h")


def bar_processed(pair: Pair, interval: KlineInterval, boundary: datetime) -> BarProcessed:
    """Return the row the loop writes for the bar closing at ``boundary``."""
    return BarProcessed(
        occurred_at=boundary - _ONE_MS, correlation_id=_CID, pair=pair, interval=interval
    )


def signal(
    pair: Pair,
    kind: SignalKind,
    at: datetime,
    price: str = "2618.45",
    *,
    correlation_id: str = _CID,
) -> SignalEmitted:
    """Return a long entry or exit signal for ``pair`` at ``at``."""
    entry = Decimal(price)
    return SignalEmitted.from_signal(
        StrategySignal(
            kind=kind,
            side=SignalSide.LONG,
            entry_price=entry,
            stop_price=entry - Decimal("70"),
            atr_context=Decimal("25"),
            signal_time=at,
            exit_reason=None if kind is SignalKind.ENTRY else "chandelier",
        ),
        strategy_id=_STRATEGY,
        pair=pair,
        correlation_id=correlation_id,
    )


def fill(signal_: SignalEmitted) -> TradeExecuted:
    """Return the simulated fill of ``signal_``: a BUY for an entry, a SELL for an exit."""
    pair = signal_.pair
    return TradeExecuted(
        occurred_at=signal_.occurred_at + timedelta(seconds=5),
        correlation_id=signal_.correlation_id,
        pair=pair,
        side=TradeSide.BUY if signal_.kind is SignalKind.ENTRY else TradeSide.SELL,
        qty=Quantity(amount=Decimal("0.01"), asset=pair.base),
        price=Price(amount=signal_.entry_price or Decimal("2618.45"), pair=pair),
        fee=Money(amount=Decimal("0.01"), currency=pair.quote),
        strategy_id=_STRATEGY,
    )


def write_ledger(
    path: Path,
    *,
    last_4h: datetime | None = LAST_4H,
    last_1d: datetime | None = LAST_1D,
    signals: tuple[SignalEmitted, ...] = (),
    fills: tuple[TradeExecuted, ...] = (),
) -> Path:
    """Write a migrated ledger at ``path`` with every stream at the given closes.

    Args:
        path: Where to create the SQLite file.
        last_4h: Boundary of the newest processed 4h bar, or ``None`` for none.
        last_1d: Boundary of the newest processed 1d bar, or ``None`` for none.
        signals: Signal rows to append.
        fills: Fill rows to append after the signals (see :func:`fill`).

    Returns:
        ``path``.

    """
    connection = sqlite3.connect(path)
    try:
        ledger = SqliteLedger(connection)
        for pair in PAIRS:
            if last_4h is not None:
                ledger.append_event(bar_processed(pair, KlineInterval.H4, last_4h))
            if last_1d is not None:
                ledger.append_event(bar_processed(pair, KlineInterval.D1, last_1d))
        for event in (*signals, *fills):
            ledger.append_event(event)
    finally:
        connection.close()
    return path
