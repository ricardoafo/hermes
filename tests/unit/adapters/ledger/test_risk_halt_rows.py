"""Unit tests for the ledger's halt and kill rows (#749).

Invariants under test:

- a recorded halt reads back equal to what was appended, through the shared
  payload parser the status reader uses;
- ``has_risk_halt`` finds a row only for the same strategy, pair, kind, rule
  and signal time, so a re-evaluated bar is recorded once;
- ``halts_since`` returns halts and kills at or after its bound, newest
  first, and no other event type.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hermes.adapters.ledger.risk_halt_rows import risk_halt_from_payload
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.domain.events.risk_halt_recorded import HaltKind, RiskHaltRecorded
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.support.status_ledger import BTC, LAST_4H, signal, write_ledger

_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)


def _halt(
    hours: int = 0,
    *,
    pair: Pair = BTC,
    kind: HaltKind = HaltKind.HALT,
    rule: str = "RO-04",
    strategy: str = "swing-donchian-4h",
) -> RiskHaltRecorded:
    return RiskHaltRecorded(
        occurred_at=LAST_4H - timedelta(hours=hours),
        correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        strategy_id=StrategyId(strategy),
        pair=pair,
        kind=kind,
        rule=rule,
        reason="stale_orderbook",
        pre_registration_ref="§10",
    )


@pytest.fixture
def ledger() -> SqliteLedger:
    """Return a migrated in-memory ledger."""
    return SqliteLedger(sqlite3.connect(":memory:"))


def test_a_recorded_halt_is_found(ledger: SqliteLedger) -> None:
    """The row appended for a halt is found again by the same record."""
    record = _halt()
    ledger.append_event(record)
    assert ledger.has_risk_halt(record) is True


@pytest.mark.parametrize(
    "other",
    [
        _halt(4),
        _halt(pair=_ETH),
        _halt(kind=HaltKind.KILL),
        _halt(rule="RO-08"),
        _halt(strategy="other-strategy"),
    ],
    ids=["time", "pair", "kind", "rule", "strategy"],
)
def test_a_halt_differing_in_one_key_is_not_found(
    ledger: SqliteLedger, other: RiskHaltRecorded
) -> None:
    """Each of the five keys tells halts apart."""
    ledger.append_event(_halt())
    assert ledger.has_risk_halt(other) is False


def test_the_status_reader_lists_halts_newest_first_from_the_bound(tmp_path: Path) -> None:
    """Halts before the bound and other event types are left out; the rest read back equal."""
    path = write_ledger(
        tmp_path / "hermes.sqlite3", signals=(signal(BTC, SignalKind.ENTRY, LAST_4H),)
    )
    old, middle, newest = _halt(48), _halt(8, kind=HaltKind.KILL, rule="max_drawdown"), _halt(0)
    connection = sqlite3.connect(path)
    writer = SqliteLedger(connection)
    for record in (middle, old, newest):
        writer.append_event(record)
    connection.close()

    with SqliteStatusReader.open(path) as reader:
        halts = reader.halts_since(LAST_4H - timedelta(hours=24))

    assert halts == (newest, middle)


def test_the_payload_parser_refuses_a_row_with_figures() -> None:
    """A tampered row quoting a figure fails to parse instead of reaching a surface."""
    payload = {
        "occurred_at": datetime(2026, 10, 17, tzinfo=UTC).isoformat(),
        "correlation_id": "01JABCDEFGHJKMNPQRSTVWXYZ0",
        "event_seq": 0,
        "strategy_id": {"value": "swing-donchian-4h"},
        "pair": {"base": "BTC", "quote": "USDT"},
        "kind": "kill",
        "rule": "max_drawdown",
        "reason": "drawdown 16%",
        "pre_registration_ref": "§10",
    }
    with pytest.raises(ValueError, match="reason"):
        risk_halt_from_payload(payload)
