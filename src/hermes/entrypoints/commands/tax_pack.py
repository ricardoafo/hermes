"""``hermes tax-pack``: see :mod:`hermes.entrypoints.tax_pack`."""

import os
from typing import TextIO

from hermes.entrypoints import tax_pack as _impl
from hermes.entrypoints.commands import Command
from hermes.entrypoints.tax_pack_fetch import fetch_main

__all__ = ["COMMAND"]


def _run(argv: list[str], *, stream: TextIO, err: TextIO | None = None) -> int:
    """Run ``tax-pack fetch`` when it is named first, else the refresh."""
    if argv[:1] == ["fetch"]:
        return fetch_main(
            argv[1:], stream=stream, default_out=_impl.out_root(None, os.environ), err=err
        )
    return _impl.main(argv, stream=stream, err=err)


COMMAND = Command(
    name="tax-pack",
    summary="Refresh each tax year's filing pack for the gestor (submits nothing).",
    run=_run,
)
