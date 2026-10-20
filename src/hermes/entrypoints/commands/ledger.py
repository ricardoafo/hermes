"""``hermes ledger``: see :mod:`hermes.entrypoints.ledger`."""

from hermes.entrypoints import ledger as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="ledger",
    summary="Ledger tools; `verify` checks it read-only (integrity, schema, event gaps).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
    json=True,
)
