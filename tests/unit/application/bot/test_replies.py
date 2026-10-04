"""Tests for the bot's reply text (ADR-015 D-02).

Invariants protected here:

- commands parse from Telegram text, including ``/cmd@botname args``;
- ``/status`` leads with the ``hermes status`` verdict and next step, then the
  next check in Madrid and UTC and what happened since the last ``/status``;
- ``/next``, ``/signals`` and ``/logs`` render from the shared layers;
- ``/money`` and ``/pos`` show money on a pulled reply and none on a blind
  surface, and say there is no live money without a read model;
- anything else gets the one-line help.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from hermes.adapters.console import FixtureReadModel
from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.application.bot.replies import Replies, ReplySources, parse_command
from hermes.application.console import ConsoleReadModel, Surface
from hermes.application.console.logs import LogLine
from hermes.application.console.views import ActivityItem, ActivityKind
from hermes.application.status import StatusReport, UnitState, build_report
from tests.support.status_ledger import INTERVALS, LAST_4H, NOW, PAIRS, write_ledger

_MONEY = re.compile(r"€|net |gross|fees")


def _report(tmp_path: Path) -> StatusReport:
    with SqliteStatusReader.open(write_ledger(tmp_path / "hermes.sqlite3")) as reader:
        return build_report(
            reader,
            pairs=PAIRS,
            intervals=INTERVALS,
            now=NOW,
            settle=timedelta(seconds=5),
            grace=timedelta(seconds=900),
            unit=UnitState.RUNNING,
        )


def _replies(
    tmp_path: Path,
    *,
    model: ConsoleReadModel | None = None,
    activity: Sequence[ActivityItem] = (),
    warnings: Sequence[LogLine] = (),
    surface: Surface = Surface.TELEGRAM_PULLED,
) -> Replies:
    report = _report(tmp_path)
    sources = ReplySources(
        status=lambda: report,
        read_model=lambda: model,
        activity=lambda since: [i for i in list(activity) if i.at >= since],
        warnings=lambda: warnings,
    )
    return Replies(sources=sources, surface=surface)


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("/status", ("status", [])),
        ("/money@hermes_bot Week", ("money", ["Week"])),
        ("  /pos eth ", ("pos", ["eth"])),
        ("hello", None),
        ("", None),
    ],
)
def test_parse_command(text: str, parsed: tuple[str, list[str]] | None) -> None:
    """Telegram command text splits into a lower-case name and its arguments."""
    assert parse_command(text) == parsed


def test_status_leads_with_the_verdict_and_next_check(tmp_path: Path) -> None:
    """The same verdict and next step as `hermes status`, then the next check."""
    lines = _replies(tmp_path).reply("status", [], NOW).splitlines()

    assert lines[:3] == [
        "All good. Watching BTC, ETH and SOL. Every check ran on time.",
        "Your move: nothing.",
        "Next check 22:00 (20:00 UTC).",
    ]


def test_status_reports_what_happened_since_the_last_status(tmp_path: Path) -> None:
    """The second /status counts only what came after the first."""
    activity = [ActivityItem(at=NOW - timedelta(hours=2), kind=ActivityKind.SIGNAL, coin="ETH")]
    replies = _replies(tmp_path, activity=activity)

    first = replies.reply("status", [], NOW).splitlines()[3]
    activity.append(
        ActivityItem(at=NOW + timedelta(minutes=30), kind=ActivityKind.OPENED, coin="ETH")
    )
    second = replies.reply("status", [], NOW + timedelta(hours=1)).splitlines()[3]

    assert first == "Since 18:10: 1 signals, 0 opened, 0 closed."
    assert second == "Since 18:10: 0 signals, 1 opened, 0 closed."


def test_status_without_live_data_says_the_test_has_not_started(tmp_path: Path) -> None:
    """No read model: the 90-day line says what it waits on, and no money is shown."""
    text = _replies(tmp_path).reply("status", [], NOW)

    assert text.splitlines()[-1] == "90-day test: not started, waits on #353."
    assert _MONEY.findall(text) == []


def test_status_on_demo_data_adds_total_and_todays_net(tmp_path: Path) -> None:
    """With a read model, a pulled /status ends with total value and today's net."""
    model = FixtureReadModel.load("ok-two-positions")

    last = _replies(tmp_path, model=model).reply("status", [], NOW).splitlines()[-1]

    assert last.startswith("Total €")
    assert "today's net" in last


