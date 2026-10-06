#!/usr/bin/env python3
"""Reject transaction control and badly named files in ledger migrations (#483).

The runner wraps every migration in one transaction (#447). A migration that
opens or ends its own transaction would commit part of itself outside that
wrapper, so a failure could leave the ledger half-migrated. New migrations are
named by UTC timestamp so parallel PRs cannot take the same number; the
legacy numbered files 0001-0006 stay valid.

Checks every ``*.sql`` under ``src/hermes/adapters/ledger/migrations/`` (or the
paths given):

- file name: ``YYYYMMDDTHHMM_<slug>.sql``, or one of the legacy ``000N_<slug>.sql``
  names with N from 1 to 6;
- no statement that is ``BEGIN``, ``COMMIT``, ``END [TRANSACTION]``,
  ``ROLLBACK``, ``SAVEPOINT`` or ``RELEASE``. Trigger bodies
  (``CREATE TRIGGER ... BEGIN ... END;``), comments and string literals are
  ignored.

Usage:
    python scripts/dev/migration-sql-check.py [FILE ...]

Exit codes:
    0 - every file passes
    1 - at least one violation (printed one per line)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATIONS_DIR = Path("src/hermes/adapters/ledger/migrations")

_TIMESTAMP_NAME = re.compile(r"^\d{8}T\d{4}_[a-z0-9_]+\.sql$")
_LEGACY_NAME = re.compile(r"^000[1-6]_[a-z0-9_]+\.sql$")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:[^']|'')*'")
_TRIGGER = re.compile(
    r"\bCREATE\s+(?:TEMP\w*\s+)?TRIGGER\b.*?\bBEGIN\b.*?\bEND\s*;", re.IGNORECASE | re.DOTALL
)
_TRANSACTION = re.compile(
    r"^(?:BEGIN(?:\s+(?:DEFERRED|IMMEDIATE|EXCLUSIVE))?(?:\s+TRANSACTION)?"
    r"|COMMIT(?:\s+TRANSACTION)?"
    r"|END(?:\s+TRANSACTION)?"
    r"|ROLLBACK\b.*"
    r"|SAVEPOINT\b.*"
    r"|RELEASE\b.*)$",
    re.IGNORECASE | re.DOTALL,
)


def name_violation(path: Path) -> str | None:
    """Return why ``path``'s name is not allowed, or ``None`` when it is."""
    if _TIMESTAMP_NAME.match(path.name) or _LEGACY_NAME.match(path.name):
        return None
    return (
        f"{path}: name must be YYYYMMDDTHHMM_<slug>.sql (UTC timestamp, lowercase slug); "
        "numbered names stop at 0006"
    )


def transaction_statements(sql: str) -> list[str]:
    """Return every transaction-control statement in ``sql``, outside triggers and comments."""
    text = _BLOCK_COMMENT.sub(" ", sql)
    text = _LINE_COMMENT.sub(" ", text)
    text = _STRING.sub("''", text)
    text = _TRIGGER.sub(" ", text)
    statements = (" ".join(part.split()) for part in text.split(";"))
    return [s for s in statements if s and _TRANSACTION.match(s)]


def check(paths: list[Path]) -> list[str]:
    """Return one message per violation across ``paths``."""
    problems: list[str] = []
    for path in paths:
        bad_name = name_violation(path)
        if bad_name:
            problems.append(bad_name)
        problems.extend(
            f"{path}: '{statement}' controls the transaction; the runner wraps each "
            "migration in one transaction, so remove it"
            for statement in transaction_statements(path.read_text(encoding="utf-8"))
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    """Check the given migration files, or every shipped one."""
    args = sys.argv[1:] if argv is None else argv
    paths = [Path(a) for a in args] if args else sorted(MIGRATIONS_DIR.glob("*.sql"))
    problems = check([p for p in paths if p.suffix == ".sql"])
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
