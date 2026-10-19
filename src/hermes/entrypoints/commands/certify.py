"""``hermes certify``: see :mod:`hermes.entrypoints.certify`."""

from hermes.entrypoints import certify as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="certify",
    summary="Evaluate the next certification gate for a strategy (pre-reg §7).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
