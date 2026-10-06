"""Chaos: how Hermes fails when its ledger file misbehaves (issue #846).

Disk full, a lock held by another process, a second serve on the same ledger,
and a ledger a newer release has migrated. Every failure must stop serve
loudly (exit 1 and a ``serve.run.failed`` line naming the cause) with no part
of a transaction committed, or be waited out with nothing lost. Faults are
raised at the SQLite connection serve opens
(``tests/integration/scenarios/faults.py``); serve itself is not changed.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import io
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from hermes.adapters.ledger.migration_runner import apply_migrations
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints.commands.serve import EXIT_LEDGER_IN_USE, serve_main
from tests.integration.scenarios import markets
from tests.integration.scenarios.conftest import RunScenario
from tests.integration.scenarios.faults import (
    FaultPlan,
    armed,
    armed_in_boot_pricing,
    armed_on,
    faulty_ledger,
    ledger_rows,
)
from tests.integration.scenarios.ledger import outline
from tests.support.fake_market import harness
from tests.support.fake_market.exchange import FakeExchange, SimClock
from tests.support.serve_loop import RecordingChannel, events

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

_DISK_FULL = "database or disk is full"
_TRADE_EVENT_WRITE = 9
"""The ``INSERT events`` of TradeExecuted, inside the BUY's transaction on the breakout bar."""

_LOCK_HELD_SECONDS = 0.3
"""How long another process holds the write lock: well under serve's 10 s busy wait."""


def _is_breakout(scenario: markets.Scenario) -> Callable[[Bar], bool]:
    def chosen(bar: Bar) -> bool:
        return (
            bar.pair == markets.BTC
            and bar.interval is KlineInterval.H4
            and bar.close_time == scenario.breakout_at
        )

    return chosen


# Any: a structlog event dict holds whatever each log call passed.
def _failure(run_logs: list[dict[str, Any]]) -> list[str]:
    return [
        f"{type(line['exc_info']).__name__}: {line['exc_info']}"
        for line in run_logs
        if line.get("event") == "serve.run.failed"
    ]


def _migrated_ledger(db: Path) -> None:
    with closing(sqlite3.connect(db)) as conn:
        apply_migrations(conn)


def _boot_through_serve_main(db: Path) -> tuple[int, list[dict[str, Any]]]:
    """Run ``hermes serve``'s boot; the bar loop must never start, so no network is reached."""

    async def loop_must_not_start(*_args: object) -> int:
        raise AssertionError("serve booted past build_composition")

    with capture_logs() as logs, pytest.MonkeyPatch.context() as patch:
        patch.setattr(serve_module, "run", loop_must_not_start)
        code = asyncio.run(serve_main(harness.production_config(), db_path=db))
    return code, [dict(line) for line in logs]


# -- disk full ---------------------------------------------------------------


@pytest.mark.parametrize("ledger", ["fresh", "migrated"])
def test_a_full_disk_at_boot_stops_serve_with_exit_1_and_the_cause_logged(
    tmp_path: Path, ledger: str
) -> None:
    """The first write at boot fails for want of space: exit 1, one failure line naming it.

    On a fresh ledger that write is the first migration's bookkeeping row; on a
    migrated one it is the capital seed.
    """
    db = tmp_path / "ledger.sqlite3"
    if ledger == "migrated":
        _migrated_ledger(db)
    plan = FaultPlan(fail_on="INSERT", error=lambda: sqlite3.OperationalError(_DISK_FULL))
    with faulty_ledger(plan), armed():
        code, logs = _boot_through_serve_main(db)
    expected = {
        "fresh": f"OperationalError: {_DISK_FULL}",
        "migrated": (
            "StartupError: cannot record the start capital in the ledger: "
            f"record_capital_seed failed: {_DISK_FULL}"
        ),
    }
    assert (code, _failure(logs)) == (1, [expected[ledger]])


