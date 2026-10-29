"""``hermes activity`` — what changed, grouped by day (#374).

Usage: ``hermes [--demo SCENARIO] activity [--all] [--json] [--utc] [--db PATH] [--days N]``

A day-grouped feed, newest first: positions opened and closed, stop raises,
restarts, warnings and errors. Quiet bar checks and signals only show with
``--all``. With ``--demo`` (or ``HERMES_CONSOLE_DEMO``) the feed comes from a
packaged fixture scenario and carries the DEMO DATA tag; otherwise it comes
from the live console read model over the ledger, read-only, over the last
``--days`` days (#546).

Exit codes: 0 success, 2 usage or unknown scenario, 3 no ledger or unreadable.

Reference: openspec/archive/374-hermes-activity/change.md;
openspec/archive/live-home-screen/change.md (D-5, D-6).
"""

import argparse
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Final, TextIO

from rich.console import Console

from hermes.adapters.console import UnknownScenarioError
from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.application.console import ConsoleReadModel, Surface
from hermes.application.console.activity import activity_to_json
from hermes.application.console.render import MADRID, RenderContext, render_activity
from hermes.domain.errors import LedgerError
from hermes.entrypoints import console as console_module
from hermes.entrypoints._cli import add_db_arg
from hermes.entrypoints._streams import resolve_err

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
    err: TextIO | None = None,
) -> int:
    """Run ``hermes activity``.

    Args:
        args: Arguments after ``activity``.
        stream: Output stream; colour follows Rich's TTY and ``NO_COLOR`` detection.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot; defaults to empty.
        now: Read time for the ledger feed; defaults to the wall clock.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parsed = _parse(args, stream, err, env or {})
    if isinstance(parsed, int):
        return parsed

    tz = UTC if parsed.utc else MADRID
    console = Console(file=stream, emoji=False, markup=False, highlight=False)
    model: ConsoleReadModel | None
    try:
        model = console_module.demo_read_model(demo_flag=demo_flag, env=env or {})
    except UnknownScenarioError as exc:
        err.write(f"ERROR: {exc}\n")
        return 2

    if model is None:
        at = now if now is not None else datetime.now(UTC)
        live = _ledger_model(parsed, at, tz, err)
        if isinstance(live, int):
            return live
        model = live
    try:
        ctx = RenderContext.for_model(
            model, surface=Surface.OPERATOR_CLI, tz=tz, width=console.width
        )
        days = model.activity(include_quiet=parsed.all)
    except LedgerError as exc:
        err.write(f"ERROR: could not read the ledger: {exc}\n")
        return 3

    if parsed.json:
        stream.write(json.dumps(activity_to_json(days, ctx), indent=2) + "\n")
    else:
        console.print(render_activity(days, ctx))
    return 0


def _parse(
    args: list[str], stream: TextIO, err: TextIO, env: Mapping[str, str]
) -> argparse.Namespace | int:
    """Parse the arguments, or return the exit code for help and usage errors."""
    parser = argparse.ArgumentParser(prog="hermes activity", add_help=False)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--utc", action="store_true")
    add_db_arg(parser, default=None)
    parser.add_argument("--days", type=int, default=_DEFAULT_DAYS)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        err.write(f"ERROR: bad arguments. {_USAGE}\n")
        return 2
    parsed.db = parsed.db or console_module.default_db_path(env)
    if parsed.help:
        stream.write(_USAGE + "\n")
        return 0
    if parsed.days < 1:
        err.write(f"ERROR: --days must be at least 1. {_USAGE}\n")
        return 2
    return parsed


def _ledger_model(
    parsed: argparse.Namespace, at: datetime, tz: tzinfo, err: TextIO
) -> ConsoleReadModel | int:
    """Open the ledger read model, or return exit code 3 when there is no ledger."""
    try:
        return LedgerReadModel.open(
            parsed.db,
            clock=lambda: at,
            tz=tz,
            activity_window=timedelta(days=parsed.days),
            journal=console_module.journal_window_lines,
        )
    except FileNotFoundError:
        err.write(f"ERROR: no ledger at {parsed.db}.\n")
        return 3
