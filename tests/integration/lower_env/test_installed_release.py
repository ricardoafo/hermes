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

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from tests.integration.scenarios import markets
from tests.support.fake_market.exchange import FakeExchange, SimClock
from tests.support.fake_market.harness import production_config
from tests.support.fake_market.service import CONTROL_PREFIX, FakeMarketService
from tests.support.fake_notify.server import Call, FakeNotify, Outage, Sink
from tests.support.serve_loop import PROJECT_ROOT

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(240)]

_HERMES = Path(sys.executable).parent / "hermes"
_TOKEN = "123456:installed-release-fake"  # noqa: S105 - test-only  # gitleaks:allow
_CHAT = "-1001234567890"
_FOREIGN_CHAT = -1009999999999
_BAR = timedelta(hours=4)
_SETTLE = timedelta(seconds=production_config().market_data.settle_delay_seconds)
_QUIET_S = 0.3
_BOOT_TIMEOUT_S = 60.0
_STEP_TIMEOUT_S = 30.0
_ENTRY_TEXT = "▲ Entry long BTC"
_COMMANDS = ("/status", "/next", "/signals", "/money", "/pos", "/logs")


@dataclass(frozen=True)
class Run:
    """What one installed ``hermes serve`` run left behind.

    Attributes:
        db: Its ledger.
        log: Its stdout and stderr.
        exit_code: How it exited after SIGTERM.
        telegram: Every Telegram request it made, oldest first.
        stopped_at: The scripted instant the run was stopped at.

    """

    db: Path
    log: str
    exit_code: int
    telegram: list[Call]
    stopped_at: datetime


def _base_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_")}
    env["HERMES_ENV"] = "qa"
    return env


def _release_env(market: FakeMarketService, notify: FakeNotify) -> dict[str, str]:
    """Point every endpoint the release reads at a fake, as a qa env file would."""
    return _base_env() | {
        "HERMES_MARKET_DATA__BASE_URL": market.binance_base,
        "HERMES_FRANKFURTER__BASE_URL": market.frankfurter_base,
        "HERMES_TELEGRAM__BOT_TOKEN": _TOKEN,
        "HERMES_TELEGRAM__CHAT_ID": _CHAT,
        "HERMES_TELEGRAM__API_BASE": notify.telegram_base,
        "HERMES_TEST_CLOCK_URL": f"{market.url}{CONTROL_PREFIX}/clock",
    }


def _market_service() -> FakeMarketService:
    scenario = markets.three_breakouts()
    fx = httpx.URL(production_config().frankfurter.base_url)
    exchange = FakeExchange(
        market=scenario.market,
        clock=SimClock(scenario.live_from, scenario.market.end),
        fx_host=fx.host,
        fx_prefix=fx.path.rstrip("/"),
    )
    return FakeMarketService(exchange)


def _wait_quiet(market: FakeMarketService, serve: subprocess.Popen[str]) -> None:
    """Return once serve has made no market request for ``_QUIET_S``: it is waiting on the clock."""
    deadline = time.monotonic() + _STEP_TIMEOUT_S
    seen = len(market.exchange.requests)
    while time.monotonic() < deadline and serve.poll() is None:
        time.sleep(_QUIET_S)
        now = len(market.exchange.requests)
        if now == seen:
            return
        seen = now
    pytest.fail("serve neither went quiet nor kept running")


def _closes_due(due: datetime) -> set[tuple[str, str, str]]:
    """Return the bars serve handles when the clock reaches ``due``, as its log names them."""
    close = (due - _SETTLE - timedelta(milliseconds=1)).isoformat()
    intervals = ["4h", "1d"] if due.hour == 0 else ["4h"]
    pairs = production_config().market_data.pairs
    return {(pair, interval, close) for pair in pairs for interval in intervals}


def _wait_handled(log_path: Path, due: datetime, serve: subprocess.Popen[str]) -> None:
    """Return once serve has logged every bar that closed at ``due``.

    Quiet alone could be a slow runner between two requests; the log says the
    bars are done, so the next step never lands in the middle of one.
    """
    expected = _closes_due(due)
    deadline = time.monotonic() + _STEP_TIMEOUT_S
    while time.monotonic() < deadline and serve.poll() is None:
        processed = {
            (row["pair"], row["interval"], row["close_time"])
            for row in _rows(log_path.read_text(encoding="utf-8"))
            if row.get("event") == "serve.bar.processed"
        }
        if expected <= processed:
            return
        time.sleep(0.05)
    pytest.fail(f"serve did not handle the {due.isoformat()} close")


def _next_due(now: datetime) -> datetime:
    """Return the next 4h close plus the settle delay, the instant serve wakes for."""
    boundary = now.replace(hour=now.hour - now.hour % 4, minute=0, second=0, microsecond=0)
    due = boundary + _SETTLE
    while due <= now:
        due += _BAR
    return due


