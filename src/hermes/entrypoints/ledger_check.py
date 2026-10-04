"""``hermes ledger-check`` — read-only consistency checks on the ledger (#559).

Prints one line per check. Safe while ``hermes.service`` runs: the ledger is
opened read-only.

Exit codes: 0 every check passed, 1 at least one failed, 2 usage, 3 the ledger
cannot be opened.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

from hermes.adapters.ledger.ledger_check import run_checks
from hermes.domain.errors import LedgerError
from hermes.entrypoints.serve import DEFAULT_DB_PATH

__all__ = ["main"]

_USAGE = (
    "Usage: hermes ledger-check [--db PATH]\n"
    "\n"
    "Read-only checks: SQLite integrity, migrations applied, lot remainders, orphans.\n"
    "Exit codes: 0 all pass, 1 a check failed, 2 usage, 3 ledger cannot be opened.\n"
)


def main(argv: list[str], *, stream: TextIO) -> int:
    """Parse ``argv`` and run the checks.

    Args:
        argv: Arguments after the ``ledger-check`` token.
        stream: Output stream.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="hermes ledger-check", add_help=False)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        stream.write("ERROR: could not parse `hermes ledger-check` arguments.\n" + _USAGE)
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    try:
        results = run_checks(parsed.db)
    except LedgerError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 3
    for result in results:
        stream.write(f"{'ok  ' if result.ok else 'FAIL'} {result.name}: {result.detail}\n")
    return 0 if all(r.ok for r in results) else 1
