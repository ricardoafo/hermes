"""``hermes ledger-check``: see :mod:`hermes.entrypoints.ledger_check`."""

from __future__ import annotations

from hermes.entrypoints import ledger_check as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="ledger-check",
    summary="Read-only consistency checks on the ledger (integrity, migrations, lots, orphans).",
    run=lambda argv, *, stream: _impl.main(argv, stream=stream),
)
