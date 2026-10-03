"""``hermes preview``: see :mod:`hermes.entrypoints.preview`."""

from __future__ import annotations

from hermes.entrypoints import preview as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="preview",
    summary="Render + optionally send notification templates.",
    run=lambda argv, *, stream: _impl.main(argv, stream=stream),
)
