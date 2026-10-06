"""Read the ``AnomalyDetected`` rows a test run left in a ledger file (#136)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def recorded_anomalies(db: Path) -> list[dict[str, object]]:
    """Return the decoded payload of every ``AnomalyDetected`` event, in append order."""
    connection = sqlite3.connect(db)
    try:
        rows = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = 'AnomalyDetected' ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return [json.loads(payload) for (payload,) in rows]
