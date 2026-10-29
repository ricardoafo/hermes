"""``hermes runners``: see :mod:`hermes.entrypoints.runners`."""

from typing import Any, TextIO

from hermes.entrypoints import runners as _impl
from hermes.entrypoints.commands import Command
from hermes.entrypoints.commands.health import health_env

__all__ = ["COMMAND"]


def _run(argv: list[str], *, stream: Any, err: TextIO | None = None) -> int:
    _ = err
    return _impl.main(argv, stream=stream, env=health_env())


COMMAND = Command(
    name="runners",
    summary="`hermes runners [--json]` shows the health of the self-hosted CI runners.",
    run=_run,
    json=True,
)
