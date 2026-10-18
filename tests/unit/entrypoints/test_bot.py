"""Tests for the ``hermes bot`` loop (ADR-015).

Invariants protected here:

- one step answers the operator's command, ignores (and logs) a foreign chat,
  drops a repeat inside 5 s, and advances the poll offset past every update;
- the heartbeat check runs at most once a minute and pushes only changes;
- missing Telegram settings exit 3; `hermes bot` is a registered command.

A fake client stands in for Telegram; the client itself has contract tests.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from structlog.testing import capture_logs

from hermes.adapters.telegram_bot import Update
from hermes.application.bot.gate import CommandGate
from hermes.application.bot.heartbeat_alerts import HeartbeatAlerter
from hermes.application.bot.replies import Replies, ReplySources
from hermes.application.status import StatusReport, UnitState, ledger_missing_report
from hermes.entrypoints import bot as bot_module
from hermes.entrypoints import cli
from tests.support.clock import FakeClock
from tests.support.paths import REPO_ROOT

_OPERATOR = "-1001234567890"
_T0 = datetime(2026, 10, 4, 10, tzinfo=UTC)


class _FakeClient:
    def __init__(self, batches: list[list[Update]]) -> None:
        self.batches = batches
        self.offsets: list[int | None] = []
        self.sent: list[tuple[str, str, bool]] = []

    def get_updates(self, offset: int | None, timeout_s: int = 30) -> list[Update]:
        self.offsets.append(offset)
        return self.batches.pop(0) if self.batches else []

    def send_message(self, chat_id: str, text: str, *, silent: bool = False) -> None:
        self.sent.append((chat_id, text, silent))


def _loop(
    batches: list[list[Update]], report: StatusReport, clock: FakeClock
) -> tuple[bot_module.BotLoop, _FakeClient]:
    client = _FakeClient(batches)
    sources = ReplySources(
        status=lambda: report,
        read_model=lambda: None,
        activity=lambda since: [],
        warnings=lambda: cast("Sequence[Any]", []),
    )
    loop = bot_module.BotLoop(
        client=cast("Any", client),
        chat_id=_OPERATOR,
        replies=Replies(sources=sources),
        gate=CommandGate(allowed_chat_id=_OPERATOR),
        alerter=HeartbeatAlerter(),
        status=lambda: report,
        clock=clock,
    )
    return loop, client


def _down() -> StatusReport:
    return ledger_missing_report(_T0, UnitState.FAILED, "/nope")


def test_a_step_answers_the_operator_and_ignores_a_stranger() -> None:
    """The operator's /status gets one reply; a stranger's gets none and is logged."""
    clock = FakeClock(_T0)
    batch = [
        Update(update_id=10, chat_id=_OPERATOR, text="/logs"),
        Update(update_id=11, chat_id="42", text="/status"),
        Update(update_id=12, chat_id=_OPERATOR, text="/logs"),
    ]
    loop, client = _loop([batch], _down(), clock)

    with capture_logs() as logs:
        loop.step()

    replies = [text for chat, text, _ in client.sent if not text.startswith("hermes has no")]
    assert replies == ["No warnings or errors in the journal."]
    assert [e for e in logs if e["event"] == "bot.foreign_chat"] == [
        {"event": "bot.foreign_chat", "chat_id": "42", "log_level": "warning"}
    ]


def test_the_offset_moves_past_every_update() -> None:
    """The next poll asks for updates after the last one seen, answered or not."""
    clock = FakeClock(_T0)
    batch = [Update(update_id=10, chat_id="42", text="/status")]
    loop, client = _loop([batch, []], _down(), clock)

    loop.step()
    loop.step()

    assert client.offsets == [None, 11]


def test_heartbeat_pushes_a_change_once_and_checks_at_most_every_minute() -> None:
    """Down is pushed loudly once; within the minute no second check runs."""
    clock = FakeClock(_T0)
    loop, client = _loop([], _down(), clock)

    loop.step()
    clock.current = _T0 + timedelta(seconds=30)
    loop.step()

    pushes = [(text.split(" since")[0], silent) for _, text, silent in client.sent]
    assert pushes == [("hermes has no ledger", False)]


def test_missing_telegram_settings_exit_three() -> None:
    """Without a token and chat id the bot refuses to start."""
    out, err = io.StringIO(), io.StringIO()

    code = bot_module.main([], stream=out, env={}, err=err)

    assert (code, out.getvalue()) == (3, "")
    assert "HERMES_TELEGRAM__BOT_TOKEN" in err.getvalue()


def test_bot_is_a_registered_command() -> None:
    """`hermes bot --help` dispatches through the top-level CLI."""
    out = io.StringIO()

    assert cli.main(["bot", "--help"], stream=out) == 0
    assert out.getvalue().startswith("Usage: hermes bot")


def test_bot_once_runs_one_step_with_a_fake_client(tmp_path: Path) -> None:
    """--once polls once and exits 0; the down verdict is pushed to the operator."""
    client = _FakeClient([[]])
    config = REPO_ROOT / "config"
    env = {
        "HERMES_TELEGRAM__BOT_TOKEN": "123456789:FAKE-bot-token_for_tests",  # gitleaks:allow
        "HERMES_TELEGRAM__CHAT_ID": _OPERATOR,
    }

    code = bot_module.main(
        ["--once", "--config-dir", str(config), "--db", str(tmp_path / "absent.sqlite3")],
        stream=io.StringIO(),
        env=env,
        client=cast("Any", client),
    )

    assert code == 0
    assert [(chat, silent) for chat, _, silent in client.sent] == [(_OPERATOR, False)]


def test_a_bad_telegram_api_base_exits_three_naming_it() -> None:
    """An unsafe HERMES_TELEGRAM__API_BASE stops the bot before any call (#970)."""
    err = io.StringIO()
    env = {
        "HERMES_TELEGRAM__BOT_TOKEN": "123456789:FAKE-bot-token_for_tests",  # gitleaks:allow
        "HERMES_TELEGRAM__CHAT_ID": _OPERATOR,
        "HERMES_TELEGRAM__API_BASE": "http://api.telegram.org",
    }

    code = bot_module.main([], stream=io.StringIO(), env=env, err=err)

    assert (code, err.getvalue()) == (
        3,
        "ERROR: HERMES_TELEGRAM__API_BASE may use http only on a loopback host: "
        "'http://api.telegram.org'\n",
    )
