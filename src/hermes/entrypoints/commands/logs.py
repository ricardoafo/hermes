"""``hermes logs``: hermes's journal, readable; see :mod:`hermes.entrypoints.logs`."""

from __future__ import annotations

from hermes.entrypoints import logs as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="logs",
    summary="Read hermes's journal: one line per entry, filter by level, time or cycle.",
    run=lambda argv, *, stream: _impl.main(argv, stream=stream),
)
