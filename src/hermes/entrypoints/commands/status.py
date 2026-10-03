"""``hermes status``: see :mod:`hermes.entrypoints.status`."""

from __future__ import annotations

from hermes.entrypoints import status as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="status",
    summary="Read-only progress check: verdict, last and next bar per stream.",
    run=lambda argv, *, stream: _impl.main(argv, stream=stream),
)
