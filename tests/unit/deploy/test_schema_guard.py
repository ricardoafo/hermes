"""Invariants for ``scripts/deploy/schema_guard.py`` (#655).

The guard stops a deploy or rollback onto a release that cannot read the
ledger's events. These tests run it against scratch SQLite ledgers with
this checkout's own event classes; nothing is mocked.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

from hermes.domain.events.fill_quality_recorded import FillQualityRecorded

_ROOT = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    """Import the script module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "schema_guard", _ROOT / "scripts" / "deploy" / "schema_guard.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["schema_guard"] = module
    spec.loader.exec_module(module)
    return module


GUARD = _load()
_KNOWN = FillQualityRecorded.__event_schema_version__


def _ledger(path: Path, rows: list[tuple[str, int]]) -> Path:
    """Write a ledger whose events table holds ``(event_type, version)`` rows."""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE events (event_type TEXT, event_schema_version INTEGER)")
        conn.executemany("INSERT INTO events VALUES (?, ?)", rows)
    conn.close()
    return path


def test_release_versions_include_every_shipped_event() -> None:
    """Invariant: the guard sees the versions the release itself would write."""
    versions = GUARD.release_versions()

    assert versions["FillQualityRecorded"] == _KNOWN
    assert "TradeExecuted" in versions


def test_newer_event_version_fails_the_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invariant: a ledger written by a newer schema stops the older release."""
    db = _ledger(tmp_path / "l.sqlite3", [("FillQualityRecorded", _KNOWN + 1)])

    assert GUARD.main(["guard", str(db)]) == 1
    assert f"FillQualityRecorded v{_KNOWN + 1} (release knows v{_KNOWN})" in capsys.readouterr().out


def test_same_or_older_versions_pass(tmp_path: Path) -> None:
    """Invariant: rows the release can read never block it."""
    db = _ledger(tmp_path / "l.sqlite3", [("FillQualityRecorded", _KNOWN), ("TradeExecuted", 1)])

    assert GUARD.main(["guard", str(db)]) == 0


def test_unknown_event_type_warns_without_failing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invariant: a type the release never reads is reported, not refused."""
    db = _ledger(tmp_path / "l.sqlite3", [("FutureEvent", 1)])

    assert GUARD.main(["guard", str(db)]) == 0
    assert "::warning::ledger has FutureEvent events" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["missing", "no-events-table"])
def test_ledger_without_events_passes(tmp_path: Path, kind: str) -> None:
    """Invariant: a first deploy, or a ledger before its first event, has nothing to check."""
    db = tmp_path / "l.sqlite3"
    if kind == "no-events-table":
        sqlite3.connect(db).close()

    assert GUARD.main(["guard", str(db)]) == 0
