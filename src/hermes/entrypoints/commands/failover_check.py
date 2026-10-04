"""``hermes failover-check``: see :mod:`hermes.entrypoints.failover_check`."""

from __future__ import annotations

from hermes.entrypoints import failover_check as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="failover-check",
    summary="Check Kraken can stand in for Binance: compare recent closes (read-only).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
