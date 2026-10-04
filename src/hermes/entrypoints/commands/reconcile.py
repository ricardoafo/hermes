"""``hermes reconcile``: see :mod:`hermes.entrypoints.reconcile`."""

from __future__ import annotations

import os

from hermes.entrypoints import reconcile as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="reconcile",
    summary="Read-only monthly reconciliation: book vs events and lots, month P&L, open lots.",
    run=lambda argv, *, stream: _impl.main(argv, stream=stream, env=os.environ),
)
