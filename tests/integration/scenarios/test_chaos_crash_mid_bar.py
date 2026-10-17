"""Chaos: how Hermes fails when the process dies in the middle of one bar (issue #846).

The breakout bar of the chandelier scenario writes the most: the entry signal,
the BUY with its lots, the fill-quality row, the live bar, the portfolio mark,
the push record, the stop mark and the ``BarProcessed`` cursor, as eight
transactions. Each case
kills serve just before one of those writes or commits runs
(:class:`~tests.integration.scenarios.faults.ProcessDied`), then boots serve
again on the same ledger at the same instant, as systemd would.

By design (change ``309-serve-bar-loop`` D-5) a bar is not one transaction: a
crash leaves the writes already committed, and the restart re-evaluates the bar
because its cursor row is missing. What must hold:

- on disk after the crash: only whole transactions, never part of one;
- after the restart: the trades, and the whole ledger, as if no crash happened;
- the entry push reaches Telegram once, except after a crash between the push
  and its ``SignalNotified`` record, which pushes it once more (issue #854:
  at least once beats at most once for a trade).

Faults are injected at the SQLite connection, below the ledger; nothing in
serve is replaced (``tests/integration/scenarios/faults.py``).
"""

from __future__ import annotations

import contextlib
import functools
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path

import pytest
from _pytest.mark.structures import ParameterSet
from structlog.testing import capture_logs

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from tests.integration.scenarios import markets
from tests.integration.scenarios.faults import (
    COMMIT,
    FaultPlan,
    ProcessDied,
    armed_on,
    entries_sent,
    faulty_ledger,
    ledger_rows,
    recording_channels,
)
from tests.integration.scenarios.ledger import trades
from tests.support.fake_market import harness

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

type Rows = dict[str, list[tuple[str, ...]]]
type TradeKey = tuple[str, ...]

BREAKOUT_BAR_WRITES = (
    # 0-1: the SignalEmitted row.
    "INSERT events",
    COMMIT,
    # 2-10: the BUY, its lot, the FIFO projection of both legs, TradeExecuted.
    "INSERT trades",
    "INSERT lots",
    "UPDATE lots",
    "DELETE lot_consumptions",
    "UPDATE lots",
    "DELETE lot_consumptions",
    "INSERT lot_consumptions",
    "INSERT events",
    COMMIT,
    # 11-12: FillQualityRecorded. The Telegram push is sent after this commit.
    "INSERT events",
    COMMIT,
    # 13-14: SignalNotified, the record of that push (issue #854).
    "INSERT events",
    COMMIT,
    # 15-20: the live bar, the portfolio mark, the stop mark.
    "INSERT live_bars",
    COMMIT,
    "INSERT portfolio_marks",
    COMMIT,
    "INSERT stop_marks",
    COMMIT,
    # 21-22: the BarProcessed cursor.
    "INSERT events",
    COMMIT,
)
"""Every write statement and commit serve makes while handling the breakout bar, in order."""

_PUSH_SENT_NOT_RECORDED = range(13, 15)
_TRADE_SAVED_FILL_QUALITY_NOT = range(11, 13)

_EUR_COLUMNS = frozenset(
    {
        "eur_rate",
        "eur_rate_date",
        "eur_source",
        "eur_status",
        "price_eur",
        "fee_eur",
        "value_eur",
        "cost_basis_per_unit",
        "realized_gain_eur",
        "proceeds_eur",
        "cost_eur",
    }
)
"""EUR values a restart prices at boot, which an uninterrupted run leaves pending."""


@functools.cache
def _scenario() -> markets.Scenario:
    return markets.chandelier_exit()


def _is_breakout(bar: Bar) -> bool:
    return (
        bar.pair == markets.BTC
        and bar.interval is KlineInterval.H4
        and bar.close_time == _scenario().breakout_at
    )


def _trade_keys(db: Path) -> list[TradeKey]:
    return [
        (t.pair, t.side, str(t.qty), str(t.price), str(t.fee), t.at.isoformat()) for t in trades(db)
    ]


@dataclass(frozen=True)
class _Reference:
    """The uninterrupted run."""

    writes: list[str]
    after_each_commit: list[Rows]
    final: Rows
    trades: list[TradeKey]


@dataclass(frozen=True)
class _CrashAndRestart:
    """A run killed at one write, then a restart on the same ledger."""

    at_crash: Rows
    final: Rows
    trades: list[TradeKey]
    entries_sent: int


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> _Reference:
    """Run the scenario once without a fault, snapshotting the ledger after each commit."""
    db = tmp_path_factory.mktemp("reference") / "ledger.sqlite3"
    snapshots: list[Rows] = []

    def snapshot() -> None:
        snapshots.append(ledger_rows(db))

    plan = FaultPlan(after_commit=snapshot)
    scenario = _scenario()
    with capture_logs(), faulty_ledger(plan), armed_on(_is_breakout, on_start=snapshot):
        run = harness.serve(scenario.market, db, live_from=scenario.live_from)
    assert run.exit_code == 0
    return _Reference(
        writes=plan.points,
        after_each_commit=snapshots,
        final=ledger_rows(db, ignore=_EUR_COLUMNS),
        trades=_trade_keys(db),
    )


