"""Every read-only ledger reader answers, or raises ``LedgerError``, at every schema (#1178).

A reader can meet a ledger at any shipped schema: a console or ``hermes
status`` started on a box whose serve has not migrated yet, or a tax pack
run over an archived ledger. Callers handle ``LedgerError`` and nothing
else, so a query against a table or column that schema lacks must surface
as ``LedgerError``, never as a raw ``sqlite3.Error``.

Invariants:

- for the empty file and for a ledger migrated through each shipped
  migration in turn, every query of every reader returns a value or raises
  ``LedgerError``;
- every reader with an ``open(path)`` lifecycle is a ``ReadOnlyLedgerReader``,
  so the open and close rules exist once (the guard against the copies
  coming back).
"""

import inspect
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

import hermes.adapters
from hermes.adapters.console.ledger_activity import LedgerActivityReader
from hermes.adapters.ledger.anomaly_reader import SqliteAnomalyReader
from hermes.adapters.ledger.connection import ReadOnlyLedgerReader
from hermes.adapters.ledger.filing_reader import LedgerFilingReader
from hermes.adapters.ledger.migration_runner import MIGRATIONS_DIR, apply_migrations
from hermes.adapters.ledger.parity_reader import SqliteParityReader
from hermes.adapters.ledger.reconciliation_reader import SqliteReconciliationReader
from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.adapters.ledger.supervisor_reader import SqliteSupervisorReader
from hermes.adapters.ledger.trade_rates_reader import LedgerTradeRates
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_SHIPPED = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
_SCHEMAS = ["empty", *_SHIPPED]
_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_H4 = KlineInterval("4h")
_FROM = datetime(2025, 1, 1, tzinfo=UTC)
_TO = datetime(2026, 1, 1, tzinfo=UTC)
_SWING = StrategyId("swing")

type _Query = Callable[[Path], object]


def _with[R: ReadOnlyLedgerReader](reader: type[R], ask: Callable[[R], object]) -> _Query:
    def query(db: Path) -> object:
        with reader.open(db) as opened:
            return ask(opened)

    return query


_QUERIES: dict[str, _Query] = {
    "parity trades": _with(SqliteParityReader, lambda r: r.trades(_SWING)),
    "parity missed bars": _with(SqliteParityReader, lambda r: r.missed_bars_between(_FROM, _TO)),
    "parity bars": _with(
        SqliteParityReader, lambda r: r.bars_closing_between(_PAIR, _H4, _FROM, _TO)
    ),
    "filing disposals": _with(LedgerFilingReader, lambda r: r.disposal_pieces(_FROM, _TO)),
    "filing purchases": _with(LedgerFilingReader, lambda r: r.purchases(_FROM, _TO)),
    "filing unit trades": _with(LedgerFilingReader, lambda r: r.unit_trades(_TO)),
    "filing last close": _with(LedgerFilingReader, lambda r: r.last_close(_PAIR, _TO)),
    "reconciliation trades": _with(SqliteReconciliationReader, lambda r: r.trades()),
    "reconciliation lots": _with(SqliteReconciliationReader, lambda r: r.open_lot_quantities()),
    "reconciliation events": _with(SqliteReconciliationReader, lambda r: r.trade_events()),
    "status last close": _with(SqliteStatusReader, lambda r: r.last_processed_close(_PAIR, _H4)),
    "status bars": _with(SqliteStatusReader, lambda r: r.count_bars_since(_H4, _FROM)),
    "status signals": _with(SqliteStatusReader, lambda r: r.count_signals_since(_FROM)),
    "status round trips": _with(SqliteStatusReader, lambda r: r.round_trips(_SWING)),
    "status open entries": _with(SqliteStatusReader, lambda r: r.open_entries()),
    "status halts": _with(SqliteStatusReader, lambda r: r.halts_since(_FROM)),
    "status clock skew": _with(SqliteStatusReader, lambda r: r.last_clock_skew()),
    "status bar check": _with(SqliteStatusReader, lambda r: r.bar_check_seconds(10)),
    "supervisor signals": _with(SqliteSupervisorReader, lambda r: r.signals_since(_FROM)),
    "supervisor bars": _with(SqliteSupervisorReader, lambda r: r.bars_since(_FROM)),
    "supervisor fills": _with(SqliteSupervisorReader, lambda r: r.fills_since(_FROM)),
    "trade rates": _with(LedgerTradeRates, lambda r: r.eur_rates()),
    "anomalies": _with(SqliteAnomalyReader, lambda r: r.since(_FROM)),
    "console activity": _with(LedgerActivityReader, lambda r: r.items_since(_FROM)),
}


@pytest.fixture(scope="module", params=_SCHEMAS)
def ledger_at(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a ledger migrated through one shipped migration, or an empty file."""
    last: str = request.param
    work = tmp_path_factory.mktemp(last.removesuffix(".sql"))
    db = work / "hermes.sqlite3"
    with closing(sqlite3.connect(db)) as conn:
        if last != "empty":
            older = work / "migrations"
            older.mkdir()
            for name in _SHIPPED:
                if name <= last:
                    shutil.copy(MIGRATIONS_DIR / name, older / name)
            apply_migrations(conn, older)
    return db


@pytest.mark.parametrize("query", sorted(_QUERIES))
def test_every_reader_query_answers_or_raises_ledger_error(ledger_at: Path, query: str) -> None:
    """A query on a schema without its table fails as LedgerError, never as raw sqlite3.Error."""
    leaked = ""
    try:
        _QUERIES[query](ledger_at)
    except LedgerError:
        pass
    except sqlite3.Error as exc:
        leaked = f"{type(exc).__name__}: {exc}"

    assert leaked == ""


def _reader_classes() -> list[type]:
    found: list[type] = []
    root = Path(hermes.adapters.__file__).parent
    for path in sorted(root.rglob("*.py")):
        module_name = ".".join(
            ("hermes", "adapters", *path.relative_to(root).with_suffix("").parts)
        )
        module = __import__(module_name, fromlist=["_"])
        found.extend(
            cls
            for _, cls in inspect.getmembers(module, inspect.isclass)
            if cls.__module__ == module_name and "open" in vars(cls) and "close" in vars(cls)
        )
    return found


def test_no_reader_keeps_its_own_open_and_close() -> None:
    """Only the base defines the read-only open/close pair; every reader inherits it."""
    own_lifecycle = sorted(
        cls.__qualname__ for cls in _reader_classes() if cls is not ReadOnlyLedgerReader
    )

    assert own_lifecycle == []
