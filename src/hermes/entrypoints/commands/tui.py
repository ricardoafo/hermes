"""``hermes tui``: the read-only home screen; see :mod:`hermes.entrypoints.console`."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Final, TextIO

from hermes.adapters.console import UnknownScenarioError
from hermes.application.console import ConsoleReadModel
from hermes.entrypoints.commands import Command
from hermes.entrypoints.console import LiveReadModelUnavailableError, resolve_read_model

__all__ = ["COMMAND", "run_tui"]

_TUI_USAGE: Final = "Usage: hermes [--demo SCENARIO] tui"


def run_tui(
    args: list[str],
    stream: TextIO,
    *,
    demo_flag: str | None = None,
    env: Mapping[str, str] | None = None,
    runner: Callable[[ConsoleReadModel], None] | None = None,
) -> int:
    """Run ``hermes tui``, the read-only home screen.

    Without a demo scenario there is no live read model yet, so the command
    says so and exits with a usage error instead of showing an empty screen.

    Args:
        args: Arguments after ``tui``; none are accepted.
        stream: Output stream for errors.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot; defaults to empty.
        runner: Runs the app on the read model; defaults to the Textual home
            screen (imported lazily so the other commands stay fast).

    Returns:
        0 after the operator quits, 2 for a usage error, an unknown scenario or
        no data source.
    """
    if args:
        stream.write(f"ERROR: bad arguments. {_TUI_USAGE}\n")
        return 2
    try:
        model = resolve_read_model(demo_flag=demo_flag, env=env or {})
    except (LiveReadModelUnavailableError, UnknownScenarioError) as exc:
        stream.write(f"ERROR: {exc}\n")
        return 2
    if runner is None:
        from hermes.entrypoints.tui import run_home  # noqa: PLC0415 - Textual is slow to import

        runner = run_home
    runner(model)
    return 0


def _run(argv: list[str], *, stream: TextIO, demo: str | None) -> int:
    return run_tui(argv, stream, demo_flag=demo, env=os.environ)


COMMAND = Command(
    name="tui",
    summary="Read-only home screen: portfolio, positions, activity (needs --demo for now).",
    run=_run,
    wants_demo=True,
)
