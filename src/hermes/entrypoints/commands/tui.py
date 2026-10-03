"""``hermes tui``: the read-only home screen; see :mod:`hermes.entrypoints.console`."""

from __future__ import annotations

import os
from typing import TextIO

from hermes.entrypoints import console as _console
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]


def _run(argv: list[str], *, stream: TextIO, demo: str | None) -> int:
    return _console.run_tui(argv, stream, demo_flag=demo, env=os.environ)


COMMAND = Command(
    name="tui",
    summary="Read-only home screen: portfolio, positions, activity (needs --demo for now).",
    run=_run,
    wants_demo=True,
)