@pytest.mark.parametrize("ledger", ["fresh", "migrated"])
def test_a_full_disk_at_boot_commits_no_row(tmp_path: Path, ledger: str) -> None:
    """Neither a migration nor the capital seed is left half-written on a full disk."""
    db = tmp_path / "ledger.sqlite3"
    if ledger == "migrated":
        _migrated_ledger(db)
    before = ledger_rows(db)
    plan = FaultPlan(fail_on="INSERT", error=lambda: sqlite3.OperationalError(_DISK_FULL))
    with faulty_ledger(plan), armed():
        _boot_through_serve_main(db)
    assert ledger_rows(db) == before


_MID_BAR_ERRORS: dict[str, Callable[[], Exception]] = {
    "sqlite-disk-full": lambda: sqlite3.OperationalError(_DISK_FULL),
    "os-enospc": lambda: OSError(errno.ENOSPC, "No space left on device"),
    "lock-wait-expired": lambda: sqlite3.OperationalError("database is locked"),
}


@dataclass(frozen=True)
class _MidBarFailure:
    """A run whose write failed inside the BUY's transaction, then a restart."""

    exit_code: int
    failures: list[str]
    left: dict[str, list[tuple[str, ...]]]
    lot_assets: list[str]
    restart_exit_code: int
    restart_trades: list[tuple[str, ...]]


@pytest.fixture(scope="module")
def failed_mid_trade(
    tmp_path_factory: pytest.TempPathFactory,
) -> Callable[[str], _MidBarFailure]:
    """Return the outcome of one storage error inside the BUY's transaction; each runs once."""
    done: dict[str, _MidBarFailure] = {}

    def outcome(error: str) -> _MidBarFailure:
        if error not in done:
            done[error] = _fail_mid_trade(_MID_BAR_ERRORS[error], tmp_path_factory.mktemp(error))
        return done[error]

    return outcome


def _fail_mid_trade(error: Callable[[], Exception], work: Path) -> _MidBarFailure:
    scenario = markets.chandelier_exit()
    db = work / "ledger.sqlite3"
    plan = FaultPlan(fail_at=_TRADE_EVENT_WRITE, error=error)
    with capture_logs() as logs, faulty_ledger(plan), armed_on(_is_breakout(scenario)) as started:
        run = harness.serve(scenario.market, db, live_from=scenario.live_from)
    assert plan.fired is not None, "the fault never fired"
    left = outline(db)
    lot_assets = [lot[0] for lot in ledger_rows(db)["lots"]]
    with capture_logs():
        restart = harness.serve(scenario.market, db, live_from=started[0])
    return _MidBarFailure(
        exit_code=run.exit_code,
        failures=_failure([dict(line) for line in logs]),
        left=left,
        lot_assets=lot_assets,
        restart_exit_code=restart.exit_code,
        restart_trades=outline(db)["trades"],
    )


@pytest.mark.parametrize("error", sorted(_MID_BAR_ERRORS))
def test_a_storage_error_mid_bar_stops_serve_with_exit_1_and_the_cause_logged(
    failed_mid_trade: Callable[[str], _MidBarFailure], error: str
) -> None:
    """A write failing inside the BUY's transaction ends the run: exit 1, the error on record."""
    cause = _MID_BAR_ERRORS[error]()
    wrapped = (
        f"LedgerError: Ledger error: record_trade failed: {cause}"
        if isinstance(cause, sqlite3.Error)
        else f"OSError: {cause}"
    )
    outcome = failed_mid_trade(error)
    assert (outcome.exit_code, outcome.failures) == (1, [wrapped])


@pytest.mark.parametrize("error", sorted(_MID_BAR_ERRORS))
def test_a_storage_error_mid_bar_commits_no_part_of_the_trade(
    failed_mid_trade: Callable[[str], _MidBarFailure], error: str
) -> None:
    """The entry signal committed before the failure stays; the BUY and its lot do not exist."""
    outcome = failed_mid_trade(error)
    assert (outcome.left, outcome.lot_assets) == (
        {"signals": [("BTC/USDT", "entry", "")], "trades": []},
        ["USDT"],
    )


