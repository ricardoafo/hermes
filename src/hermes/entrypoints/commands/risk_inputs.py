"""``hermes risk-inputs``: see :mod:`hermes.entrypoints.risk_inputs`."""

from hermes.entrypoints import risk_inputs as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="risk-inputs",
    summary="Show the risk inputs shadow records and whether RO-02 or the recovery kill fires.",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
