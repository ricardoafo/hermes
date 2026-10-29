"""``hermes tax-filing``: see :mod:`hermes.entrypoints.tax_filing`."""

from hermes.entrypoints import tax_filing as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="tax-filing",
    summary="Figures for Modelo 100 and 721 for one tax year (an estimate; submits nothing).",
    run=lambda argv, *, stream, err=None: _impl.main(argv, stream=stream, err=err),
)