@pytest.mark.parametrize("error", sorted(_MID_BAR_ERRORS))
def test_a_restart_after_a_storage_error_mid_bar_trades_once(
    failed_mid_trade: Callable[[str], _MidBarFailure], error: str
) -> None:
    """Once the disk has room again, the restart buys once and sells once."""
    outcome = failed_mid_trade(error)
    assert (outcome.restart_exit_code, outcome.restart_trades) == (
        0,
        [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "issue #853: SqliteLedger rolls back only on sqlite3.Error, so a non-SQLite error "
        "mid-write leaves rows the next write commits"
    ),
)
def test_a_non_sqlite_error_mid_write_leaves_nothing_for_the_next_commit(
    run_scenario: RunScenario,
) -> None:
    """Boot EUR pricing fails between its two writes; serve goes on, the seed stays unpriced.

    Serve logs the pricing failure and keeps running (``serve.eur_pricing_failed``),
    so the next bar's commit must not carry the half-done pricing with it.
    """
    plan = FaultPlan(fail_at=1, error=lambda: MemoryError("between the seed and its lot"))
    with faulty_ledger(plan), armed_in_boot_pricing():
        run = run_scenario(markets.chandelier_exit())
    with closing(sqlite3.connect(run.db)) as conn:
        seed = conn.execute("SELECT eur_status, value_eur FROM capital_seeds").fetchall()
    assert (plan.points, seed) == (["UPDATE capital_seeds", "UPDATE lots"], [("pending", None)])


# -- database locked ---------------------------------------------------------


