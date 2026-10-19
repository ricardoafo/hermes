"""``hermes anomalies`` — list the anomalies recorded in the ledger, read-only (#136).

Usage: ``hermes anomalies [--since 7d|YYYY-MM-DD] [--db PATH] [--json]``

Every ``AnomalyDetected`` raised by serve or by the alert jobs
(``archive-check``, ``reconcile --notify``, ``parity check --alert``) is
appended to the ledger ``events`` table. This command lists them, newest
first, one line each: UTC time, severity, source, reason and the tail of the
correlation id. ``--since`` takes a relative age (``12h``, ``7d``, ``2w``) or
a UTC date or datetime; the default is the last 7 days.

The ledger is opened with SQLite's ``mode=ro`` and never migrated, so the
command is safe next to ``hermes serve``. Listing an anomaly changes nothing:
anomalies are recorded and notified only, and nothing pauses trading on them.

Exit codes: 0 success (also when there is none), 2 usage, 3 no ledger or unreadable.
"""

import argparse
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, TextIO

from hermes.adapters.ledger.anomaly_reader import SqliteAnomalyReader
from hermes.domain.errors import LedgerError
from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.serve import DEFAULT_DB_PATH
from hermes.infrastructure.time import now_utc

__all__ = ["main"]

_USAGE: Final = (
    "Usage: hermes anomalies [--since 7d|YYYY-MM-DD] [--db PATH] [--json]\n"
    "\n"
    "Lists the anomalies recorded in the ledger, newest first. Read-only.\n"
    "--since takes 12h, 7d, 2w or a UTC date/datetime (default 7d).\n"
    "Exit codes: 0 success, 2 usage, 3 no ledger or unreadable.\n"
)
_RELATIVE: Final = re.compile(r"^(\d+)([hdw])$")
_UNIT_HOURS: Final = {"h": 1, "d": 24, "w": 24 * 7}
_DEFAULT_SINCE: Final = "7d"
_TAIL: Final = 6


def main(
    argv: list[str],
    *,
    stream: TextIO,
    err: TextIO | None = None,
    now: datetime | None = None,
) -> int:
    """Run ``hermes anomalies``.

    Args:
        argv: Arguments after ``anomalies``.
        stream: Output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.
        now: Current UTC time for a relative ``--since``; defaults to the wall clock.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = argparse.ArgumentParser(prog="hermes anomalies", add_help=False)
    parser.add_argument("--since", default=_DEFAULT_SINCE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write("ERROR: could not parse `hermes anomalies` arguments.\n" + _USAGE)
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    since = parse_since(parsed.since, now if now is not None else now_utc())
    if since is None:
        err.write(f"ERROR: --since {parsed.since!r} is not 12h, 7d, 2w or a date.\n" + _USAGE)
        return 2
    try:
        with SqliteAnomalyReader.open(parsed.db) as reader:
            anomalies = reader.since(since)
    except FileNotFoundError:
        err.write(f"ERROR: no ledger at {parsed.db}\n")
        return 3
    except LedgerError as exc:
        err.write(f"ERROR: could not read the ledger: {exc}\n")
        return 3
    if parsed.json:
        document = {"since": since.isoformat(), "anomalies": [_to_dict(a) for a in anomalies]}
        stream.write(json.dumps(document, indent=2) + "\n")
    elif not anomalies:
        stream.write(f"No anomalies recorded since {_utc(since)}.\n")
    else:
        stream.writelines(_line(a) + "\n" for a in anomalies)
    return 0


def parse_since(value: str, now: datetime) -> datetime | None:
    """Return the UTC lower bound ``value`` names, or ``None`` when it is not valid.

    Args:
        value: A relative age (``12h``, ``7d``, ``2w``) or an ISO date or
            datetime; a naive one is read as UTC.
        now: Current UTC time, the base of a relative age.

    Returns:
        The timezone-aware lower bound, or ``None``.
    """
    match = _RELATIVE.match(value)
    if match is not None:
        return now - timedelta(hours=int(match.group(1)) * _UNIT_HOURS[match.group(2)])
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _utc(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def _line(anomaly: AnomalyDetected) -> str:
    return (
        f"{_utc(anomaly.occurred_at)}  {anomaly.severity.value:<8}  {anomaly.source}  "
        f"{anomaly.reason}  …{anomaly.correlation_id[-_TAIL:]}"
    )


def _to_dict(anomaly: AnomalyDetected) -> dict[str, str]:
    return {
        "occurred_at": anomaly.occurred_at.isoformat(),
        "severity": anomaly.severity.value,
        "source": anomaly.source,
        "reason": anomaly.reason,
        "correlation_id": anomaly.correlation_id,
    }
