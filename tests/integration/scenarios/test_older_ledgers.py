"""Boot serve over a ledger left at each older schema version.

Every deploy boots the new release over the ledger the old one wrote. Serve
applies pending additive migrations at start, and stops at a pending
destructive one without writing a row until ``hermes migrate`` has
snapshotted the ledger and applied it. Either way, the rows the
older release wrote stay as they were, and serve then trades exactly as it
does on a fresh ledger.

Each ledger holds what the shadow ledger held before its first trade: the
last bar the older release handled before the deploy, as a ``BarProcessed``
event and, once the table exists, a ``live_bars`` row.

Invariant protected: from every older schema, serve reaches the current
schema without losing a row and then enters and exits as on a fresh ledger;
a pending destructive migration stops the boot before any row is written.

Reference: docs/guides/ledger-migrations.md; issue #692.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import (
    DESTRUCTIVE_MARKER,
    MIGRATIONS_DIR,
    apply_migrations,
    migrate_with_snapshot,
)
from hermes.adapters.ledger.sqlite_ledger import _event_to_json, _iso_utc
from hermes.domain.errors import LedgerError
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from tests.integration.scenarios import markets
from tests.integration.scenarios.conftest import RunScenario
from tests.integration.scenarios.ledger import outline

pytestmark = pytest.mark.integration

_SHIPPED = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
_OLDER = _SHIPPED[:-1]


def _last_bar_before_boot(scenario: markets.Scenario) -> Bar:
    closed = [
        bar
        for bar in scenario.market.bars(markets.BTC, KlineInterval.H4)
        if bar.close_time < scenario.live_from
    ]
    return closed[-1]


def _ledger_at(db: Path, last: str, bar: Bar, work: Path) -> None:
    """Write a ledger migrated through ``last`` holding the older release's last bar."""
    older = work / "migrations"
    older.mkdir()
    for name in _SHIPPED:
        if name <= last:
            shutil.copy(MIGRATIONS_DIR / name, older / name)
    event = BarProcessed.from_bar(bar, correlation_id="01OLDERRELEASE0000000000000")
    with closing(sqlite3.connect(db)) as conn:
        apply_migrations(conn, older)
        conn.execute(
            "INSERT INTO events (event_type, event_schema_version, correlation_id, occurred_at, "
            "payload_json, recorded_at) VALUES ('BarProcessed', ?, ?, ?, ?, ?)",
            (
                BarProcessed.__event_schema_version__,
                event.correlation_id,
                _iso_utc(event.occurred_at),
                _event_to_json(event),
                _iso_utc(bar.close_time),
            ),
        )
        if "live_bars" in _tables(conn):
            conn.execute(
                "INSERT INTO live_bars (pair_base, pair_quote, interval, open_time, close_time, "
                "open, high, low, close, volume, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bar.pair.base.value,
                    bar.pair.quote.value,
                    bar.interval.value,
                    _iso_utc(bar.open_time),
                    _iso_utc(bar.close_time),
                    str(bar.open),
                    str(bar.high),
                    str(bar.low),
                    str(bar.close),
                    str(bar.volume),
                    _iso_utc(bar.close_time),
                ),
            )
        conn.commit()


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _dump(db: Path) -> dict[str, list[tuple[object, ...]]]:
    """Return the applied migrations and the rows the older release wrote."""
    with closing(sqlite3.connect(db)) as conn:
        bars = (
            conn.execute(
                "SELECT pair_base, interval, open_time, close FROM live_bars "
                "WHERE recorded_at = close_time ORDER BY open_time"
            ).fetchall()
            if "live_bars" in _tables(conn)
            else []
        )
        return {
            "migrations": conn.execute(
                "SELECT filename FROM schema_migrations ORDER BY filename"
            ).fetchall(),
            "events": conn.execute(
                "SELECT event_type, occurred_at, payload_json FROM events "
                "WHERE correlation_id = '01OLDERRELEASE0000000000000'"
            ).fetchall(),
            "live_bars": bars,
        }


def _first_destructive_after(last: str) -> str | None:
    return next(
        (
            name
            for name in _SHIPPED
            if name > last
            and (MIGRATIONS_DIR / name).read_text(encoding="utf-8").startswith(DESTRUCTIVE_MARKER)
        ),
        None,
    )


@pytest.mark.parametrize("last", _OLDER)
def test_serve_upgrades_an_older_ledger_and_trades_as_on_a_fresh_one(
    run_scenario: RunScenario, tmp_path: Path, last: str
) -> None:
    """The old rows survive, every shipped migration is applied, then one entry and one exit."""
    db = tmp_path / "ledger.sqlite3"
    scenario = markets.chandelier_exit()
    _ledger_at(db, last, _last_bar_before_boot(scenario), tmp_path)
    before = _dump(db)
    destructive = _first_destructive_after(last)
    if destructive is not None:
        with pytest.raises(LedgerError, match="hermes migrate"):
            run_scenario(scenario)
        assert _dump(db) == {
            **before,
            "migrations": [(name,) for name in _SHIPPED if name < destructive],
        }
        migrate_with_snapshot(db, tmp_path / "snapshots")

    run = run_scenario(scenario)

    assert (run.exit_code, outline(db), _dump(db)) == (
        0,
        {
            "signals": [("BTC/USDT", "entry", ""), ("BTC/USDT", "exit", "chandelier")],
            "trades": [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
        },
        {**before, "migrations": [(name,) for name in _SHIPPED]},
    )
