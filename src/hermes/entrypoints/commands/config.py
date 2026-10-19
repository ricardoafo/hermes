"""``hermes config``: config tools; see :mod:`hermes.entrypoints.config_check`."""

from typing import Any, TextIO

from hermes.entrypoints import config_check as _check
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

_USAGE = "Usage: hermes config check [--config-dir DIR] [--env-file PATH|-] [--json]"


def _run(argv: list[str], *, stream: Any, err: TextIO | None = None) -> int:
    """Dispatch ``hermes config <subcommand>``; only ``check`` exists.

    Returns:
        The subcommand's exit code, 0 for ``-h``, 2 for none or an unknown one.
    """
    if argv and argv[0] in {"-h", "--help"}:
        stream.write(f"{_USAGE}\n")
        return 0
    if argv and argv[0] == "check":
        return _check.main(argv[1:], stream=stream, err=err)
    resolve_err(err).write(f"ERROR: expected a subcommand. {_USAGE}\n")
    return 2


COMMAND = Command(
    name="config",
    summary="Check the config and secrets without starting anything (hermes config check).",
    run=_run,
    json=True,
)