def test_serve_waits_out_a_write_lock_held_by_another_process(
    run_scenario: RunScenario, tmp_path: Path
) -> None:
    """Another writer holds the lock as the breakout bar is handled: serve waits, loses nothing.

    The other process (``BEGIN IMMEDIATE``) lets go after 0.3 s; serve's
    signal write must wait for it rather than fail or be dropped.
    """
    scenario = markets.chandelier_exit()
    db = tmp_path / "ledger.sqlite3"
    signals_while_locked: list[int] = []
    timers: list[threading.Timer] = []

    def hold_the_write_lock() -> None:
        other = sqlite3.connect(db, check_same_thread=False, isolation_level=None)
        other.execute("BEGIN IMMEDIATE")

        def release() -> None:
            count = other.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'SignalEmitted'"
            ).fetchone()[0]
            signals_while_locked.append(count)
            other.execute("ROLLBACK")
            other.close()

        timer = threading.Timer(_LOCK_HELD_SECONDS, release)
        timers.append(timer)
        timer.start()

    with armed_on(_is_breakout(scenario), on_start=hold_the_write_lock):
        run = run_scenario(scenario)
    for timer in timers:
        timer.join()
    assert signals_while_locked == [0], "serve wrote while the lock was held: no contention"
    assert (run.exit_code, outline(run.db)) == (
        0,
        {
            "signals": [("BTC/USDT", "entry", ""), ("BTC/USDT", "exit", "chandelier")],
            "trades": [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
        },
    )


# -- double start, newer ledger ----------------------------------------------


def _fake_exchange(scenario: markets.Scenario, clock: SimClock) -> FakeExchange:
    fx_url = httpx.URL(harness.production_config().frankfurter.base_url)
    return FakeExchange(
        market=scenario.market,
        clock=clock,
        fx_host=fx_url.host,
        fx_prefix=fx_url.path.rstrip("/"),
    )


async def _serve_until_idle(db: Path, scenario: markets.Scenario, clock: SimClock) -> int:
    """Boot one serve on ``db`` and run it until its clock reaches the end; return its exit code."""
    composition = harness.compose(
        harness.production_config(), db, _fake_exchange(scenario, clock), RecordingChannel()
    )
    shutdown = asyncio.Event()
    task = asyncio.create_task(serve_module.run(composition, shutdown))
    idle = asyncio.create_task(clock.finished.wait())
    await asyncio.wait({task, idle}, return_when=asyncio.FIRST_COMPLETED)
    shutdown.set()
    idle.cancel()
    return await task


def test_a_second_serve_on_a_ledger_in_use_refuses_to_run(tmp_path: Path) -> None:
    """While one serve runs, a second on the same ledger handles no bar.

    Two writers on one ledger each think they own the cursor, the open position
    and the cash: every bar after the second boot would be recorded twice.
    """
    scenario = markets.chandelier_exit()
    db = tmp_path / "ledger.sqlite3"

    async def two_serves() -> int:
        first_clock = SimClock(scenario.live_from, scenario.breakout_at)
        first_exchange = _fake_exchange(scenario, first_clock)
        first = harness.compose(harness.production_config(), db, first_exchange, RecordingChannel())
        stop_first = asyncio.Event()
        running = asyncio.create_task(serve_module.run(first, stop_first))
        await first_clock.finished.wait()
        before = len(events(db, "BarProcessed"))
        try:
            with capture_logs():
                await _serve_until_idle(
                    db, scenario, SimClock(scenario.live_from, scenario.market.end)
                )
        except LedgerError, serve_module.StartupError:
            pass
        finally:
            stop_first.set()
            await running
        return len(events(db, "BarProcessed")) - before

    with capture_logs():
        handled_by_second = asyncio.run(two_serves())
    assert handled_by_second == 0


def test_hermes_serve_on_a_ledger_in_use_exits_with_the_refusal_code(tmp_path: Path) -> None:
    """``hermes serve`` on a ledger another serve holds exits 4 and names the ledger.

    A distinct code lets the operator (and the journal) tell "already running"
    apart from a crash (1) or a bad config (3).
    """
    scenario = markets.chandelier_exit()
    db = tmp_path / "ledger.sqlite3"
    clock = SimClock(scenario.live_from, scenario.breakout_at)
    first = harness.compose(
        harness.production_config(), db, _fake_exchange(scenario, clock), RecordingChannel()
    )
    err = io.StringIO()
    try:
        code = asyncio.run(serve_main(harness.production_config(), db_path=db, err=err))
    finally:
        asyncio.run(serve_module.run(first, _already_set()))
    assert (code, f"another hermes serve is running on {db}" in err.getvalue()) == (
        EXIT_LEDGER_IN_USE,
        True,
    )


def test_the_serve_lock_is_released_when_the_first_serve_exits(tmp_path: Path) -> None:
    """Once the first serve has stopped, the next serve on the same ledger boots."""
    scenario = markets.chandelier_exit()
    db = tmp_path / "ledger.sqlite3"

    def boot() -> serve_module.Composition:
        clock = SimClock(scenario.live_from, scenario.breakout_at)
        return harness.compose(
            harness.production_config(), db, _fake_exchange(scenario, clock), RecordingChannel()
        )

    with capture_logs():
        asyncio.run(serve_module.run(boot(), _already_set()))
        second = boot()
        asyncio.run(serve_module.run(second, _already_set()))
    assert second.serve_lock is not None
    assert second.serve_lock.held is False


def _already_set() -> asyncio.Event:
    event = asyncio.Event()
    event.set()
    return event


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="issue #856: older code on a newer ledger boots and writes without a word",
)
def test_serve_refuses_a_ledger_a_newer_release_has_migrated(
    run_scenario: RunScenario, tmp_path: Path
) -> None:
    """A half-installed release (old code, new ledger) writes nothing to a schema it cannot know."""
    db = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(db)) as conn:
        apply_migrations(conn)
        conn.execute(
            "INSERT INTO schema_migrations (filename) VALUES ('9999_from_a_newer_release.sql')"
        )
        conn.commit()
    with contextlib.suppress(LedgerError, serve_module.StartupError):
        run_scenario(markets.chandelier_exit())
    assert events(db, "BarProcessed") == []
