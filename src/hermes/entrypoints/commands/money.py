"""``hermes money``: the portfolio section; see :mod:`hermes.entrypoints.money`."""

from __future__ import annotations

import os

from hermes.entrypoints import money as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="money",
    summary="Portfolio total in EUR and the gross-to-net waterfall; `--range week|month|year`.",
    run=lambda argv, *, stream, demo, err=None: _impl.main(
        argv, stream=stream, demo_flag=demo, env=os.environ, err=err
    ),
    wants_demo=True,
    json=True,
)
