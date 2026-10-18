"""``hermes bot`` — the read-only Telegram bot (ADR-015, #379).

Long-polls Telegram, answers ``/status``, ``/next``, ``/signals``, ``/money``,
``/pos`` and ``/logs`` from the operator's chat only, and once a minute pushes
change-only heartbeat alerts. It reads the ledger read-only, the journal and
systemd, and never changes anything. Runs as ``hermes-bot.service``.

Usage: ``hermes bot [--config-dir DIR] [--db PATH] [--once]``

Exit codes: 0 clean stop, 2 usage, 3 missing Telegram settings or bad config.

Reference: openspec/archive/379-telegram-bot/change.md.
"""

from __future__ import annotations

import argparse
import os
import signal
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Final, TextIO

import structlog
from pydantic import SecretStr

from hermes.adapters.console.ledger_activity import LedgerActivityReader
from hermes.adapters.endpoint import (
    DEFAULT_TELEGRAM_API_BASE,
    TELEGRAM_API_BASE_ENV,
    EndpointConfigError,
    api_base_from_env,
)
from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.adapters.systemd_journal import JournalctlReader, JournalUnavailableError
from hermes.adapters.telegram_bot import TelegramBotClient, TelegramBotError, Update
from hermes.application.bot.gate import CommandGate, GateDecision
from hermes.application.bot.heartbeat_alerts import HeartbeatAlerter
from hermes.application.bot.replies import Replies, ReplySources, parse_command
from hermes.application.console import ConsoleReadModel
from hermes.application.console.logs import LogLevel, LogLine, filter_lines, parse_journal_entry
from hermes.application.console.views import ActivityItem
from hermes.application.status import StatusReport, build_report, ledger_missing_report
from hermes.domain.errors import LedgerError
from hermes.entrypoints import console as console_module
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.status import systemd_unit_state
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.config.root import HermesConfig

__all__ = ["BotLoop", "main"]

_log = structlog.get_logger(__name__)
_USAGE: Final = "Usage: hermes bot [--config-dir DIR] [--db PATH] [--once]"
_HEARTBEAT_EVERY: Final = timedelta(minutes=1)
_POLL_TIMEOUT_S: Final = 30
_ERROR_BACKOFF_S: Final = 5.0
_JOURNAL_SCAN: Final = 500


@dataclass
class BotLoop:
    """One bot: answers commands and pushes heartbeat changes.

    Attributes:
        client: Telegram client.
        chat_id: The operator's chat, the only one answered and alerted.
        replies: Builds reply text.
        gate: Allowlist and rate limit.
        alerter: Heartbeat state machine.
        status: Builds the current status report, for heartbeat checks.
        clock: Current UTC time.
    """

    client: TelegramBotClient
    chat_id: str
    replies: Replies
    gate: CommandGate
    alerter: HeartbeatAlerter
    status: Callable[[], StatusReport]
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _offset: int | None = None
    _last_heartbeat: datetime | None = None

    def step(self) -> None:
        """Poll once, answer what arrived, then run a due heartbeat check."""
        for update in self.client.get_updates(self._offset, _POLL_TIMEOUT_S):
            self._offset = update.update_id + 1
            self._handle(update)
        now = self.clock()
        if self._last_heartbeat is None or now - self._last_heartbeat >= _HEARTBEAT_EVERY:
            self._last_heartbeat = now
            report = self.status()
            for message in self.alerter.observe(report.verdict.level, report.verdict.headline, now):
                self.client.send_message(self.chat_id, message.text, silent=message.silent)

    def _handle(self, update: Update) -> None:
        parsed = parse_command(update.text)
        name, args = parsed if parsed is not None else ("help", [])
        decision = self.gate.decide(update.chat_id, name, self.clock())
        if decision is GateDecision.FOREIGN_CHAT:
            _log.warning("bot.foreign_chat", chat_id=update.chat_id)
            return
        if decision is GateDecision.RATE_LIMITED:
            return
        self.client.send_message(self.chat_id, self.replies.reply(name, args, self.clock()))


