"""Tests for the ledger reads behind the missed-bar budget (issue #1117).

Invariants protected here: a bar counts once however many ``BarMissed`` rows it
has; distinct pairs, intervals and close times each count; a crossing alert is
found by its source and only by its source.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.bar_missed import BarMissed
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_CLOSE = datetime(2024, 10, 29, 4, 0, tzinfo=UTC)
_CORRELATION = "01JABCDEFGHJKMNPQRSTVWXYZ0"


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Return a ledger over an in-memory database, closed afterwards."""
    connection = sqlite3.connect(":memory:")
    yield SqliteLedger(connection)
    connection.close()


def _missed(
    pair: Pair = _BTC, interval: KlineInterval = KlineInterval.H4, close: datetime = _CLOSE
) -> BarMissed:
    return BarMissed(
        occurred_at=close,
        correlation_id=_CORRELATION,
        pair=pair,
        interval=interval,
        handled_at=close + timedelta(hours=6),
    )


def _anomaly(source: str) -> AnomalyDetected:
    return AnomalyDetected(
        occurred_at=_CLOSE,
        correlation_id=_CORRELATION,
        source=source,
        severity=AnomalySeverity.WARNING,
        reason="reason",
    )


def test_an_empty_ledger_has_missed_no_bar(ledger: SqliteLedger) -> None:
    """Invariant: with no ``BarMissed`` row the count is 0."""
    assert ledger.missed_bar_count() == 0


def test_a_bar_re_handled_after_a_crash_counts_once(ledger: SqliteLedger) -> None:
    """Invariant: two rows for one bar (crash before ``BarProcessed``) spend one bar."""
    ledger.append_event(_missed())
    ledger.append_event(_missed())
    assert ledger.missed_bar_count() == 1


def test_each_pair_interval_and_close_time_counts_as_its_own_bar(ledger: SqliteLedger) -> None:
    """Invariant: bars differing only in pair, interval or close time are 3 more bars."""
    for event in (
        _missed(),
        _missed(pair=_ETH),
        _missed(interval=KlineInterval.D1),
        _missed(close=_CLOSE + timedelta(hours=4)),
    ):
        ledger.append_event(event)
    assert ledger.missed_bar_count() == 4


def test_an_anomaly_is_found_only_by_its_own_source(ledger: SqliteLedger) -> None:
    """Invariant: a restart report never reads as a budget alert; a sent alert is found."""
    ledger.append_event(_anomaly("serve.restart"))
    before = ledger.has_anomaly_from("serve.missed_bar_budget")
    ledger.append_event(_anomaly("serve.missed_bar_budget"))
    assert (before, ledger.has_anomaly_from("serve.missed_bar_budget")) == (False, True)
