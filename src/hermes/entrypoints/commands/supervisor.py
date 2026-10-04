"""``hermes supervisor``: see :mod:`hermes.entrypoints.supervisor`."""

from __future__ import annotations

import os

from hermes.entrypoints import supervisor as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="supervisor",
    summary="Read-only, outcome-blind supervisor tools; `mcp` serves them, `brief` sums them up.",
    run=lambda argv, *, stream: _impl.main(argv, stream=stream, env=os.environ),
)
