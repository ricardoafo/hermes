"""Record an ``AnomalyDetected`` raised by a CLI job in the ledger (#136).

The alert jobs (``archive-check``, ``reconcile --notify``, ``parity check
--alert``) run as their own processes, outside serve's event bus. Each alert
they raise is appended to the ledger ``events`` table through
:meth:`SqliteLedger.append_event`, the same path serve uses, so ``hermes
anomalies`` lists every anomaly from one place.

Recording is best effort: a missing or unwritable ledger prints one WARNING
line and never changes the alert, its delivery or the exit code. A missing
ledger is never created. Nothing here pauses or gates trading.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TextIO

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.events.anomaly_detected import AnomalyDetected

__all__ = ["record_anomaly"]


def record_anomaly(db: Path, event: AnomalyDetected, err: TextIO) -> bool:
    """Append ``event`` to the ``events`` table of the ledger at ``db``.

    Args:
        db: The ledger file; it must already exist.
        event: The anomaly to record.
        err: Stream for the WARNING line when the record fails.

    Returns:
        True when the event was written, False when it was not (warned).
    """
    if not db.is_file():
        err.write(f"WARNING: no ledger at {db}; the anomaly was not recorded\n")
        return False
    try:
        connection = sqlite3.connect(db)
    except sqlite3.Error as exc:
        err.write(f"WARNING: the anomaly could not be recorded: {exc}\n")
        return False
    try:
        SqliteLedger(connection).append_event(event)
    except (LedgerError, sqlite3.Error) as exc:
        err.write(f"WARNING: the anomaly could not be recorded: {exc}\n")
        return False
    finally:
        connection.close()
    return True
