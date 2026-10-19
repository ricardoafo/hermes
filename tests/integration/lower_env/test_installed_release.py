"""An installed release, run as its own processes, against the lower-environment fakes.

Invariants protected here (issue #972, part 2):

- ``hermes serve``, started the way systemd starts it but with ``HERMES_ENV=qa``
  and its endpoints on the fakes, sends the scripted breakout's entry to
  Telegram once, in a payload the Bot API accepts;
- a one-off Telegram outage is retried and the entry still arrives once; a
  long outage loses the message but neither the fill nor the run;
- ``hermes bot`` answers each read-only command (ADR-015) from that run's
  ledger, in the operator's chat only, each reply accepted by the Bot API.

Nothing here is wired by hand: the processes read the same config, env
variables and entry points as on a box. The test clock (``HERMES_TEST_CLOCK_URL``,
qa only) moves both onto the fake market's scripted time; the test steps it
from one bar close to the next only once serve has gone quiet, so serve never
sees a close late and never measures a skew that a jump in mid-request would
fake.
"""

import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.integration.scenarios import markets
from tests.support.fake_notify.server import Call, FakeNotify, Outage, Sink
from tests.support.installed_release import (
    BAR,
    CHAT,
    HERMES,
    SETTLE,
    Run,
    log_events,
    market_service,
    release_env,
    serve,
)
from tests.support.serve_loop import PROJECT_ROOT

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(240)]

_FOREIGN_CHAT = -1009999999999
_ENTRY_TEXT = "▲ Entry long BTC"
_COMMANDS = ("/status", "/next", "/signals", "/money", "/pos", "/logs")
_SCENARIO = markets.three_breakouts()


def _serve(tmp_path: Path, until: datetime, *, outage: tuple[Outage, int] | None = None) -> Run:
    return serve(tmp_path, _SCENARIO.market, _SCENARIO.live_from, until, outage=outage)


def _texts(calls: list[Call]) -> list[str]:
    return [str(c.body["text"]) for c in calls if c.path.endswith("/sendMessage")]


_BREAKOUT_HANDLED = _SCENARIO.live_from + timedelta(hours=28) + SETTLE
_AFTER_BREAKOUT = _BREAKOUT_HANDLED + BAR


@pytest.fixture(scope="module")
def clean_run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    """One run through the BTC breakout and one bar past it, with every sink up."""
    return _serve(tmp_path_factory.mktemp("clean"), _AFTER_BREAKOUT)


def test_the_entry_reaches_telegram_once_in_a_valid_payload(clean_run: Run) -> None:
    """The scripted breakout's entry is the one message, accepted by the Bot API contract."""
    sent = [c for c in clean_run.telegram if c.path.endswith("/sendMessage")]

    assert [(c.status, c.problems, c.body["chat_id"]) for c in sent] == [(200, (), CHAT)]
    assert _texts(sent)[0].startswith(_ENTRY_TEXT)


def test_the_run_handles_every_close_on_time_and_stops_cleanly(clean_run: Run) -> None:
    """No close is missed or late, no skew is faked, and SIGTERM ends the run with 0."""
    events = log_events(clean_run.log)

    assert clean_run.exit_code == 0, clean_run.log
    assert "serve.signal.missed_entry" not in events
    assert "serve.clock.skew_changed" not in events
    assert events.count("serve.fill.executed") == 1


def test_a_single_telegram_failure_is_retried_and_the_entry_arrives_once(
    tmp_path: Path,
) -> None:
    """One 5xx from Telegram is retried; the operator still gets the entry, once."""
    run = _serve(tmp_path, _AFTER_BREAKOUT, outage=(Outage.SERVER_ERROR, 1))

    statuses = [c.status for c in run.telegram if c.path.endswith("/sendMessage")]
    assert statuses == [502, 200]
    assert _texts(run.telegram)[1].startswith(_ENTRY_TEXT)
    assert "serve.notify.retry" in log_events(run.log)


def test_a_long_telegram_outage_loses_the_message_but_not_the_fill_or_the_run(
    tmp_path: Path,
) -> None:
    """With Telegram down past the retry, the fill is still recorded and serve keeps going."""
    run = _serve(tmp_path, _AFTER_BREAKOUT, outage=(Outage.SERVER_ERROR, 10))
    events = log_events(run.log)

    assert {c.status for c in run.telegram} == {502}
    assert "serve.notify.failed" in events
    assert events.count("serve.fill.executed") == 1
    assert events[events.index("serve.notify.failed") :].count("serve.bar.processed") > 0
    assert run.exit_code == 0, run.log


@pytest.fixture(scope="module")
def bot_replies(clean_run: Run) -> Iterator[list[Call]]:
    """Every command, plus one from a foreign chat, answered by ``hermes bot --once``."""
    with market_service(_SCENARIO.market, _SCENARIO.live_from) as market, FakeNotify() as notify:
        market.set_clock(clean_run.stopped_at)
        for command in _COMMANDS:
            notify.inject_command(int(CHAT), command)
        notify.inject_command(_FOREIGN_CHAT, "/status")
        result = subprocess.run(  # noqa: S603 - this checkout's own entry point
            [
                str(HERMES),
                "bot",
                "--once",
                "--db",
                str(clean_run.db),
                "--config-dir",
                str(PROJECT_ROOT / "config"),
            ],
            env=release_env(market, notify),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        yield [c for c in notify.calls(Sink.TELEGRAM) if c.path.endswith("/sendMessage")]


def test_the_bot_answers_each_command_once_in_the_operators_chat_only(
    bot_replies: list[Call],
) -> None:
    """One reply per command, in order, all to the operator, all accepted by the Bot API.

    The foreign chat's command gets nothing.
    """
    assert [(c.status, c.problems, c.body["chat_id"]) for c in bot_replies] == [
        (200, (), CHAT)
    ] * len(_COMMANDS)


def test_the_bot_reads_the_runs_ledger_on_the_test_clock(bot_replies: list[Call]) -> None:
    """/status, /next and /signals report the run at its scripted time, not at wall time."""
    status, upcoming, signals = _texts(bot_replies)[:3]

    assert status.startswith("All good. Watching BTC, ETH and SOL. Every check ran on time.")
    assert "Next check 13:00 (12:00 UTC)." in status
    assert "90-day test: day 2 of 90, running." in status
    assert "BTC/USDT 4h: 13:00 (12:00 UTC)" in upcoming.splitlines()
    assert signals == "1 signals in the last 7 days:\nTue 05:00 BTC entry long"


def test_the_bot_shows_the_runs_position_and_money_from_its_db(bot_replies: list[Call]) -> None:
    """/pos and /money read the --db ledger, and name the pending tax once."""
    money, positions, logs = _texts(bot_replies)[3:]

    assert money.startswith("Total €")
    assert money.count("tax estimate: not available yet") == 2
    assert "tax tax" not in money
    assert positions.startswith("1 open (simulated)\nBTC long · 4h · net ")
    assert logs == "No warnings or errors in the journal."