def main(
    argv: list[str],
    *,
    stream: TextIO,
    env: Mapping[str, str] | None = None,
    client: TelegramBotClient | None = None,
    err: TextIO | None = None,
) -> int:
    """Run ``hermes bot`` until stopped.

    Args:
        argv: Arguments after ``bot``.
        stream: Output stream for usage.
        env: Environment snapshot; defaults to ``os.environ``.
        client: Telegram client override for tests.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = argparse.ArgumentParser(prog="hermes bot", add_help=False)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write(f"ERROR: bad arguments. {_USAGE}\n")
        return 2
    if parsed.help:
        stream.write(_USAGE + "\n")
        return 0
    environ = dict(os.environ) if env is None else dict(env)
    token = environ.get("HERMES_TELEGRAM__BOT_TOKEN", "").strip()
    chat_id = environ.get("HERMES_TELEGRAM__CHAT_ID", "").strip()
    if not token or not chat_id:
        err.write("ERROR: HERMES_TELEGRAM__BOT_TOKEN and HERMES_TELEGRAM__CHAT_ID must be set.\n")
        return 3
    try:
        api_base = api_base_from_env(environ, TELEGRAM_API_BASE_ENV, DEFAULT_TELEGRAM_API_BASE)
    except EndpointConfigError as exc:
        err.write(f"ERROR: {exc}\n")
        return 3
    try:
        config = load_config(parsed.config_dir)
    except ConfigError as exc:
        err.write(f"ERROR: config load failed: {exc}\n")
        return 3

    sources = build_sources(config, parsed.db, environ)
    bot = BotLoop(
        client=client or TelegramBotClient(token=SecretStr(token), api_base=api_base),
        chat_id=chat_id,
        replies=Replies(sources=sources),
        gate=CommandGate(allowed_chat_id=chat_id),
        alerter=HeartbeatAlerter(),
        status=sources.status,
    )
    return _run(bot, once=parsed.once)


def build_sources(config: HermesConfig, db: Path, env: Mapping[str, str]) -> ReplySources:
    """Wire the reply sources to the ledger, the journal, systemd and the read model.

    Args:
        config: Loaded config (pairs, intervals, settle and stale delays).
        db: The ledger file, opened read-only per call.
        env: Environment, for the console demo switch.

    Returns:
        The sources.
    """
    market = config.market_data

    def status() -> StatusReport:
        now = datetime.now(UTC)
        unit = systemd_unit_state()
        try:
            with SqliteStatusReader.open(db) as reader:
                return build_report(
                    reader,
                    pairs=tuple(market.parsed_pairs()),
                    intervals=tuple(market.intervals),
                    now=now,
                    settle=timedelta(seconds=market.settle_delay_seconds),
                    grace=timedelta(seconds=market.stale_after_seconds),
                    unit=unit,
                )
        except FileNotFoundError, LedgerError:
            return ledger_missing_report(now, unit, str(db))

    def read_model() -> ConsoleReadModel | None:
        try:
            return console_module.resolve_read_model(demo_flag=None, env=env)
        except console_module.LiveReadModelUnavailableError:
            return None

    def activity(since: datetime) -> Sequence[ActivityItem]:
        try:
            with LedgerActivityReader.open(db) as reader:
                return reader.items_since(since)
        except FileNotFoundError, LedgerError:
            return ()

    def warnings() -> Sequence[LogLine]:
        try:
            raw = JournalctlReader().lines(last=_JOURNAL_SCAN)
            entries = (line for line in map(parse_journal_entry, raw) if line is not None)
            return list(filter_lines(entries, min_level=LogLevel.WARNING))
        except JournalUnavailableError:
            return []

    return ReplySources(status=status, read_model=read_model, activity=activity, warnings=warnings)


def _run(bot: BotLoop, *, once: bool) -> int:
    previous = signal.signal(signal.SIGTERM, _stop)
    _log.info("bot.started")
    try:
        while True:
            try:
                bot.step()
            except TelegramBotError as exc:
                _log.warning("bot.telegram_error", error=str(exc))
                if not once:
                    time.sleep(_ERROR_BACKOFF_S)
            if once:
                return 0
    except KeyboardInterrupt:
        _log.info("bot.stopped")
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous)


def _stop(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt
