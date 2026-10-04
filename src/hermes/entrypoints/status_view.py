"""Render a :class:`~hermes.application.status.StatusReport` for people and scripts.

- :func:`render_status` draws the human view with Rich: the verdict first
  (coloured, plain words, "Your move"), then the stream table, then signal
  and position counts. On a terminal the verdict sits in a panel; when
  stdout is not a terminal, or ``NO_COLOR`` is set, Rich drops colour and
  the verdict prints as plain lines, so the output pastes cleanly.
- :func:`report_to_dict` gives the same facts as JSON-ready data for scripts,
  vps-diagnose and the healthchecks ping.

Times show in UTC and Europe/Madrid. No P&L field exists in either form.

Reference: openspec/changes/370-hermes-status/change.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hermes.application.status import (
    MADRID,
    StatusReport,
    StreamState,
    StreamStatus,
    UnitState,
    VerdictLevel,
)

__all__ = ["render_status", "report_to_dict"]

_VERDICT_STYLE: Final = {
    VerdictLevel.OK: ("●", "bold green"),
    VerdictLevel.LATE: ("▲", "bold yellow"),
    VerdictLevel.DOWN: ("■", "bold red"),
}
_STATE_STYLE: Final = {
    StreamState.ON_TIME: "green",
    StreamState.LATE: "yellow",
    StreamState.NO_DATA: "red",
}
_DASH: Final = "-"


def render_status(
    report: StatusReport, console: Console, *, bot: UnitState = UnitState.UNKNOWN
) -> None:
    """Print the human view of ``report`` to ``console``.

    Args:
        report: The report to show.
        console: Where to print. Its colour and terminal settings decide
            whether the verdict is a panel or plain lines.
    """
    console.print(_header(report, bot))
    console.print()
    verdict = _verdict(report)
    if console.is_terminal:
        _, style = _VERDICT_STYLE[report.verdict.level]
        # SQUARE, not Rich's default ROUNDED: Rich swaps ROUNDED for SQUARE on
        # Windows consoles, so the default would draw differently per OS.
        console.print(
            Panel(
                verdict,
                box=box.SQUARE,
                border_style=style.removeprefix("bold "),
                expand=False,
            )
        )
    else:
        console.print(verdict)
    console.print()
    if report.streams:
        console.print(_stream_table(report.streams))
        console.print()
    console.print(f"signals    24h: {report.signals_24h}   7d: {report.signals_7d}")
    console.print(f"positions  open: {len(report.open_entries)} (shadow, entry facts only)")
    for entry in report.open_entries:
        console.print(
            f"  {entry.pair}  opened {_utc(entry.entry_time)} UTC"
            f" ({_madrid(entry.entry_time)} Madrid)  entry {entry.entry_price}"
        )


def report_to_dict(report: StatusReport, *, bot: UnitState = UnitState.UNKNOWN) -> dict[str, Any]:
    """Return ``report`` as JSON-ready data with the same facts as the human view.

    Args:
        report: The report to convert.

    Returns:
        A dict of strings, ints and lists; datetimes as ISO-8601 UTC.
    """
    return {
        "generated_at": report.generated_at.isoformat(),
        "unit": report.unit.value,
        "bot": _bot_label(bot),
        "verdict": {
            "level": report.verdict.level.value,
            "headline": report.verdict.headline,
            "reason": report.verdict.reason,
            "next_step": report.verdict.next_step,
        },
        "streams": [
            {
                "pair": str(s.pair),
                "interval": s.interval.value,
                "last_close": _iso(s.last_close),
                "next_close": _iso(s.next_close),
                "due": _iso(s.due),
                "state": s.state.value,
                "late_by_seconds": int(s.late_by.total_seconds()),
            }
            for s in report.streams
        ],
        "bars_24h": {interval.value: count for interval, count in report.bars_24h},
        "signals": {"last_24h": report.signals_24h, "last_7d": report.signals_7d},
        "open_positions": [
            {
                "pair": str(e.pair),
                "entry_time": e.entry_time.isoformat(),
                "entry_price": str(e.entry_price),
            }
            for e in report.open_entries
        ],
    }


def _header(report: StatusReport, bot: UnitState) -> Text:
    """Return the one-line header: when, and what systemd says about both units."""
    at = report.generated_at
    bot_part = "" if bot is UnitState.UNKNOWN else f"   bot: {_bot_label(bot)}"
    return Text.assemble(
        ("hermes status", "bold"),
        f"   {_utc(at)} UTC ({_madrid(at)} Madrid)   unit: {report.unit.value}{bot_part}",
    )


def _bot_label(bot: UnitState) -> str:
    """Return ``running``, ``down``, ``not installed`` or ``unknown`` for the bot unit."""
    if bot in {UnitState.FAILED, UnitState.STOPPED, UnitState.RESTARTING}:
        return "down"
    return bot.value


def _verdict(report: StatusReport) -> Group:
    """Return the verdict block: headline, reason, your move."""
    icon, style = _VERDICT_STYLE[report.verdict.level]
    return Group(
        Text(f"{icon} {report.verdict.headline}", style=style),
        Text(report.verdict.reason),
        Text.assemble("Your move: ", (report.verdict.next_step, "bold")),
    )


def _stream_table(streams: tuple[StreamStatus, ...]) -> Table:
    """Return the per-stream table with UTC and Madrid due times."""
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    for column in ("STREAM", "LAST CLOSE (UTC)", "NEXT DUE (UTC)", "NEXT (MADRID)", "STATE"):
        table.add_column(column, no_wrap=True)
    for s in streams:
        table.add_row(
            f"{s.pair} {s.interval.value}",
            _utc(s.last_close),
            _utc(s.next_close),
            _madrid(s.next_close),
            Text(s.state.value.upper(), style=_STATE_STYLE[s.state]),
        )
    return table


def _utc(at: datetime | None) -> str:
    """Format ``at`` as ``YYYY-MM-DD HH:MM`` in UTC, or a dash."""
    return _DASH if at is None else at.strftime("%Y-%m-%d %H:%M")


def _madrid(at: datetime | None) -> str:
    """Format ``at`` as weekday and ``HH:MM`` in Madrid time, or a dash."""
    return _DASH if at is None else at.astimezone(MADRID).strftime("%a %H:%M")


def _iso(at: datetime | None) -> str | None:
    """Return ``at`` as ISO-8601, or ``None``."""
    return None if at is None else at.isoformat()
