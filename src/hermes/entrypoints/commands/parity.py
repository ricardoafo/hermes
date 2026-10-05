"""``hermes parity``: see :mod:`hermes.entrypoints.parity`."""

from __future__ import annotations

from hermes.entrypoints import parity as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="parity",
    summary="Backtest/live parity; `check` replays a day read-only and compares decisions.",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
