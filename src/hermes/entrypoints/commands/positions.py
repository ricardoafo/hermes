"""``hermes positions``: open shadow positions; see :mod:`hermes.entrypoints.console`.

Without ``--demo`` the positions are read from the ledger, read-only
(``--db PATH``, default ``./hermes.sqlite3``); with no ledger file the answer is
the empty state (change ``ledger-read-model`` D-9).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC
from pathlib import Path
from typing import Final, TextIO

from rich.console import Console

from hermes.adapters.console import UnknownScenarioError
from hermes.application.console import ConsoleReadModel, Surface
from hermes.application.console.render import (
    MADRID,
    RenderContext,
    render_position_detail,
    render_positions,
)
from hermes.application.console.serialize import position_detail_to_json, positions_to_json
from hermes.domain.errors import LedgerError
from hermes.entrypoints.commands import Command
from hermes.entrypoints.console import LiveReadModelUnavailableError, resolve_read_model

__all__ = ["COMMAND", "run_positions"]

_NO_FILLS_NOTE: Final = "Fills start with #353."
_POSITIONS_USAGE: Final = (
    "Usage: hermes [--demo SCENARIO] positions [COIN] [--json] [--utc] [--db PATH]"
)
_DB: Final = "--db"
_POSITIONS_HELP: Final = (
    f"{_POSITIONS_USAGE}\n"
    "  COIN       show the detail for one coin\n"
    "  --json     one JSON document on stdout\n"
    "  --utc      show times in UTC instead of Europe/Madrid\n"
    "  --db PATH  ledger to read (default: the service ledger)\n"
    "  -h, --help show this help\n"
    "Exit codes: 0 success, 1 COIN has no open position, 2 usage or unknown scenario, "
    "3 ledger unreadable."
)


def run_positions(
    args: list[str],
    stream: TextIO,
    *,
    demo_flag: str | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run ``hermes positions [COIN] [--json] [--utc] [--db PATH]``.

    Without a demo scenario the positions come from the ledger at ``--db``. With
    no ledger file no position can exist, so the answer is the empty state plus
    a note, not an error (console-positions D-1).

    Args:
        args: Arguments after ``positions``.
        stream: Output stream; colour follows Rich's TTY and ``NO_COLOR`` detection.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot; defaults to empty.

    Returns:
        0 on success, 1 when COIN has no open position, 2 for a usage error or
        an unknown scenario, 3 when the ledger cannot be read.
    """
    if "-h" in args or "--help" in args:
        stream.write(_POSITIONS_HELP + "\n")
        return 0
    split = _split_db(args)
    rest, db_path = split if split is not None else ([], None)
    flags = {a for a in rest if a.startswith("-")}
    coins = [a for a in rest if not a.startswith("-")]
    if split is None or flags - {"--json", "--utc"} or len(coins) > 1:
        stream.write(f"ERROR: bad arguments. {_POSITIONS_USAGE}\n")
        return 2
    as_json = "--json" in flags
    coin = coins[0].upper() if coins else None
    try:
        model = resolve_read_model(demo_flag=demo_flag, env=env or {}, db_path=db_path)
    except LiveReadModelUnavailableError:
        return _positions_without_data(stream, as_json=as_json)
    except UnknownScenarioError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 2
    try:
        return _show(model, coin, stream, utc="--utc" in flags, as_json=as_json)
    except LedgerError as exc:
        stream.write(f"ERROR: could not read the ledger: {exc}\n")
        return 3


def _split_db(args: list[str]) -> tuple[list[str], Path | None] | None:
    """Remove ``--db PATH`` or ``--db=PATH``; ``None`` when the path is missing."""
    rest: list[str] = []
    db_path: Path | None = None
    items = iter(args)
    for arg in items:
        if arg == _DB:
            value = next(items, None)
            if value is None or value.startswith("-"):
                return None
            db_path = Path(value)
        elif arg.startswith(f"{_DB}="):
            value = arg.removeprefix(f"{_DB}=")
            if not value:
                return None
            db_path = Path(value)
        else:
            rest.append(arg)
    return rest, db_path


def _show(
    model: ConsoleReadModel, coin: str | None, stream: TextIO, *, utc: bool, as_json: bool
) -> int:
    console = Console(file=stream, emoji=False, markup=False, highlight=False)
    ctx = RenderContext.for_model(
        model,
        surface=Surface.OPERATOR_CLI,
        tz=UTC if utc else MADRID,
        width=console.width,
    )
    if coin is None:
        rows = model.positions()
        if as_json:
            stream.write(json.dumps(positions_to_json(rows, ctx), indent=2) + "\n")
        else:
            console.print(render_positions(rows, ctx))
        return 0
    return _print_detail(model, coin, ctx, console, as_json=as_json)


def _print_detail(
    model: ConsoleReadModel, coin: str, ctx: RenderContext, console: Console, *, as_json: bool
) -> int:
    detail = model.position(coin)
    if as_json:
        payload = (
            {"error": f"no open position for {coin}"}
            if detail is None
            else position_detail_to_json(detail, ctx)
        )
        console.file.write(json.dumps(payload, indent=2) + "\n")
    else:
        console.print(render_position_detail(detail, ctx))
    return 0 if detail is not None else 1


def _positions_without_data(stream: TextIO, *, as_json: bool) -> int:
    if as_json:
        stream.write(json.dumps({"positions": [], "note": _NO_FILLS_NOTE}, indent=2) + "\n")
    else:
        stream.write(f"No open shadow positions.\n{_NO_FILLS_NOTE}\n")
    return 0


def _run(argv: list[str], *, stream: TextIO, demo: str | None) -> int:
    return run_positions(argv, stream, demo_flag=demo, env=os.environ)


COMMAND = Command(
    name="positions",
    summary="Open shadow positions, net per row; `positions COIN` for the detail.",
    run=_run,
    wants_demo=True,
)
