"""Run the installed ``hermes`` entry point as its own process against the lower-environment fakes.

Nothing here is wired by hand: ``hermes serve`` reads the same config, env
variables and entry points as on a box, with ``HERMES_ENV=qa`` and every
endpoint on a fake. The test clock (``HERMES_TEST_CLOCK_URL``, qa only) moves
serve onto the fake market's scripted time; :func:`serve` steps it from one
bar close to the next only once serve has gone quiet, so serve never sees a
close late and never measures a skew that a jump in mid-request would fake.

Shared by the installed-release tests in ``tests/integration/lower_env/``
(issues #972 and #974).
"""

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from tests.support.fake_market.exchange import FakeExchange, SimClock
from tests.support.fake_market.harness import production_config
from tests.support.fake_market.script import Market
from tests.support.fake_market.service import CONTROL_PREFIX, FakeMarketService
from tests.support.fake_notify.server import Call, FakeNotify, Outage, Sink
from tests.support.serve_loop import PROJECT_ROOT

HERMES = Path(sys.executable).parent / "hermes"
TOKEN = "123456:installed-release-fake"  # noqa: S105 - test-only  # gitleaks:allow
CHAT = "-1001234567890"
BAR = timedelta(hours=4)
SETTLE = timedelta(seconds=production_config().market_data.settle_delay_seconds)
_QUIET_S = 0.3
_BOOT_TIMEOUT_S = 60.0
_STEP_TIMEOUT_S = 30.0


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


def market_service(market: Market, live_from: datetime) -> FakeMarketService:
    """Return the fake market service for ``market``, its clock at ``live_from``."""
    fx = httpx.URL(production_config().frankfurter.base_url)
    exchange = FakeExchange(
        market=market,
        clock=SimClock(live_from, market.end),
        fx_host=fx.host,
        fx_prefix=fx.path.rstrip("/"),
    )
    return FakeMarketService(exchange)


def release_env(market: FakeMarketService, notify: FakeNotify) -> dict[str, str]:
    """Point every endpoint the release reads at a fake, as a qa env file would."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_")}
    return env | {
        "HERMES_ENV": "qa",
        "HERMES_MARKET_DATA__BASE_URL": market.binance_base,
        "HERMES_FRANKFURTER__BASE_URL": market.frankfurter_base,
        "HERMES_TELEGRAM__BOT_TOKEN": TOKEN,
        "HERMES_TELEGRAM__CHAT_ID": CHAT,
        "HERMES_TELEGRAM__API_BASE": notify.telegram_base,
        "HERMES_TEST_CLOCK_URL": f"{market.url}{CONTROL_PREFIX}/clock",
    }


def log_rows(log: str) -> list[dict[str, str]]:
    """Return serve's structured log lines, oldest first."""
    return [json.loads(line) for line in log.splitlines() if line.startswith("{")]


def log_events(log: str) -> list[str]:
    """Return the event name of each structured log line, oldest first."""
    return [row["event"] for row in log_rows(log)]


def serve(
    tmp_path: Path,
    market: Market,
    live_from: datetime,
    until: datetime,
    *,
    outage: tuple[Outage, int] | None = None,
    restart: bool = False,
    stop: signal.Signals = signal.SIGTERM,
) -> Run:
    """Boot an installed ``hermes serve`` and step the clock to ``until``.

    Args:
        tmp_path: Directory for the ledger and the log.
        market: The scripted bars.
        live_from: Boot instant; warm-up sees every bar closed by then.
        until: The scripted instant to stop at; a bar is handled once the
            clock reaches its close plus the settle delay.
        outage: Make the next N Telegram requests fail this way.
        restart: Boot on the ledger an earlier run in ``tmp_path`` left,
            as a deploy or a reboot does, instead of a fresh one.
        stop: How the run ends: SIGTERM is systemd's stop, SIGKILL a crash
            or a power cut.

    Returns:
        The run's ledger, log, exit code and Telegram calls.

    """
    db = tmp_path / "hermes.sqlite3"
    log_path = tmp_path / f"serve-{len(list(tmp_path.glob('serve-*.log')))}.log"
    new_ledger = [] if restart else ["--new-ledger"]
    with market_service(market, live_from) as service, FakeNotify(timeout_hold_s=0.5) as notify:
        if outage is not None:
            notify.fail_next(Sink.TELEGRAM, *outage)
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(  # noqa: S603 - this checkout's own entry point
                [
                    str(HERMES),
                    "serve",
                    "--config-dir",
                    str(PROJECT_ROOT / "config"),
                    "--db",
                    str(db),
                    *new_ledger,
                ],
                env=release_env(service, notify),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                _wait_started(log_path, process)
                _wait_quiet(service, process)
                while service.now() < until and process.poll() is None:
                    due = min(_next_due(service.now()), until)
                    service.set_clock(due)
                    _wait_handled(log_path, due, process)
                    _wait_quiet(service, process)
            finally:
                process.send_signal(stop)
                code = process.wait(timeout=30)
        return Run(
            db=db,
            log=log_path.read_text(encoding="utf-8"),
            exit_code=code,
            telegram=notify.calls(Sink.TELEGRAM),
            stopped_at=service.now(),
        )


def _wait_started(log_path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + _BOOT_TIMEOUT_S
    while time.monotonic() < deadline and process.poll() is None:
        if "serve.run.started" in log_path.read_text(encoding="utf-8"):
            return
        time.sleep(0.1)
    pytest.fail(f"serve did not start:\n{log_path.read_text(encoding='utf-8')}")


def _wait_quiet(market: FakeMarketService, process: subprocess.Popen[str]) -> None:
    """Return once serve has made no market request for ``_QUIET_S``: it is waiting on the clock."""
    deadline = time.monotonic() + _STEP_TIMEOUT_S
    seen = len(market.exchange.requests)
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(_QUIET_S)
        now = len(market.exchange.requests)
        if now == seen:
            return
        seen = now
    pytest.fail("serve neither went quiet nor kept running")


def _closes_due(due: datetime) -> set[tuple[str, str, str]]:
    """Return the bars serve handles when the clock reaches ``due``, as its log names them."""
    close = (due - SETTLE - timedelta(milliseconds=1)).isoformat()
    intervals = ["4h", "1d"] if due.hour == 0 else ["4h"]
    pairs = production_config().market_data.pairs
    return {(pair, interval, close) for pair in pairs for interval in intervals}


def _wait_handled(log_path: Path, due: datetime, process: subprocess.Popen[str]) -> None:
    """Return once serve has logged every bar that closed at ``due``.

    Quiet alone could be a slow runner between two requests; the log says the
    bars are done, so the next step never lands in the middle of one.
    """
    expected = _closes_due(due)
    deadline = time.monotonic() + _STEP_TIMEOUT_S
    while time.monotonic() < deadline and process.poll() is None:
        processed = {
            (row["pair"], row["interval"], row["close_time"])
            for row in log_rows(log_path.read_text(encoding="utf-8"))
            if row.get("event") == "serve.bar.processed"
        }
        if expected <= processed:
            return
        time.sleep(0.05)
    pytest.fail(f"serve did not handle the {due.isoformat()} close")


def _next_due(now: datetime) -> datetime:
    """Return the next 4h close plus the settle delay, the instant serve wakes for."""
    boundary = now.replace(hour=now.hour - now.hour % 4, minute=0, second=0, microsecond=0)
    due = boundary + SETTLE
    while due <= now:
        due += BAR
    return due
