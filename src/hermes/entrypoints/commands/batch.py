"""``hermes batch``: see :mod:`hermes.entrypoints.batch`."""

from hermes.entrypoints import batch as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="batch",
    summary="Check a research batch document (ADR-026 §1).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
