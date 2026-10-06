"""Refuse a release that cannot read the events already in the ledger (#655).

A release reads each event type only at the schema version it knows.
Once a newer release has written, say, ``FillQualityRecorded`` v2 rows,
an older release that knows only v1 fails at start. Run this with the
target release's own interpreter, so the versions it imports are the
ones that release will run with:

    <release>/.venv/bin/python schema_guard.py <ledger.sqlite3>

Exit codes:
    0  the release knows every event type at or above the ledger's
       newest version, or there is no ledger or events table yet.
    3  the ledger holds a newer version of an event type than the
       release knows; each one is printed. Not 1, which is also what
       Python exits with on an uncaught exception, so the deploy scripts
       can tell a refusal from a guard that crashed.

An event type the release does not know at all is printed as a warning
and does not fail the check: older releases skip types they never read.
"""

from __future__ import annotations

import importlib
import pkgutil
import sqlite3
import sys
from pathlib import Path


def release_versions() -> dict[str, int]:
    """Return ``{event type: schema version}`` for every event this release defines."""
    package = importlib.import_module("hermes.domain.events")
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package.__name__}.{module.name}")
    base = importlib.import_module("hermes.domain.events.base").DomainEvent
    versions: dict[str, int] = {}
    pending = list(base.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        versions[cls.__qualname__] = int(cls.__dict__["__event_schema_version__"])
    return versions


def ledger_versions(db: Path) -> dict[str, int]:
    """Return the newest stored schema version of each event type in ``db``."""
    if not db.is_file():
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        has_events = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'events'"
        ).fetchone()
        if has_events is None:
            return {}
        rows = conn.execute(
            "SELECT event_type, MAX(event_schema_version) FROM events GROUP BY event_type"
        ).fetchall()
    finally:
        conn.close()
    return {str(name): int(version) for name, version in rows}


REFUSED = 3


def main(argv: list[str]) -> int:
    """Compare the ledger at ``argv[1]`` with this interpreter's release."""
    known = release_versions()
    newer = []
    for name, stored in sorted(ledger_versions(Path(argv[1])).items()):
        if name not in known:
            print(f"::warning::ledger has {name} events this release does not define")
        elif stored > known[name]:
            newer.append(f"{name} v{stored} (release knows v{known[name]})")
    if newer:
        print(
            "::error::the ledger holds newer events than this release can read: " + ", ".join(newer)
        )
        return REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
