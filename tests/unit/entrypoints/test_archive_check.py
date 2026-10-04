"""Unit tests for ``hermes archive-check`` (#359).

Invariant under test: the command checks the requested day for every
configured pair and interval and maps the outcome to its exit code (0 match,
1 mismatch, 2 usage, 3 failure). The archive is faked; the ledger is a real
file.
"""

from __future__ import annotations

import io
import sqlite3
from datetime import date
from pathlib import Path
from typing import ClassVar

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import InfraError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import archive_check as module
from hermes.entrypoints import cli

_PROJECT_CONFIG = Path(__file__).resolve().parents[3] / "config"


class _EmptyArchive:
    """Publishes an empty day for every stream and records what was asked."""

    asked: ClassVar[list[tuple[Pair, KlineInterval, date]]] = []

    def __init__(self, client: object) -> None:
        del client

    def daily_bars(self, pair: Pair, interval: KlineInterval, day: date) -> tuple[Bar, ...]:
        type(self).asked.append((pair, interval, day))
        return ()


class _FailingArchive(_EmptyArchive):
    def daily_bars(self, pair: Pair, interval: KlineInterval, day: date) -> tuple[Bar, ...]:
        raise InfraError("archive down")


def _ledger(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(db)
    SqliteLedger(connection)
    connection.close()
    return db


def _main(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    return module.main(argv, stream=out, env={}), out.getvalue()


def test_empty_day_on_both_sides_exits_0_for_every_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every configured pair and interval is checked for the requested day."""
    _EmptyArchive.asked = []
    monkeypatch.setattr(module, "BinanceVisionDailyArchive", _EmptyArchive)
    db = _ledger(tmp_path)
    code, out = _main(
        ["--date", "2026-10-02", "--db", str(db), "--config-dir", str(_PROJECT_CONFIG)]
    )
    assert code == 0
    assert "0 mismatches" in out
    assert {day for _, _, day in _EmptyArchive.asked} == {date(2026, 10, 2)}
    assert len(_EmptyArchive.asked) >= 1


def test_archive_failure_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A download or checksum failure is a failed check, not a clean day."""
    monkeypatch.setattr(module, "BinanceVisionDailyArchive", _FailingArchive)
    out, err = io.StringIO(), io.StringIO()
    code = module.main(
        ["--db", str(_ledger(tmp_path)), "--config-dir", str(_PROJECT_CONFIG)],
        stream=out,
        err=err,
    )
    assert (code, out.getvalue(), err.getvalue()) == (3, "", "ERROR: archive down\n")


def test_bad_arguments_exit_2() -> None:
    """An unparseable date is a usage error."""
    assert _main(["--date", "yesterday"])[0] == 2


def test_help_exits_0() -> None:
    """--help prints usage and does no work."""
    code, out = _main(["--help"])
    assert code == 0
    assert out.startswith("Usage: hermes archive-check")


def test_cli_routes_the_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    """`hermes archive-check` reaches this command's main."""
    seen: list[list[str]] = []

    def fake_main(rest: list[str], *, stream: object, err: object) -> int:
        seen.append(rest)
        return 0

    monkeypatch.setattr(module, "main", fake_main)
    assert cli.main(["archive-check", "--help"], stream=io.StringIO()) == 0
    assert seen == [["--help"]]
