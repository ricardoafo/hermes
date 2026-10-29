"""Tests for the read-only SQLite supervisor reader (#470).

Invariants protected here:

- the reader is a structural ``SupervisorReadPort`` and cannot write;
- ``signals_since`` is inclusive of its bound, oldest first, and carries no
  price, stop or size, only the exit rule that fired;
- ``bars_since`` counts handled bars per stream from its bound on;
- ``fills_since`` returns pair, side and time only, never a price, size or fee;
- an unreadable payload is a ``LedgerError``, not a crash.
"""

import dataclasses
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.ledger.supervisor_reader import SqliteSupervisorReader
from hermes.domain.errors import LedgerError
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.supervisor import BarCount, FillFact, SignalFact, SupervisorReadPort
from tests.support.status_ledger import (
    BTC,
    ETH,
    LAST_4H,
    SOL,
    bar_processed,
    signal,
    write_ledger,
)

_FILL_PRICE = Decimal("2618.45")


def _fill(pair: Pair, side: TradeSide, at: datetime) -> TradeExecuted:
    return TradeExecuted(
        occurred_at=at,
        correlation_id="01J0000000000000000000FILL",
        pair=pair,
        side=side,
        qty=Quantity(amount=Decimal("0.0123"), asset=pair.base),
        price=Price(amount=_FILL_PRICE, pair=pair),
        fee=Money(amount=Decimal("0.031"), currency=Currency.USDT),
        strategy_id=StrategyId("swing-donchian-4h"),
    )


def _append(path: Path, *events: DomainEvent) -> None:
    connection = sqlite3.connect(path)
    try:
        ledger = SqliteLedger(connection)
        for event in events:
            ledger.append_event(event)
    finally:
        connection.close()


def test_reader_satisfies_the_port() -> None:
    """The adapter is a structural SupervisorReadPort."""
    assert issubclass(SqliteSupervisorReader, SupervisorReadPort)


def test_a_write_through_the_reader_connection_fails(tmp_path: Path) -> None:
    """mode=ro means the supervisor can never change the ledger it reads."""
    path = write_ledger(tmp_path / "hermes.sqlite3")

    with SqliteSupervisorReader.open(path) as reader, pytest.raises(sqlite3.OperationalError):
        reader._conn.execute("DELETE FROM events")


def test_signals_since_is_inclusive_and_oldest_first(tmp_path: Path) -> None:
    """A signal exactly at ``since`` is included; earlier ones are not."""
    early = LAST_4H - timedelta(hours=8)
    at = LAST_4H - timedelta(hours=4)
    path = write_ledger(
        tmp_path / "hermes.sqlite3",
        signals=(
            signal(ETH, SignalKind.EXIT, LAST_4H),
            signal(BTC, SignalKind.ENTRY, early),
            signal(ETH, SignalKind.ENTRY, at),
        ),
    )

    with SqliteSupervisorReader.open(path) as reader:
        facts = reader.signals_since(at)

    assert facts == (
        SignalFact(pair=ETH, kind="entry", occurred_at=at),
        SignalFact(pair=ETH, kind="exit", occurred_at=LAST_4H, exit_reason="chandelier"),
    )


def test_signal_facts_carry_no_price_fields() -> None:
    """SignalFact has pair, kind, time and exit rule: no price, stop, ATR or size."""
    assert [f.name for f in dataclasses.fields(SignalFact)] == [
        "pair",
        "kind",
        "occurred_at",
        "exit_reason",
    ]


def test_bars_since_counts_each_stream_from_its_bound(tmp_path: Path) -> None:
    """Bars closing at or after ``since`` count per pair and interval; the older 4h and 1d don't."""
    path = write_ledger(tmp_path / "hermes.sqlite3")
    _append(
        path,
        bar_processed(BTC, KlineInterval.H4, LAST_4H - timedelta(hours=4)),
        bar_processed(BTC, KlineInterval.H4, LAST_4H - timedelta(hours=8)),
    )

    with SqliteSupervisorReader.open(path) as reader:
        counts = reader.bars_since(LAST_4H - timedelta(hours=4) - timedelta(milliseconds=1))

    assert counts == (
        BarCount(pair=BTC, interval=KlineInterval.H4, count=2),
        BarCount(pair=ETH, interval=KlineInterval.H4, count=1),
        BarCount(pair=SOL, interval=KlineInterval.H4, count=1),
    )


def test_fills_since_returns_pair_side_and_time_only(tmp_path: Path) -> None:
    """Fills from the bound on come back oldest first, with no price, size or fee."""
    early, buy, sell = (LAST_4H - timedelta(hours=h) for h in (12, 8, 4))
    path = write_ledger(tmp_path / "hermes.sqlite3")
    _append(
        path,
        _fill(ETH, TradeSide.SELL, sell),
        _fill(BTC, TradeSide.BUY, early),
        _fill(ETH, TradeSide.BUY, buy),
    )

    with SqliteSupervisorReader.open(path) as reader:
        fills = reader.fills_since(buy)

    assert fills == (
        FillFact(pair=ETH, side="BUY", occurred_at=buy),
        FillFact(pair=ETH, side="SELL", occurred_at=sell),
    )
    assert [f.name for f in dataclasses.fields(FillFact)] == ["pair", "side", "occurred_at"]


def test_an_unreadable_signal_payload_is_a_ledger_error(tmp_path: Path) -> None:
    """A malformed row surfaces as LedgerError so the tool can refuse cleanly."""
    path = write_ledger(tmp_path / "hermes.sqlite3")
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO events (event_type, event_schema_version, correlation_id, occurred_at, "
        "payload_json, recorded_at) VALUES ('SignalEmitted', 1, 'c', ?, '{}', ?)",
        (LAST_4H.isoformat(), LAST_4H.isoformat()),
    )
    connection.commit()
    connection.close()

    with SqliteSupervisorReader.open(path) as reader, pytest.raises(LedgerError):
        reader.signals_since(LAST_4H - timedelta(days=1))
