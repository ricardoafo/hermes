"""Output streams shared by every ``hermes`` command.

A command writes its result to ``stream`` (stdout) and its ERROR lines to
``err`` (stderr), so ``hermes <cmd> --json | jq`` never reads an error line as
JSON (#543). ``err`` is optional everywhere and resolves to the live
``sys.stderr`` when omitted.
"""

from __future__ import annotations

import sys
from typing import TextIO

__all__ = ["resolve_err"]


def resolve_err(err: TextIO | None) -> TextIO:
    """Return ``err``, or the current ``sys.stderr`` when it is ``None``.

    ``sys.stderr`` is read at call time, not import time, so a redirected or
    captured stderr is honoured.

    Args:
        err: The error stream a caller injected, if any.

    Returns:
        The stream ERROR lines are written to.
    """
    return err if err is not None else sys.stderr
