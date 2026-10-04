"""``hermes ledger`` — ledger tools; today only ``verify`` (#538).

Usage: ``hermes ledger verify [--db PATH] [--json]``

Opens the ledger read-only, runs ``PRAGMA integrity_check``, and reports the
migration level, the event count and any gaps in the event id sequence. Safe
while ``hermes.service`` runs. Prints ``OK — N events, 0 gaps, schema vX`` or
lists each finding.

Exit codes: 0 OK, 1 findings, 2 usage, 3 ledger missing or unreadable.

Reference: openspec/changes/538-ledger-verify/change.md.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Final, TextIO

from hermes.adapters.ledger.integrity import LedgerReport, check_ledger, open_read_only
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.serve import DEFAULT_DB_PATH

__all__ = ["main"]

_USAGE: Final = (
    "Usage: hermes ledger verify [--db PATH] [--json]\n"
    "\n"
    "Read-only check of the ledger: SQLite integrity, migration level, event count,\n"
    "gaps in the event id sequence.\n"
    "Exit codes: 0 OK, 1 findings, 2 usage, 3 ledger missing or unreadable.\n"
)


def main(argv: list[str], *, stream: TextIO, err: TextIO | None = None) -> int:
    """Dispatch ``hermes ledger <subcommand>``.

    Args:
        argv: Arguments after the ``ledger`` token.
        stream: Output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    if argv and argv[0] in ("-h", "--help"):
        stream.write(_USAGE)
        return 0
    if not argv or argv[0] != "verify":
        err.write("ERROR: expected the subcommand `verify`.\n" + _USAGE)
        return 2
    return _verify(argv[1:], stream, err)


def _verify(argv: list[str], stream: TextIO, err: TextIO) -> int:
    parser = argparse.ArgumentParser(prog="hermes ledger verify", add_help=False)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write("ERROR: bad arguments.\n" + _USAGE)
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    db: Path = parsed.db
    if not db.exists():
        err.write(f"ERROR: no ledger at {db}.\n")
        return 3
    try:
        conn = open_read_only(db)
        try:
            report = check_ledger(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        err.write(f"ERROR: could not read the ledger: {exc}\n")
        return 3
    if parsed.json:
        stream.write(json.dumps(_to_json(report), indent=2) + "\n")
    else:
        _write_text(report, stream)
    return 0 if report.ok else 1


def _to_json(report: LedgerReport) -> dict[str, object]:
    return {
        "ok": report.ok,
        "integrity": list(report.integrity),
        "schema_level": report.schema_level,
        "pending_migrations": list(report.pending),
        "event_count": report.event_count,
        "gaps": [{"first": gap.first, "last": gap.last} for gap in report.gaps],
        "unknown_migrations": list(report.unknown_migrations),
        "lot_problems": list(report.lot_problems),
        "orphans": list(report.orphans),
        "findings": list(report.findings),
    }


def _write_text(report: LedgerReport, stream: TextIO) -> None:
    if report.ok:
        stream.write(f"OK — {report.event_count} events, 0 gaps, schema v{report.schema_level}\n")
        return
    stream.write(
        f"FINDINGS — {report.event_count} events, {len(report.gaps)} gaps, "
        f"schema v{report.schema_level}\n"
    )
    for finding in report.findings:
        stream.write(f"- {finding}\n")
