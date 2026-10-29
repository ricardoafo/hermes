"""``hermes tax-pack``: see :mod:`hermes.entrypoints.tax_pack`."""

from hermes.entrypoints import tax_pack as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="tax-pack",
    summary="Refresh each tax year's filing pack for the gestor (submits nothing).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
