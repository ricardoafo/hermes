"""``hermes version``: see :mod:`hermes.entrypoints.version`."""

from __future__ import annotations

from hermes.entrypoints import version as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="version",
    summary="Print the package version, live release, Python and ledger migration level.",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
