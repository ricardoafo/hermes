"""Integration tests for the ``hermes serve`` shadow bar loop.

A scripted feed drives the real composition root over a real SQLite ledger
file. Channels are recording fakes, except in the shutdown test where real
adapters sit on ``httpx.MockTransport`` clients so closing can be observed.
No network is touched.

Invariants protected here (change ``309-serve-bar-loop``):

- one signal gives one ledger row and one call per channel;
- a restart never re-notifies, never skips a bar and never forgets an open
  position, whatever happened while the service was down;
- warm-up seeds history and never evaluates it;
- failures are loud (exit 1, Sentry) except a notifier outage, which never
  stops the loop; SIGTERM while idle closes every resource and exits 0.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.notifier.slack_adapter import SlackAdapter, SlackConfig
from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)
from hermes.application.notification_router import RouterConfig
from hermes.application.signal_notifier import SignalNotifier
from hermes.domain.errors import ExchangeError
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.market_data.kline import KlineInterval
from hermes.entrypoints import serve as serve_module
from tests.support.serve_loop import (
    BTC,
    FakeFeed,
    RecordingChannel,
    build,
    d1_bar,
    events,
    h4_bar,
    history,
    live,
    run_once,
    signal_kinds,
)

pytestmark = pytest.mark.integration

_TELEGRAM_TOKEN = "123:fake"  # noqa: S105  # gitleaks:allow
_SLACK_TOKEN = "xoxb-fake"  # noqa: S105  # gitleaks:allow


def _after(index: int) -> datetime:
    """Return one second after the close of scripted 4h bar ``index``."""
    return h4_bar(index).close_time + timedelta(seconds=1)


def _signals(db: Path) -> list[tuple[str, str]]:
    """Return ``(kind, occurred_at)`` of every SignalEmitted row, oldest first."""
    return [
        (json.loads(row["payload_json"])["kind"], row["occurred_at"])
        for row in events(db, "SignalEmitted")
    ]


def _cursor(db: Path, interval: KlineInterval) -> datetime | None:
    """Return the persisted cursor of BTC/USDT at ``interval``."""
    connection = sqlite3.connect(db)
    try:
        return SqliteLedger(connection).last_processed_close(BTC, interval)
    finally:
        connection.close()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[Exception]:
    """Replace Sentry's capture with a recorder and return its list."""
    recorded: list[Exception] = []
    monkeypatch.setattr(serve_module.sentry_integration, "capture", recorded.append)
    return recorded


def test_scripted_bars_emit_exactly_one_signal_and_one_notification_per_channel(
    tmp_path: Path,
) -> None:
    """One entry gives one ledger row and one call per channel; 4 cursor rows are written."""
    db = tmp_path / "ledger.sqlite3"
    feed = FakeFeed(warm=history(10), live_bars=live(11, 12))
    telegram, slack = RecordingChannel(), RecordingChannel()

    code = run_once(build(db, feed, _after(12), telegram=telegram, slack=slack), feed)

    assert (
        code,
        [kind for kind, _ in _signals(db)],
        signal_kinds(telegram),
        signal_kinds(slack),
        len(events(db, "BarProcessed")),
    ) == (0, ["entry"], ["entry"], ["entry"], 4)


def test_sigterm_while_idle_closes_every_resource_and_exits_zero(tmp_path: Path) -> None:
    """SIGTERM (the shutdown event) while the feed idles closes feed, both clients, SQLite."""
    feed = FakeFeed(warm=history(10))
    telegram_client = httpx.Client(
        base_url="https://api.telegram.org",
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"ok": True})),
    )
    slack_client = httpx.Client(
        base_url="https://slack.com",
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"ok": True, "ts": "1"})),
    )
    telegram = TelegramAdapter(
        config=TelegramConfig(bot_token=_TELEGRAM_TOKEN, chat_id="1"),
        client=telegram_client,
    )
    slack = SlackAdapter(
        config=SlackConfig(bot_token=_SLACK_TOKEN, default_channel="C1"),
        client=slack_client,
    )
    composition = dataclasses.replace(
        build(tmp_path / "ledger.sqlite3", feed, _after(10)),
        telegram=telegram,
        slack=slack,
        signal_notifier=SignalNotifier(RouterConfig(telegram=telegram, slack=slack)),
    )

    code = run_once(composition, feed)

    with pytest.raises(sqlite3.ProgrammingError):
        composition.sqlite_connection.execute("SELECT 1")
    assert (code, feed.closed, telegram_client.is_closed, slack_client.is_closed) == (
        0,
        True,
        True,
        True,
    )


