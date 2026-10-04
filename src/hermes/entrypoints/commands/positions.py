"""``hermes positions``: open shadow positions; see :mod:`hermes.entrypoints.console`."""

from __future__ import annotations

import os
from typing import TextIO

from hermes.entrypoints import console as _console
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]


def _run(argv: list[str], *, stream: TextIO, demo: str | None, err: TextIO | None = None) -> int:
    return _console.run_positions(argv, stream, demo_flag=demo, env=os.environ, err=err)


COMMAND = Command(
    name="positions",
    summary="Open shadow positions, net per row; `positions COIN` for the detail.",
    run=_run,
    wants_demo=True,
)
