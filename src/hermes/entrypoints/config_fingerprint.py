"""``hermes config fingerprint``: print prod's value fingerprints for the repo (#970).

Usage: ``hermes config fingerprint --env-file PATH|-``

Reads prod's env file and prints, as the YAML of
``config/prod_fingerprints.yaml``, the SHA-256 of each notifier, heartbeat
and error-report value it sets. Every tier but prod then refuses those
values (ADR-024 §4). The values themselves are never printed. The operator
runs it on prod's machine, where the file already lives, so no other tier
or tool ever reads prod's secrets.

Exit codes: 0 printed, 1 none of the fingerprinted variables is set,
2 usage, 3 env file could not be read.
"""

import argparse
import sys
from typing import Final, TextIO

from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.config_check import read_env_file
from hermes.infrastructure.config.prod_fingerprints import (
    FINGERPRINTED_ENV_VARS,
    render_fingerprints,
)

__all__ = ["USAGE", "main"]

USAGE: Final = "Usage: hermes config fingerprint --env-file PATH|-"


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
        stream: Output stream for the YAML.
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
        stream.write(f"{USAGE}\n\nPrints the SHA-256 of each of these values in prod's env file,\n")
        stream.write(f"never the value: {', '.join(FINGERPRINTED_ENV_VARS)}.\n")
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
    document = render_fingerprints(env)
    if not document:
        err.write("ERROR: the env file sets none of the fingerprinted variables\n")
        return 1
    stream.write(document)
    return 0
