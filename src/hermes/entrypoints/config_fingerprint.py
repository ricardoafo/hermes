"""``hermes config fingerprint``: print ``prod_fingerprints.yaml`` for an env file (#970).

Usage: ``hermes config fingerprint --env-file PATH|-``

Run on prod's machine against prod's env file, after each rotation. It
prints the file's new text: the SHA-256 of each watched notifier, heartbeat
and error-report value that the env file sets, never a value. Commit the
output as ``config/prod_fingerprints.yaml``; every tier but prod then
refuses those values (see :mod:`hermes.infrastructure.config.fingerprints`).

``-`` reads the env file from stdin, so a root-only file can be piped in
by its owner. The file is parsed as systemd reads it, without expanding
``${VAR}``.

Exit codes: 0 printed, 2 usage, 3 env file could not be read or sets no
watched value.
"""

import argparse
import sys
from typing import Final, TextIO

from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.config_check import read_env_file
from hermes.infrastructure.config.fingerprints import WATCHED_VARS, render_prod_fingerprints

__all__ = ["USAGE", "main"]

USAGE: Final = "Usage: hermes config fingerprint --env-file PATH|-"
_EXIT_CODES: Final = (
    "Exit codes: 0 printed, 2 usage, 3 env file could not be read or sets no watched value."
)


def main(
    argv: list[str],
    *,
    stream: TextIO,
    err: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    """Run ``hermes config fingerprint``.

    Args:
        argv: Arguments after ``config fingerprint``.
        stream: Where the file's text goes.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.
        stdin: Where ``--env-file -`` reads from; defaults to ``sys.stdin``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = argparse.ArgumentParser(prog="hermes config fingerprint", add_help=False)
    parser.add_argument("--env-file")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write(f"ERROR: bad arguments. {USAGE}\n")
        return 2
    if parsed.help:
        stream.write(f"{USAGE}\n\nPrints config/prod_fingerprints.yaml for prod's env file:\n")
        stream.write(f"hashes of {', '.join(WATCHED_VARS)}; never a value.\n{_EXIT_CODES}\n")
        return 0
    if parsed.env_file is None:
        err.write(f"ERROR: --env-file is required. {USAGE}\n")
        return 2
    try:
        env = read_env_file(parsed.env_file, sys.stdin if stdin is None else stdin)
    except (OSError, UnicodeDecodeError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) else "not UTF-8"
        err.write(f"ERROR: env file could not be read: {reason}\n")
        return 3
    if not any(env.get(name, "").strip() for name in WATCHED_VARS):
        # An empty file would replace every fingerprint and refuse nothing.
        err.write(f"ERROR: the env file sets none of {', '.join(WATCHED_VARS)}\n")
        return 3
    stream.write(render_prod_fingerprints(env))
    return 0