@pytest.fixture(scope="module")
def crash_and_restart(
    tmp_path_factory: pytest.TempPathFactory,
) -> Callable[[int], _CrashAndRestart]:
    """Return the outcome of killing serve before write ``index``; each index runs once."""
    done: dict[int, _CrashAndRestart] = {}

    def outcome(index: int) -> _CrashAndRestart:
        if index not in done:
            done[index] = _crash_then_restart(index, tmp_path_factory.mktemp(f"crash{index}"))
        return done[index]

    return outcome


def _crash_then_restart(index: int, work: Path) -> _CrashAndRestart:
    db = work / "ledger.sqlite3"
    scenario = _scenario()
    plan = FaultPlan(fail_at=index)
    with capture_logs(), recording_channels() as channels:
        with (
            faulty_ledger(plan),
            armed_on(_is_breakout) as started,
            contextlib.suppress(ProcessDied),
        ):
            harness.serve(scenario.market, db, live_from=scenario.live_from)
        assert isinstance(plan.fired, ProcessDied), f"no write {index} on the breakout bar"
        at_crash = ledger_rows(db)
        restart = harness.serve(scenario.market, db, live_from=started[0])
    assert restart.exit_code == 0
    return _CrashAndRestart(
        at_crash=at_crash,
        final=ledger_rows(db, ignore=_EUR_COLUMNS),
        trades=_trade_keys(db),
        entries_sent=entries_sent(channels),
    )


def _each_write(*, xfail: Collection[int] = (), reason: str = "") -> list[ParameterSet]:
    return [
        pytest.param(
            index,
            id=f"{index:02d}-{write.replace(' ', '-')}",
            marks=(
                [pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)]
                if index in xfail
                else []
            ),
        )
        for index, write in enumerate(BREAKOUT_BAR_WRITES)
    ]


def test_the_crash_cases_cover_every_write_of_the_breakout_bar(reference: _Reference) -> None:
    """A write added to the bar path without a crash case here fails this test."""
    assert reference.writes == list(BREAKOUT_BAR_WRITES)


@pytest.mark.parametrize("index", _each_write())
def test_a_crash_mid_bar_leaves_only_whole_transactions_on_disk(
    reference: _Reference, crash_and_restart: Callable[[int], _CrashAndRestart], index: int
) -> None:
    """Killed before write N, the ledger is exactly what the commits before N left: no part-rows."""
    commits_before = BREAKOUT_BAR_WRITES[:index].count(COMMIT)
    assert crash_and_restart(index).at_crash == reference.after_each_commit[commits_before]


@pytest.mark.parametrize("index", _each_write())
def test_a_restart_after_a_crash_mid_bar_records_each_trade_once(
    reference: _Reference, crash_and_restart: Callable[[int], _CrashAndRestart], index: int
) -> None:
    """The restart neither buys twice nor loses the BUY or the SELL: same trades, same fills."""
    assert crash_and_restart(index).trades == reference.trades


@pytest.mark.parametrize(
    "index",
    _each_write(
        xfail=_TRADE_SAVED_FILL_QUALITY_NOT,
        reason=(
            "issue #857: a crash after the trade commit and before the FillQualityRecorded "
            "commit loses the fill-quality row for good; the restart sees the trade, "
            "returns AlreadyFilled and records no quality row"
        ),
    ),
)
def test_a_restart_after_a_crash_mid_bar_leaves_the_ledger_an_uninterrupted_run_leaves(
    reference: _Reference, crash_and_restart: Callable[[int], _CrashAndRestart], index: int
) -> None:
    """Every row of every table matches the uninterrupted run once the restart has caught up."""
    assert crash_and_restart(index).final == reference.final


@pytest.mark.parametrize(
    "index", [p for p in _each_write() if p.values[0] not in _PUSH_SENT_NOT_RECORDED]
)
def test_a_crash_mid_bar_does_not_lose_the_entry_push(
    crash_and_restart: Callable[[int], _CrashAndRestart], index: int
) -> None:
    """The entry reaches Telegram exactly once across the killed run and the restart.

    Killed after the signal row and before the push (issue #854), the restart
    finds no push record and pushes it; killed after the record, it pushes nothing.
    """
    assert crash_and_restart(index).entries_sent == 1


@pytest.mark.parametrize(
    "index", [p for p in _each_write() if p.values[0] in _PUSH_SENT_NOT_RECORDED]
)
def test_a_crash_between_the_push_and_its_record_pushes_the_entry_once_more(
    crash_and_restart: Callable[[int], _CrashAndRestart], index: int
) -> None:
    """Killed after the push and before its record, the restart pushes it again, once.

    The documented at-least-once window (issue #854): the push left the
    process, but the ledger cannot know, so the restart sends it a second time.
    """
    assert crash_and_restart(index).entries_sent == 2
