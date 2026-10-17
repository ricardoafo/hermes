"""``hermes trials``: see :mod:`hermes.entrypoints.trials`."""

from hermes.entrypoints import trials as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="trials",
    summary="Repair a trial log whose last line a power loss cut (asks to confirm).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