def test_unrecoverable_feed_error_exits_one_after_closing_resources(
    tmp_path: Path, captured: list[Exception]
) -> None:
    """A feed error that survives its own retries exits 1, is sent to Sentry, resources close."""
    boom = ExchangeError("feed is dead")
    feed = FakeFeed(warm=history(10), error=boom)
    composition = build(tmp_path / "ledger.sqlite3", feed, _after(10))

    code = run_once(composition, feed)

    with pytest.raises(sqlite3.ProgrammingError):
        composition.sqlite_connection.execute("SELECT 1")
    assert (code, feed.closed, captured) == (1, True, [boom])


def test_notifier_outage_does_not_stop_the_loop(tmp_path: Path, captured: list[Exception]) -> None:
    """Telegram failing is logged and captured; Slack is still notified; the next bar is handled."""
    db = tmp_path / "ledger.sqlite3"
    feed = FakeFeed(warm=history(10), live_bars=live(11, 12))
    outage = TelegramNotifierError("telegram is down")
    telegram, slack = RecordingChannel(raises=outage), RecordingChannel()

    code = run_once(build(db, feed, _after(12), telegram=telegram, slack=slack), feed)

    assert (code, signal_kinds(slack), captured, _cursor(db, KlineInterval.H4)) == (
        0,
        ["entry"],
        [outage],
        h4_bar(12).close_time,
    )


def test_restart_over_same_history_emits_nothing_new(tmp_path: Path) -> None:
    """Restarting over history that already holds the entry adds no row and no message."""
    db = tmp_path / "ledger.sqlite3"
    first = FakeFeed(warm=history(10), live_bars=live(11, 12))
    run_once(build(db, first, _after(12)), first)
    second = FakeFeed(warm=history(12))
    telegram, slack = RecordingChannel(), RecordingChannel()

    code = run_once(build(db, second, _after(12), telegram=telegram, slack=slack), second)

    assert (code, [k for k, _ in _signals(db)], telegram.seen, slack.seen) == (
        0,
        ["entry"],
        [],
        [],
    )


def test_signal_missed_during_downtime_is_emitted_once_across_two_restarts(
    tmp_path: Path,
) -> None:
    """An entry that closed while down is notified once, not again on the next boot."""
    db = tmp_path / "ledger.sqlite3"
    boot1 = FakeFeed(warm=history(10))
    run_once(build(db, boot1, _after(10)), boot1)
    boot2 = FakeFeed(warm=history(12))
    boot3 = FakeFeed(warm=history(12))
    telegram2, telegram3 = RecordingChannel(), RecordingChannel()

    run_once(build(db, boot2, _after(12), telegram=telegram2), boot2)
    run_once(build(db, boot3, _after(12), telegram=telegram3), boot3)

    assert (
        [k for k, _ in _signals(db)],
        signal_kinds(telegram2),
        signal_kinds(telegram3),
    ) == (["entry"], ["entry"], [])


def test_restart_between_entry_and_exit_still_emits_exactly_one_exit(tmp_path: Path) -> None:
    """After a restart the open position is restored, so the crash bar emits one EXIT."""
    db = tmp_path / "ledger.sqlite3"
    boot1 = FakeFeed(warm=history(10), live_bars=live(11, 12))
    run_once(build(db, boot1, _after(12)), boot1)
    boot2 = FakeFeed(warm=history(12), live_bars=live(13, 14))
    slack = RecordingChannel()

    code = run_once(build(db, boot2, _after(14), slack=slack), boot2)

    exits = [json.loads(r["payload_json"]) for r in events(db, "SignalEmitted")][1:]
    assert (code, [k for k, _ in _signals(db)], signal_kinds(slack)) == (
        0,
        ["entry", "exit"],
        ["exit"],
    )
    assert [(e["kind"], e["exit_reason"], e["entry_price"]) for e in exits] == [
        ("exit", "chandelier", "2000")
    ]


def test_stale_signal_from_downtime_is_recorded_but_not_notified(tmp_path: Path) -> None:
    """An entry older than two bars is written to the ledger and not notified; the fresh exit is."""
    db = tmp_path / "ledger.sqlite3"
    boot1 = FakeFeed(warm=history(10))
    run_once(build(db, boot1, _after(10)), boot1)
    boot2 = FakeFeed(warm=history(14))
    telegram = RecordingChannel()

    run_once(build(db, boot2, _after(14), telegram=telegram), boot2)

    assert ([k for k, _ in _signals(db)], signal_kinds(telegram)) == (
        ["entry", "exit"],
        ["exit"],
    )


def test_bar_closed_while_down_is_evaluated_after_restart(tmp_path: Path) -> None:
    """Bars 11 and 12 closed while down: both are evaluated and the cursor lands on 12."""
    db = tmp_path / "ledger.sqlite3"
    boot1 = FakeFeed(warm=history(10))
    run_once(build(db, boot1, _after(10)), boot1)
    boot2 = FakeFeed(warm=history(12))

    run_once(build(db, boot2, _after(12)), boot2)

    processed = [
        e["occurred_at"] for e in events(db, "BarProcessed") if '"4h"' in e["payload_json"]
    ]
    assert (_signals(db)[0][1], processed[-3:], _cursor(db, KlineInterval.H4)) == (
        h4_bar(11).close_time.isoformat(),
        [
            h4_bar(10).close_time.isoformat(),
            h4_bar(11).close_time.isoformat(),
            h4_bar(12).close_time.isoformat(),
        ],
        h4_bar(12).close_time,
    )