def test_next_lists_every_stream_in_madrid_and_utc(tmp_path: Path) -> None:
    """One line per stream with its next close."""
    lines = _replies(tmp_path).reply("next", [], NOW).splitlines()

    assert lines[0] == "Next bars:"
    assert lines[1] == "BTC/USDT 4h: 22:00 (20:00 UTC)"
    assert len(lines) == 1 + len(PAIRS) * len(INTERVALS)


def test_signals_lists_the_last_seven_days_newest_first(tmp_path: Path) -> None:
    """Signals older than 7 days are left out."""
    activity = [
        ActivityItem(at=LAST_4H, kind=ActivityKind.SIGNAL, coin="ETH", text="entry long"),
        ActivityItem(at=LAST_4H - timedelta(days=8), kind=ActivityKind.SIGNAL, coin="BTC"),
        ActivityItem(at=LAST_4H, kind=ActivityKind.OPENED, coin="ETH"),
    ]

    text = _replies(tmp_path, activity=activity).reply("signals", [], NOW)

    assert text.splitlines() == ["1 signals in the last 7 days:", "Sat 18:00 ETH entry long"]


def test_money_without_a_read_model_says_there_is_none_yet(tmp_path: Path) -> None:
    """Before fills land there is no live money to show."""
    assert _replies(tmp_path).reply("money", [], NOW).startswith("No live money yet")


def test_money_on_demo_data_shows_the_waterfall(tmp_path: Path) -> None:
    """Gross, fees, tax and net for today and this year."""
    model = FixtureReadModel.load("ok-two-positions")

    lines = _replies(tmp_path, model=model).reply("money", ["year"], NOW).splitlines()

    assert lines[0].startswith("Total €") and lines[0].endswith(" year)")
    assert lines[1].startswith("Today: gross ")
    assert lines[2].startswith("This year: gross ")


def test_money_with_a_bad_range_shows_usage(tmp_path: Path) -> None:
    """Only today, week, month and year are ranges."""
    model = FixtureReadModel.load("ok-two-positions")

    assert _replies(tmp_path, model=model).reply("money", ["decade"], NOW) == (
        "Usage: /money [week|month|year]"
    )


@pytest.mark.parametrize("command", ["money", "pos"])
def test_blind_surfaces_never_get_money(tmp_path: Path, command: str) -> None:
    """A pushed surface shows no figure even with demo money available."""
    model = FixtureReadModel.load("ok-two-positions")
    replies = _replies(tmp_path, model=model, surface=Surface.TELEGRAM_PUSHED)

    assert _MONEY.findall(replies.reply(command, [], NOW)) == []


def test_pos_lists_positions_with_net_and_the_detail_with_the_waterfall(tmp_path: Path) -> None:
    """Rows show net only; /pos COIN shows gross, fees, tax and net."""
    model = FixtureReadModel.load("ok-two-positions")
    replies = _replies(tmp_path, model=model)
    coin = model.positions()[0].coin

    rows = replies.reply("pos", [], NOW).splitlines()
    detail = replies.reply("pos", [coin.lower()], NOW).splitlines()

    assert rows[0] == f"{len(model.positions())} open (simulated)"
    assert " · net " in rows[1]
    assert detail[0].startswith(f"{coin} ")
    assert detail[-1].startswith("Gross ")


def test_logs_shows_the_last_twenty_warning_lines(tmp_path: Path) -> None:
    """At most 20 lines, newest last, in Madrid time."""
    lines = [
        LogLine(at=NOW + timedelta(minutes=i), level="warning", event=f"e{i}") for i in range(25)
    ]

    text = _replies(tmp_path, warnings=lines).reply("logs", [], NOW).splitlines()

    assert (len(text), text[0], text[-1]) == (20, "18:15 WARNING e5", "18:34 WARNING e24")


def test_unknown_commands_get_the_help_line(tmp_path: Path) -> None:
    """Anything else gets one line listing the commands."""
    assert _replies(tmp_path).reply("pause", [], NOW).startswith("Commands: /status")
