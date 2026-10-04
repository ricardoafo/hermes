"""``hermes activity`` — what changed, grouped by day (#374).

Usage: ``hermes [--demo SCENARIO] activity [--all] [--json] [--utc] [--db PATH] [--days N]``

A day-grouped feed, newest first: positions opened and closed, stop raises,
restarts, warnings and errors. Quiet bar checks and signals only show with
``--all``. With ``--demo`` (or ``HERMES_CONSOLE_DEMO``) the feed comes from a
packaged fixture scenario and carries the DEMO DATA tag; otherwise it is read
from the ledger, read-only, over the last ``--days`` days.

Exit codes: 0 success, 2 usage or unknown scenario, 3 no ledger or unreadable.

Reference: openspec/changes/374-hermes-activity/change.md.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Final, TextIO

from rich.console import Console

from hermes.adapters.console import UnknownScenarioError
from hermes.adapters.console.ledger_activity import LedgerActivityReader
from hermes.application.console import Surface
from hermes.application.console.activity import activity_to_json, group_activity
from hermes.application.console.render import MADRID, RenderContext, render_activity
from hermes.application.console.views import ActivityDay
from hermes.domain.errors import LedgerError
from hermes.entrypoints import console as console_module

__all__ = ["run_activity"]

_USAGE: Final = (
    "Usage: hermes [--demo SCENARIO] activity [--all] [--json] [--utc] [--db PATH] [--days N]"
)
_DEFAULT_DAYS: Final = 7


def run_activity(
    args: list[str],
    stream: TextIO,
    *,
    demo_flag: str | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> int:
    """Run ``hermes activity``.

    Args:
        args: Arguments after ``activity``.
        stream: Output stream; colour follows Rich's TTY and ``NO_COLOR`` detection.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot; defaults to empty.
        now: Read time for the ledger feed; defaults to the wall clock.

    Returns:
        Process exit code.
    """
    parsed = _parse(args, stream, env or {})
    if isinstance(parsed, int):
        return parsed

    tz = UTC if parsed.utc else MADRID
    console = Console(file=stream, emoji=False, markup=False, highlight=False)
    try:
        model = console_module.demo_read_model(demo_flag=demo_flag, env=env or {})
    except UnknownScenarioError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 2

    if model is not None:
        ctx = RenderContext.for_model(
            model, surface=Surface.OPERATOR_CLI, tz=tz, width=console.width
        )
        days = model.activity(include_quiet=parsed.all)
    else:
        at = now if now is not None else datetime.now(UTC)
        ledger_days = _ledger_days(parsed, at, tz, stream)
        if isinstance(ledger_days, int):
            return ledger_days
        days = ledger_days
        ctx = RenderContext(surface=Surface.OPERATOR_CLI, now=at, tz=tz, width=console.width)

    if parsed.json:
        stream.write(json.dumps(activity_to_json(days, ctx), indent=2) + "\n")
    else:
        console.print(render_activity(days, ctx))
    return 0


def _parse(args: list[str], stream: TextIO, env: Mapping[str, str]) -> argparse.Namespace | int:
    """Parse the arguments, or return the exit code for help and usage errors."""
    parser = argparse.ArgumentParser(prog="hermes activity", add_help=False)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--utc", action="store_true")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--days", type=int, default=_DEFAULT_DAYS)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        stream.write(f"ERROR: bad arguments. {_USAGE}\n")
        return 2
    parsed.db = parsed.db or console_module.default_db_path(env)
    if parsed.help:
        stream.write(_USAGE + "\n")
        return 0
    if parsed.days < 1:
        stream.write(f"ERROR: --days must be at least 1. {_USAGE}\n")
        return 2
    return parsed


def _ledger_days(
    parsed: argparse.Namespace, at: datetime, tz: tzinfo, stream: TextIO
) -> tuple[ActivityDay, ...] | int:
    """Read the ledger feed, or return exit code 3 when it is missing or unreadable."""
    try:
        with LedgerActivityReader.open(parsed.db) as reader:
            items = reader.items_since(at - timedelta(days=parsed.days))
    except FileNotFoundError:
        stream.write(f"ERROR: no ledger at {parsed.db}.\n")
        return 3
    except LedgerError as exc:
        stream.write(f"ERROR: could not read the ledger: {exc}\n")
        return 3
    return group_activity(items, tz, include_quiet=parsed.all)
