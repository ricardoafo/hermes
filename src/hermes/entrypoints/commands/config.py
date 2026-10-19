"""``hermes config``: config tools; see :mod:`hermes.entrypoints.config_check`."""

from typing import Any, TextIO

from hermes.entrypoints import config_check as _check
from hermes.entrypoints import config_fingerprint as _fingerprint
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND", "USAGE"]

USAGE = f"{_check.USAGE}\n       {_fingerprint.USAGE.removeprefix('Usage: ')}"


def _run(argv: list[str], *, stream: Any, err: TextIO | None = None) -> int:
    """Dispatch ``hermes config <subcommand>``: ``check`` or ``fingerprint``.

    Returns:
        The subcommand's exit code, 0 for ``-h``, 2 for none or an unknown one.
    """
    if argv and argv[0] in {"-h", "--help"}:
        stream.write(f"{USAGE}\n")
        return 0
    if argv and argv[0] == "check":
        return _check.main(argv[1:], stream=stream, err=err)
    if argv and argv[0] == "fingerprint":
        return _fingerprint.main(argv[1:], stream=stream, err=err)
    resolve_err(err).write(f"ERROR: expected a subcommand. {USAGE}\n")
    return 2


COMMAND = Command(
    name="config",
    summary="Check the config and secrets without starting anything (hermes config check).",
    run=_run,
    json=True,
)
