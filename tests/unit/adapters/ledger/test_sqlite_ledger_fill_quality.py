"""Tests for the ledger's fill-quality read (change 353-fill-kernel-in-serve).

Invariants protected here: a ``FillQualityRecorded`` row comes back field for
field (including the optional model price, drift, mid and slippage), only the
asked strategy's rows are returned, in time order, and an unreadable row
raises instead of being skipped. The SHADOW_CERTIFIED gate and RO-03 read
these rows, so a lossy round trip would corrupt both.

All run against SQLite ``:memory:``.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.events.fill_quality_recorded import BookSource, FillQualityRecorded
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_STRATEGY = StrategyId("swing-donchian-4h")
_T0 = datetime(2026, 10, 4, 4, 0, 6, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Return a ledger over an in-memory database, closed afterwards."""
    connection = sqlite3.connect(":memory:")
    yield SqliteLedger(connection)
    connection.close()


def _quality(
    at: datetime,
    *,
    strategy: StrategyId = _STRATEGY,
    model_price: Decimal | None = Decimal("84800.5"),
    drift_bps: Decimal | None = Decimal("3.9"),
    arrival_price: Decimal | None = None,
    delta_ms: int | None = None,
) -> FillQualityRecorded:
    """Build a live-book quality row at ``at``."""
    return FillQualityRecorded(
        occurred_at=at,
        correlation_id=_CID,
        strategy_id=strategy,
        pair=_BTC,
        side=TradeSide.SELL,
        qty=Decimal("0.00039"),
        book_source=BookSource.LIVE,
        book_time=at,
        fill_price=Decimal("84833.11"),
        model_price=model_price,
        drift_bps=drift_bps,
        book_mid=Decimal("84833.115"),
        slippage=Decimal("0.00000005"),
        arrival_price=arrival_price,
        delta_ms=delta_ms,
    )


def test_a_row_round_trips_field_for_field(ledger: SqliteLedger) -> None:
    """What the executor appends is exactly what the gate reads back."""
    row = _quality(_T0)
    ledger.append_event(row)

    assert ledger.fill_quality_events(_STRATEGY) == (row,)


def test_a_row_without_a_model_price_round_trips(ledger: SqliteLedger) -> None:
    """The optional model price and drift stay ``None``, not ``"None"`` or zero."""
    row = _quality(_T0, model_price=None, drift_bps=None)
    ledger.append_event(row)

    assert ledger.fill_quality_events(_STRATEGY) == (row,)


def test_rows_come_back_oldest_first(ledger: SqliteLedger) -> None:
    """Insert order does not decide the order; ``occurred_at`` does."""
    later, earlier = _quality(_T0 + timedelta(hours=4)), _quality(_T0)
    ledger.append_event(later)
    ledger.append_event(earlier)

    assert ledger.fill_quality_events(_STRATEGY) == (earlier, later)


def test_another_strategys_rows_are_not_read(ledger: SqliteLedger) -> None:
    """Rows are bucketed by strategy id."""
    ledger.append_event(_quality(_T0, strategy=StrategyId("other")))

    assert ledger.fill_quality_events(_STRATEGY) == ()


def test_an_unreadable_payload_raises_ledger_error(ledger: SqliteLedger) -> None:
    """A corrupted row is an error, not a silently missing fill."""
    ledger.append_event(_quality(_T0))
    connection = ledger._conn
    connection.execute(
        "UPDATE events SET payload_json = json_remove(payload_json, '$.fill_price') "
        "WHERE event_type = 'FillQualityRecorded'"
    )
    connection.commit()

    with pytest.raises(LedgerError, match="unreadable payload"):
        ledger.fill_quality_events(_STRATEGY)


def test_arrival_price_and_delta_round_trip(ledger: SqliteLedger) -> None:
    """The v2 fields are stored and read back exactly, Decimal and int."""
    row = _quality(_T0, arrival_price=Decimal("84990.5"), delta_ms=6012)
    ledger.append_event(row)

    (read,) = ledger.fill_quality_events(_STRATEGY)
    assert (read.arrival_price, read.delta_ms) == (Decimal("84990.5"), 6012)


def test_a_v1_row_reads_back_with_the_new_fields_none(ledger: SqliteLedger) -> None:
    """A row written before schema v2 (no keys, version 1) stays readable."""
    ledger.append_event(_quality(_T0))
    ledger._conn.execute(
        "UPDATE events SET event_schema_version = 1, "
        "payload_json = json_remove(payload_json, '$.arrival_price', '$.delta_ms') "
        "WHERE event_type = 'FillQualityRecorded'"
    )
    ledger._conn.commit()

    (read,) = ledger.fill_quality_events(_STRATEGY)
    assert (read.arrival_price, read.delta_ms, read.fill_price) == (
        None,
        None,
        Decimal("84833.11"),
    )


def test_an_unknown_schema_version_raises_ledger_error(ledger: SqliteLedger) -> None:
    """A row from a schema this reader does not know is refused, not guessed."""
    ledger.append_event(_quality(_T0))
    ledger._conn.execute(
        "UPDATE events SET event_schema_version = 9 WHERE event_type = 'FillQualityRecorded'"
    )
    ledger._conn.commit()

    with pytest.raises(LedgerError, match="unsupported FillQualityRecorded schema version 9"):
        ledger.fill_quality_events(_STRATEGY)
