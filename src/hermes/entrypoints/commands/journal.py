"""``hermes journal``: see :mod:`hermes.entrypoints.journal`."""

from hermes.entrypoints import journal as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="journal",
    summary="Record, list and review Code Owner decisions (decision journal).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
    json=True,
)
