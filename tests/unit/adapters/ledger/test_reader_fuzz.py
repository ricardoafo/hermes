"""Fuzz properties for every ledger reader (issue #842).

Each read-only reader returns its value or raises ``LedgerError``, nothing
else, whatever one cell of the ledger holds: SQLite stores any type in any
column, so a bad migration, a hand edit or a disk fault can leave a NULL, a
number, a blob, a naive time, ``NaN`` or broken JSON where the reader expects
a decimal string. The callers (``hermes status``, the tax pack, parity,
reconciliation, the supervisor) only catch ``LedgerError``. A value a reader
accepts holds only finite decimals and timezone-aware times.

The template ledger is written through the production write path
(``standard_ledger`` plus the status-ledger events); each example copies it
and corrupts one cell, with the append-only triggers and CHECK constraints
lifted so the corruption can be written at all.
"""

import json
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.ledger.filing_reader import LedgerFilingReader
from hermes.adapters.ledger.live_bar_reader import SqliteLiveBarReader
from hermes.adapters.ledger.parity_reader import SqliteParityReader
from hermes.adapters.ledger.reconciliation_reader import SqliteReconciliationReader
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.adapters.ledger.supervisor_reader import SqliteSupervisorReader
from hermes.adapters.ledger.trade_rates_reader import LedgerTradeRates
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.support.status_ledger import BTC, LAST_4H, signal
from tests.unit.adapters.ledger.filing_ledger import BTC_USDT, standard_ledger
from tests.unit.adapters.wire_fuzz import (
    FUZZ_SLOW,
    accepted,
    clean_violations,
    foreign_error,
    mutated,
    numeric_strings,
)

_FROM: Final = datetime(2026, 1, 1, tzinfo=UTC)
_TO: Final = datetime(2027, 1, 1, tzinfo=UTC)
_STRATEGY: Final = StrategyId("swing-donchian-4h")
_H4: Final = KlineInterval.H4

type _Read = Callable[[Path], object]

_READS: Final[dict[str, _Read]] = {}


def _read(name: str) -> Callable[[_Read], _Read]:
    def register(read: _Read) -> _Read:
        _READS[name] = read
        return read

    return register


@_read("filing.disposal_pieces")
def _disposals(path: Path) -> object:
    with LedgerFilingReader.open(path) as reader:
        return reader.disposal_pieces(_FROM, _TO)


@_read("filing.purchases")
def _purchases(path: Path) -> object:
    with LedgerFilingReader.open(path) as reader:
        return reader.purchases(_FROM, _TO)


@_read("filing.unit_trades")
def _unit_trades(path: Path) -> object:
    with LedgerFilingReader.open(path) as reader:
        return reader.unit_trades(_TO)


@_read("filing.last_close")
def _last_close(path: Path) -> object:
    with LedgerFilingReader.open(path) as reader:
        return reader.last_close(BTC_USDT, _TO)


@_read("live_bars")
def _live_bars(path: Path) -> object:
    return SqliteLiveBarReader(path).live_bars(BTC_USDT, _H4, _FROM, _TO)


@_read("ledger.live_bars")
def _ledger_live_bars(path: Path) -> object:
    with closing(sqlite3.connect(path)) as connection:
        return SqliteLedger(connection).live_bars(BTC_USDT, _H4, _FROM, _TO)


@_read("parity.bars_closing_between")
def _parity_bars(path: Path) -> object:
    with SqliteParityReader.open(path) as reader:
        return reader.bars_closing_between(BTC_USDT, _H4, _FROM, _TO)


@_read("parity.signals_between")
def _parity_signals(path: Path) -> object:
    with SqliteParityReader.open(path) as reader:
        return reader.signals_between(_STRATEGY, _FROM, _TO)


@_read("parity.latest_signal")
def _parity_latest(path: Path) -> object:
    with SqliteParityReader.open(path) as reader:
        return reader.latest_signal(_STRATEGY, BTC, at_or_before=_TO)


@_read("parity.trades")
def _parity_trades(path: Path) -> object:
    with SqliteParityReader.open(path) as reader:
        return reader.trades(StrategyId("swing"))


@_read("reconciliation.trades")
def _recon_trades(path: Path) -> object:
    with SqliteReconciliationReader.open(path) as reader:
        return reader.trades()


@_read("reconciliation.open_lot_quantities")
def _recon_lots(path: Path) -> object:
    with SqliteReconciliationReader.open(path) as reader:
        return reader.open_lot_quantities()


@_read("reconciliation.trade_events")
def _recon_events(path: Path) -> object:
    with SqliteReconciliationReader.open(path) as reader:
        return reader.trade_events()


@_read("status.last_processed_close")
def _status_close(path: Path) -> object:
    with SqliteStatusReader.open(path) as reader:
        return reader.last_processed_close(BTC, _H4)


