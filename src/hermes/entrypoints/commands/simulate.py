"""``hermes simulate``: placeholder until historical replay ships."""

from __future__ import annotations

from typing import TextIO

from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]


def _run(argv: list[str], *, stream: TextIO) -> int:
    """Refuse loudly so composition scripts notice the missing command."""
    _ = argv
    stream.write(
        "ERROR: `hermes simulate` is not yet implemented. Tracked for a future integration PR.\n"
    )
    return 2


COMMAND = Command(
    name="simulate",
    summary="Replay historical scenarios (stub — implemented in a future PR).",
    run=_run,
    stub=True,
)
