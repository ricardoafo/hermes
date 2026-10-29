"""``hermes anomalies``: see :mod:`hermes.entrypoints.anomalies`."""

from hermes.entrypoints import anomalies as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="anomalies",
    summary="List the anomalies recorded in the ledger, newest first (read-only).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
    json=True,
)