def _serve(tmp_path: Path, until: datetime, *, outage: tuple[Outage, int] | None = None) -> Run:
    """Run an installed ``hermes serve`` on a fresh ledger, stepping the clock to ``until``."""
    db = tmp_path / "hermes.sqlite3"
    log_path = tmp_path / "serve.log"
    with _market_service() as market, FakeNotify(timeout_hold_s=0.5) as notify:
        if outage is not None:
            notify.fail_next(Sink.TELEGRAM, *outage)
        with log_path.open("w", encoding="utf-8") as log:
            serve = subprocess.Popen(  # noqa: S603 - this checkout's own entry point
                [
                    str(_HERMES),
                    "serve",
                    "--config-dir",
                    str(PROJECT_ROOT / "config"),
                    "--db",
                    str(db),
                    "--new-ledger",
                ],
                env=_release_env(market, notify),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                _wait_started(log_path, serve)
                _wait_quiet(market, serve)
                while market.now() < until and serve.poll() is None:
                    due = min(_next_due(market.now()), until)
                    market.set_clock(due)
                    _wait_handled(log_path, due, serve)
                    _wait_quiet(market, serve)
            finally:
                serve.send_signal(signal.SIGTERM)
                code = serve.wait(timeout=30)
        return Run(
            db=db,
            log=log_path.read_text(encoding="utf-8"),
            exit_code=code,
            telegram=notify.calls(Sink.TELEGRAM),
            stopped_at=market.now(),
        )


def _wait_started(log_path: Path, serve: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + _BOOT_TIMEOUT_S
    while time.monotonic() < deadline and serve.poll() is None:
        if "serve.run.started" in log_path.read_text(encoding="utf-8"):
            return
        time.sleep(0.1)
    pytest.fail(f"serve did not start:\n{log_path.read_text(encoding='utf-8')}")


def _texts(calls: list[Call]) -> list[str]:
    return [str(c.body["text"]) for c in calls if c.path.endswith("/sendMessage")]


def _rows(log: str) -> list[dict[str, str]]:
    return [json.loads(line) for line in log.splitlines() if line.startswith("{")]


def _events(log: str) -> list[str]:
    return [row["event"] for row in _rows(log)]


_BREAKOUT_HANDLED = markets.three_breakouts().live_from + timedelta(hours=28) + _SETTLE
_AFTER_BREAKOUT = _BREAKOUT_HANDLED + _BAR


@pytest.fixture(scope="module")
def clean_run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    """One run through the BTC breakout and one bar past it, with every sink up."""
    return _serve(tmp_path_factory.mktemp("clean"), _AFTER_BREAKOUT)


def test_the_entry_reaches_telegram_once_in_a_valid_payload(clean_run: Run) -> None:
    """The scripted breakout's entry is the one message, accepted by the Bot API contract."""
    sent = [c for c in clean_run.telegram if c.path.endswith("/sendMessage")]

    assert [(c.status, c.problems, c.body["chat_id"]) for c in sent] == [(200, (), _CHAT)]
    assert _texts(sent)[0].startswith(_ENTRY_TEXT)


def test_the_run_handles_every_close_on_time_and_stops_cleanly(clean_run: Run) -> None:
    """No close is missed or late, no skew is faked, and SIGTERM ends the run with 0."""
    events = _events(clean_run.log)

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
    assert "serve.notify.retry" in _events(run.log)


def test_a_long_telegram_outage_loses_the_message_but_not_the_fill_or_the_run(
    tmp_path: Path,
) -> None:
    """With Telegram down past the retry, the fill is still recorded and serve keeps going."""
    run = _serve(tmp_path, _AFTER_BREAKOUT, outage=(Outage.SERVER_ERROR, 10))
    events = _events(run.log)

    assert {c.status for c in run.telegram} == {502}
    assert "serve.notify.failed" in events
    assert events.count("serve.fill.executed") == 1
    assert events[events.index("serve.notify.failed") :].count("serve.bar.processed") > 0
    assert run.exit_code == 0, run.log


@pytest.fixture(scope="module")
def bot_replies(clean_run: Run) -> Iterator[list[Call]]:
    """Every command, plus one from a foreign chat, answered by ``hermes bot --once``."""
    with _market_service() as market, FakeNotify() as notify:
        market.set_clock(clean_run.stopped_at)
        for command in _COMMANDS:
            notify.inject_command(int(_CHAT), command)
        notify.inject_command(_FOREIGN_CHAT, "/status")
        result = subprocess.run(  # noqa: S603 - this checkout's own entry point
            [
                str(_HERMES),
                "bot",
                "--once",
                "--db",
                str(clean_run.db),
                "--config-dir",
                str(PROJECT_ROOT / "config"),
            ],
            env=_release_env(market, notify),
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
        (200, (), _CHAT)
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
