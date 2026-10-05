"""``hermes version``: what is installed, which release is live, which schema.

Prints the package version, the release (the target name of the
``/opt/hermes/current`` symlink that ``scripts/deploy/install_release.sh``
maintains), the Python version and the ledger migration level. The ledger is
opened read-only and never written. Every field degrades to ``unknown`` or
``no ledger`` instead of failing.

Forms: ``hermes version [--db PATH] [--json]``; ``hermes --version`` and
``-V`` print the same plain output.

Exit codes: 0 always, 2 usage.

Reference: issue #539; openspec/changes/539-hermes-version.
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import TextIO

from hermes.adapters.ledger.migration_runner import pending_migrations
from hermes.entrypoints._release import DEFAULT_RELEASE_LINK, ReleaseInfo, read_release
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.serve import DEFAULT_DB_PATH

__all__ = [
    "DEFAULT_RELEASE_LINK",
    "ReleaseInfo",
    "VersionInfo",
    "collect_info",
    "main",
    "read_release",
]

_UNKNOWN = "unknown"
_NO_LEDGER = "no ledger"

_USAGE = (
    "Usage: hermes version [--db PATH] [--json]\n"
    "\n"
    "Print the package version, the live release, the Python version and the ledger\n"
    "migration level (read-only). Also: hermes --version, hermes -V.\n"
    "Exit codes: 0 always, 2 usage.\n"
)


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """What ``hermes version`` reports.

    Attributes:
        version: Installed package version, or ``unknown``.
        release: Name of the directory ``current`` points at, or ``unknown``.
        python: The running interpreter's version.
        ledger: Latest applied migration with the pending count, or
            ``no ledger``.
    """

    version: str
    release: str
    python: str
    ledger: str


def _package_version() -> str:
    try:
        return metadata.version("hermes")
    except metadata.PackageNotFoundError:
        return _UNKNOWN


def _ledger_level(db: Path) -> str:
    if not db.is_file():
        return _NO_LEDGER
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            applied = sorted(
                row[0] for row in conn.execute("SELECT filename FROM schema_migrations")
            )
            pending = len(pending_migrations(conn))
        finally:
            conn.close()
    except sqlite3.Error:
        return _NO_LEDGER
    if not applied:
        return _NO_LEDGER
    return f"{applied[-1]} ({pending} pending)"


def collect_info(*, db: Path, release_link: Path = DEFAULT_RELEASE_LINK) -> VersionInfo:
    """Gather the version report; never raises for a missing release or ledger.

    Args:
        db: Ledger path, opened read-only.
        release_link: The ``current`` symlink; injectable for tests.

    Returns:
        The report.
    """
    return VersionInfo(
        version=_package_version(),
        release=read_release(release_link).name,
        python=platform.python_version(),
        ledger=_ledger_level(db),
    )


def _parse(argv: list[str], stream: TextIO, err: TextIO) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(prog="hermes version", add_help=False)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        return parser.parse_args(argv)
    except SystemExit:
        err.write("ERROR: could not parse `hermes version` arguments.\n" + _USAGE)
        return None


def main(
    argv: list[str],
    *,
    stream: TextIO,
    release_link: Path = DEFAULT_RELEASE_LINK,
    err: TextIO | None = None,
) -> int:
    """Parse ``argv`` and print the version report.

    Args:
        argv: Arguments after the ``version`` token.
        stream: Output stream.
        release_link: The ``current`` symlink; injectable for tests.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        0, or 2 on a usage error.
    """
    parsed = _parse(argv, stream, resolve_err(err))
    if parsed is None:
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    info = collect_info(db=parsed.db, release_link=release_link)
    if parsed.json:
        stream.write(json.dumps(asdict(info)) + "\n")
        return 0
    stream.write(
        f"hermes {info.version}\n"
        f"release: {info.release}\n"
        f"python: {info.python}\n"
        f"ledger: {info.ledger}\n"
    )
    return 0