@_read("status.open_entries")
def _status_entries(path: Path) -> object:
    with SqliteStatusReader.open(path) as reader:
        return reader.open_entries()


@_read("supervisor.signals_since")
def _sup_signals(path: Path) -> object:
    with SqliteSupervisorReader.open(path) as reader:
        return reader.signals_since(_FROM)


@_read("supervisor.bars_since")
def _sup_bars(path: Path) -> object:
    with SqliteSupervisorReader.open(path) as reader:
        return reader.bars_since(_FROM)


@_read("supervisor.fills_since")
def _sup_fills(path: Path) -> object:
    with SqliteSupervisorReader.open(path) as reader:
        return reader.fills_since(_FROM)


@_read("trade_rates.eur_rates")
def _eur_rates(path: Path) -> object:
    with LedgerTradeRates.open(path) as reader:
        return reader.eur_rates()


_cells: Final = st.one_of(
    st.none(),
    st.integers(min_value=-(2**63), max_value=2**63 - 1),
    st.floats(allow_nan=False),
    numeric_strings,
    st.sampled_from(
        ["2026-03-02T10:00:00", "2026-13-45", "not a time", "XXX", "sell", "{}", "[]", "null"]
    ),
    st.text(max_size=8),
    st.binary(max_size=4),
)
"""What one corrupted cell may hold: every SQLite storage class."""


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write the template ledger once: trades, lots, bars and every event type the readers read."""
    path = tmp_path_factory.mktemp("ledger") / "template.sqlite3"
    standard_ledger(path)
    connection = sqlite3.connect(path)
    try:
        ledger = SqliteLedger(connection)
        ledger.append_event(signal(BTC, SignalKind.ENTRY, LAST_4H))
        ledger.append_event(signal(BTC, SignalKind.EXIT, LAST_4H.replace(hour=20)))
    finally:
        connection.close()
    return path


def _tables(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        filled = [n for n in names if connection.execute(f'SELECT 1 FROM "{n}"').fetchone()]  # noqa: S608 - names come from the template's own schema
        return sorted(filled)
    finally:
        connection.close()


def _corrupt(path: Path, data: st.DataObject) -> None:
    """Overwrite one cell of one row; JSON cells get a structural mutation instead."""
    connection = sqlite3.connect(path)
    try:
        for (trigger,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall():
            connection.execute(f'DROP TRIGGER "{trigger}"')
        connection.execute("PRAGMA ignore_check_constraints = ON")
        table = data.draw(st.sampled_from(_tables(path)), label="table")
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
        column = data.draw(st.sampled_from(columns), label="column")
        select_rowids = f'SELECT rowid FROM "{table}"'  # noqa: S608 - names come from the template's own schema
        rowids = [row[0] for row in connection.execute(select_rowids)]
        rowid = data.draw(st.sampled_from(rowids), label="rowid")
        (current,) = connection.execute(
            f'SELECT "{column}" FROM "{table}" WHERE rowid = ?',  # noqa: S608 - names come from the template's own schema
            (rowid,),
        ).fetchone()
        value = data.draw(_cells, label="value")
        if isinstance(current, str) and current.startswith("{"):
            value = json.dumps(data.draw(mutated(json.loads(current)), label="json"))
        try:
            connection.execute(
                f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',  # noqa: S608 - names come from the template's own schema
                (value, rowid),
            )
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()  # NOT NULL / UNIQUE: the example reads an intact ledger
    finally:
        connection.close()


def _corrupted_copy(template: Path, tmp_path: Path, data: st.DataObject) -> Path:
    path = tmp_path / "ledger.sqlite3"
    shutil.copyfile(template, path)
    _corrupt(path, data)
    return path


@pytest.mark.parametrize("read", sorted(_READS))
@FUZZ_SLOW
@given(data=st.data())
def test_a_ledger_reader_returns_or_raises_ledger_error(
    read: str, data: st.DataObject, template: Path, tmp_path: Path
) -> None:
    """Invariant: with any one cell corrupted, a reader returns or raises LedgerError."""
    path = _corrupted_copy(template, tmp_path, data)
    assert foreign_error(lambda: _READS[read](path), LedgerError) == ""


@pytest.mark.parametrize("read", sorted(_READS))
@FUZZ_SLOW
@given(data=st.data())
def test_a_ledger_reader_accepts_only_finite_numbers_and_aware_times(
    read: str, data: st.DataObject, template: Path, tmp_path: Path
) -> None:
    """Invariant: what a reader returns from a corrupted ledger has finite numbers, aware times."""
    path = _corrupted_copy(template, tmp_path, data)
    outcome = accepted(lambda: _READS[read](path), LedgerError)
    assert clean_violations(outcome, utc=False) == []
