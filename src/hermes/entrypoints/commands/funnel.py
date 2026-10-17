"""``hermes funnel``: see :mod:`hermes.entrypoints.funnel`."""

from hermes.entrypoints import funnel as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="funnel",
    summary="Evaluate the ADR-021 §5 backtest funnel against a signed pre-registration.",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
