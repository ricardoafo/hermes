"""``hermes fx``: see :mod:`hermes.entrypoints.fx`."""

from __future__ import annotations

from hermes.entrypoints import fx as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="fx",
    summary="`hermes fx backfill` fills pending EUR values in the ledger.",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
