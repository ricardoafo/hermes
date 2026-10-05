"""``hermes tax``: see :mod:`hermes.entrypoints.tax`."""

from __future__ import annotations

from hermes.entrypoints import tax as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="tax",
    summary="Estimated tax on hermes gains for a tax year (estimate, shadow).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
    json=True,
)