def test_warmup_history_that_qualifies_for_entry_emits_no_signal_and_opens_no_position(
    tmp_path: Path,
) -> None:
    """Entry-qualifying history is only seeded: no signal, and a later crash exits nothing."""
    db = tmp_path / "ledger.sqlite3"
    feed = FakeFeed(warm=history(11), live_bars=[h4_bar(12, crash=True)])
    telegram = RecordingChannel()

    code = run_once(build(db, feed, _after(12), telegram=telegram), feed)

    assert (code, _signals(db), telegram.seen) == (0, [], [])


def test_first_boot_notifies_nothing_and_writes_the_cursor(tmp_path: Path) -> None:
    """A first boot seeds history, notifies nothing and writes one cursor row per stream."""
    db = tmp_path / "ledger.sqlite3"
    feed = FakeFeed(warm=history(10))
    telegram = RecordingChannel()

    code = run_once(build(db, feed, _after(10), telegram=telegram), feed)

    assert (
        code,
        telegram.seen,
        _cursor(db, KlineInterval.H4),
        _cursor(db, KlineInterval.D1),
    ) == (0, [], h4_bar(10).close_time, d1_bar(11).close_time)


def test_cursor_older_than_history_exits_one(tmp_path: Path, captured: list[Exception]) -> None:
    """A cursor that predates the warm-up window means bars were lost: BarGapError, exit 1."""
    db = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(db)
    SqliteLedger(connection).append_event(
        BarProcessed.from_bar(h4_bar(0), correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1")
    )
    connection.close()
    feed = FakeFeed(warm=history(10, h4_from=3))

    code = run_once(build(db, feed, _after(10)), feed)

    assert (code, [type(e) for e in captured]) == (1, [serve_module.BarGapError])


def test_bar_gap_exits_one(tmp_path: Path, captured: list[Exception]) -> None:
    """A live stream that skips a 4h bar raises BarGapError and exits 1."""
    db = tmp_path / "ledger.sqlite3"
    feed = FakeFeed(warm=history(10), live_bars=[h4_bar(11), h4_bar(13)])

    code = run_once(build(db, feed, _after(13)), feed)

    assert (code, [type(e) for e in captured], _cursor(db, KlineInterval.H4)) == (
        1,
        [serve_module.BarGapError],
        h4_bar(11).close_time,
    )


def test_duplicate_live_bar_is_evaluated_once(tmp_path: Path) -> None:
    """A bar delivered twice is skipped the second time: one cursor row, one signal."""
    db = tmp_path / "ledger.sqlite3"
    feed = FakeFeed(warm=history(10), live_bars=[h4_bar(11), h4_bar(11)])
    telegram = RecordingChannel()

    code = run_once(build(db, feed, _after(11), telegram=telegram), feed)

    assert (code, signal_kinds(telegram), len(events(db, "BarProcessed"))) == (0, ["entry"], 3)


def test_shutdown_set_before_start_returns_zero_without_touching_the_feed(
    tmp_path: Path,
) -> None:
    """A pre-set shutdown event exits 0 before any warm-up call."""
    feed = FakeFeed(warm=history(10))
    composition = build(tmp_path / "ledger.sqlite3", feed, _after(10))

    async def _go() -> int:
        shutdown = asyncio.Event()
        shutdown.set()
        return await asyncio.wait_for(serve_module.run(composition, shutdown), timeout=5)

    assert (asyncio.run(_go()), feed.closed) == (0, True)


def test_crash_between_signal_row_and_cursor_row_does_not_notify_twice(tmp_path: Path) -> None:
    """A bar re-evaluated after a crash finds its signal in the ledger and stays silent."""
    db = tmp_path / "ledger.sqlite3"
    boot1 = FakeFeed(warm=history(10), live_bars=live(11, 12))
    run_once(build(db, boot1, _after(12)), boot1)
    connection = sqlite3.connect(db)
    connection.execute(
        "DELETE FROM events WHERE event_type = 'BarProcessed' "
        "AND payload_json LIKE '%\"4h\"%' AND occurred_at >= ?",
        (h4_bar(11).close_time.isoformat(),),
    )
    connection.commit()
    connection.close()
    boot2 = FakeFeed(warm=history(12))
    telegram = RecordingChannel()

    code = run_once(build(db, boot2, _after(12), telegram=telegram), boot2)

    assert (code, [k for k, _ in _signals(db)], telegram.seen) == (0, ["entry"], [])
