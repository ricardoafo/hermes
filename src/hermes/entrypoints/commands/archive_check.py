"""``hermes archive-check``: see :mod:`hermes.entrypoints.archive_check`."""

from hermes.entrypoints import archive_check as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="archive-check",
    summary="Compare yesterday's live bars with the Binance archive (read-only).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
