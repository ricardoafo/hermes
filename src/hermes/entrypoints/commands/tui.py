"""``hermes tui``: the read-only home screen; see :mod:`hermes.entrypoints.console`."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final, TextIO

from hermes.adapters.console import UnknownScenarioError
from hermes.application.console import ConsoleReadModel
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.commands import Command
from hermes.entrypoints.console import (
    LiveReadModelUnavailableError,
    recent_journal_lines,
    resolve_read_model,
)

__all__ = ["COMMAND", "run_tui"]

_TUI_USAGE: Final = "Usage: hermes [--demo SCENARIO] tui [--db PATH]"
_TUI_HELP: Final = (
    f"{_TUI_USAGE}\n"
    "  --db PATH   ledger to read (default: the service ledger)\n"
    "  -h, --help  show this help\n"
    "Exit codes: 0 after the operator quits, 2 usage, unknown scenario or no data source."
)
_DB: Final = "--db"


class _BadArgumentsError(ValueError):
    """The arguments are not ``[--db PATH]``."""


def run_tui(
    args: list[str],
    stream: TextIO,
    *,
    demo_flag: str | None = None,
    env: Mapping[str, str] | None = None,
    runner: Callable[[ConsoleReadModel], None] | None = None,
    err: TextIO | None = None,
) -> int:
    """Run ``hermes tui``, the read-only home screen.

    Without a demo scenario the screen reads the ledger at ``--db``, read-only
    (change ``live-home-screen`` D-6). A card the ledger cannot answer yet
    says so on the screen instead of stopping the app.

    Args:
        args: Arguments after ``tui``: only ``--db PATH``.
        stream: Output stream.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot; defaults to empty.
        runner: Runs the app on the read model; defaults to the Textual home
            screen (imported lazily so the other commands stay fast).
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        0 after the operator quits, 2 for a usage error, an unknown scenario or
        no data source (no demo and no ledger file).
    """
    err = resolve_err(err)
    if "-h" in args or "--help" in args:
        stream.write(_TUI_HELP + "\n")
        return 0
    try:
        db_path = _db_path(args)
    except _BadArgumentsError:
        err.write(f"ERROR: bad arguments. {_TUI_USAGE}\n")
        return 2
    try:
        model = resolve_read_model(demo_flag=demo_flag, env=env or {}, db_path=db_path)
    except (UnknownScenarioError, LiveReadModelUnavailableError) as exc:
        err.write(f"ERROR: {exc}\n")
        return 2
    if runner is None:
        from hermes.entrypoints.tui import run_home  # noqa: PLC0415 - Textual is slow to import

        def runner(read_model: ConsoleReadModel) -> None:
            run_home(read_model, journal=recent_journal_lines)

    runner(model)
    return 0


def _db_path(args: list[str]) -> Path | None:
    """Return the ``--db`` path, or ``None`` without one.

    Raises:
        _BadArgumentsError: Anything other than ``--db PATH`` or ``--db=PATH``.
    """
    match args:
        case []:
            return None
        case [flag, value] if flag == _DB and value and not value.startswith("-"):
            return Path(value)
        case [single] if single.startswith(f"{_DB}=") and single != f"{_DB}=":
            return Path(single.removeprefix(f"{_DB}="))
        case _:
            raise _BadArgumentsError


def _run(argv: list[str], *, stream: TextIO, demo: str | None, err: TextIO | None = None) -> int:
    return run_tui(argv, stream, demo_flag=demo, env=os.environ, err=err)


COMMAND = Command(
    name="tui",
    summary="Read-only home screen: portfolio, positions, activity, shadow test.",
    run=_run,
    wants_demo=True,
)
