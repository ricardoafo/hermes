"""``hermes shadow-start``: see :mod:`hermes.entrypoints.shadow_start`."""

from hermes.entrypoints import shadow_start as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="shadow-start",
    summary="Check shadow Ready, archive the warm-up ledger and record day 0 (VPS, operator).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
