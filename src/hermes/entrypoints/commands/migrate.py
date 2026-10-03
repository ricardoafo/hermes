"""``hermes migrate``: see :mod:`hermes.entrypoints.migrate`."""

from __future__ import annotations

from hermes.entrypoints import migrate as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="migrate",
    summary="Snapshot the ledger, then apply pending migrations (stop hermes first).",
    run=lambda argv, *, stream: _impl.main(argv, stream=stream),
)
