"""Unit tests for the ledger's exchange-stop rows (#1080).

Invariants under test:

- a recorded row reads back equal to what was appended, Decimals and times exact;
- the latest row is per strategy and pair, in append order;
- a failure row never hides the stop the ledger last knew to rest, so a
  failed move still finds and cancels the old stop next time;
- every event of a pass is appended, the alert beside the row.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.exchange_stop_rows import SqliteExchangeStopJournal
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.exchange_stop_changed import ExchangeStopChanged, StopAction
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
STRATEGY = StrategyId("swing-donchian-4h")
AT = datetime(2024, 10, 29, 4, 0, 5, tzinfo=UTC)
ENTRY = datetime(2024, 10, 29, 3, 59, 59, 999000, tzinfo=UTC)


def _row(
    action: StopAction = StopAction.PLACED,
    *,
    order_id: str | None = "BTCUSDT:1000",
    pair: Pair = BTC,
    strategy: StrategyId = STRATEGY,
    stop: str = "140.12345678",
    minutes: int = 0,
) -> ExchangeStopChanged:
    return ExchangeStopChanged(
        occurred_at=AT + timedelta(minutes=minutes),
        correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        strategy_id=strategy,
        pair=pair,
        action=action,
        entry_time=ENTRY,
        order_id=order_id,
        qty=Decimal("0.50000000"),
        stop_price=Decimal(stop),
        reason="protect_position",
    )


@pytest.fixture
def journal() -> Iterator[SqliteExchangeStopJournal]:
    """Yield a journal over a migrated in-memory ledger, closed after the test."""
    with closing(sqlite3.connect(":memory:")) as connection:
        yield SqliteExchangeStopJournal(SqliteLedger(connection), connection)


def test_a_recorded_row_reads_back_unchanged(journal: SqliteExchangeStopJournal) -> None:
    """Every field round-trips: the restart compares the exchange against exactly this."""
    row = _row()
    journal.record_exchange_stops([row])
    assert journal.latest_exchange_stop(STRATEGY, BTC) == row


def test_the_latest_row_is_the_last_appended(journal: SqliteExchangeStopJournal) -> None:
    """Append order decides, even when a later row carries an earlier time."""
    journal.record_exchange_stops([_row(minutes=5), _row(order_id="BTCUSDT:1001", stop="142")])
    latest = journal.latest_exchange_stop(STRATEGY, BTC)
    assert latest is not None
    assert latest.order_id == "BTCUSDT:1001"


@pytest.mark.parametrize(
    "other",
    [_row(pair=ETH, order_id="ETHUSDT:1"), _row(strategy=StrategyId("swing-mvp1-v2"))],
    ids=["other pair", "other strategy"],
)
def test_another_pair_or_strategy_is_not_this_pairs_stop(
    journal: SqliteExchangeStopJournal, other: ExchangeStopChanged
) -> None:
    """ETH's stop, or another strategy's, reads as its own and never as BTC's."""
    journal.record_exchange_stops([other])
    found = (
        journal.latest_exchange_stop(STRATEGY, BTC),
        journal.latest_exchange_stop(other.strategy_id, other.pair),
    )
    assert found == (None, other)


def test_a_failure_row_does_not_hide_the_resting_stop(journal: SqliteExchangeStopJournal) -> None:
    """After a failed move the ledger still names the stop that rests."""
    placed = _row()
    journal.record_exchange_stops([placed, _row(StopAction.FAILED, order_id=None)])
    assert journal.latest_exchange_stop(STRATEGY, BTC) == placed


def test_a_pass_appends_its_alert_beside_its_row(journal: SqliteExchangeStopJournal) -> None:
    """The alert is in the ledger too, so ``hermes status`` can show it."""
    alert = AnomalyDetected(
        occurred_at=AT,
        correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        source="serve.exchange_stop",
        severity=AnomalySeverity.CRITICAL,
        reason="the exchange stop on BTC/USDT could not be kept (refused)",
    )
    journal.record_exchange_stops([_row(StopAction.FAILED, order_id=None), alert])
    types = [r[0] for r in journal.connection.execute("SELECT event_type FROM events ORDER BY id")]
    assert types == ["ExchangeStopChanged", "AnomalyDetected"]


def test_an_unreadable_row_is_a_ledger_error(journal: SqliteExchangeStopJournal) -> None:
    """A row whose payload lost a field fails loudly instead of reading as no stop."""
    journal.record_exchange_stops([_row()])
    journal.connection.execute(
        "UPDATE events SET payload_json = json_remove(payload_json, '$.order_id')"
    )
    with pytest.raises(LedgerError, match="unreadable payload"):
        journal.latest_exchange_stop(STRATEGY, BTC)
