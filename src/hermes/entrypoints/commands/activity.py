"""``hermes activity``: the day-grouped console feed; see :mod:`hermes.entrypoints.activity`."""

from __future__ import annotations

import os
from typing import TextIO

from hermes.entrypoints import activity as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]


def _run(argv: list[str], *, stream: TextIO, demo: str | None) -> int:
    return _impl.run_activity(argv, stream, demo_flag=demo, env=os.environ)


COMMAND = Command(
    name="activity",
    summary="What changed, grouped by day; `--all` adds bar checks and signals.",
    run=_run,
    wants_demo=True,
)
