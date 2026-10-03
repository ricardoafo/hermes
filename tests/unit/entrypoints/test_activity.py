"""Tests for ``hermes activity`` end to end (#374).

Invariants protected here:

- on every demo scenario the feed matches its reviewed snapshot at 80 and
  120 columns, carries the DEMO DATA tag, folds repeated stop raises and
  shows no bar check without ``--all``;
- without a demo the feed comes from the ledger, opened read-only: an entry
  signal is an "opened" row at its price, an exit a "closed" row with no money;
- a missing ledger exits 3 and creates nothing; bad arguments exit 2;
- the top-level CLI passes a leading ``--demo`` through to the command.
"""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from hermes.adapters.console import available_scenarios
from hermes.adapters.console.ledger_activity import LedgerActivityReader
from hermes.domain.strategy.signal import SignalKind
from hermes.entrypoints import cli
from hermes.entrypoints.activity import run_activity
from tests.support.status_ledger import BTC, ETH, LAST_4H, NOW, signal, write_ledger

_SCENARIOS = available_scenarios()


def _run(args: list[str], **kw: object) -> tuple[int, str]:
    out = io.StringIO()
    code = run_activity(args, out, **kw)  # type: ignore[arg-type]
    return code, out.getvalue()


def _lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines()]


@pytest.mark.parametrize("width", [80, 120])
@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_demo_feed_matches_snapshot(
    scenario: str, width: int, monkeypatch: pytest.MonkeyPatch, snapshot: SnapshotAssertion
) -> None:
    """The reviewed feed per scenario and width."""
    monkeypatch.setenv("COLUMNS", str(width))

    code, out = _run([], demo_flag=scenario)

    assert (code, _lines(out)) == (0, snapshot)


def test_demo_feed_is_tagged_and_folds_stop_raises() -> None:
    """DEMO DATA leads the feed; six stop raises read as one row, levels in EUR."""
    _, out = _run([], demo_flag="ok-two-positions")

    assert "DEMO DATA" in out.splitlines()[0]
    assert "SOL stop raised \u00d76, \u20ac119.1\u2192\u20ac121.7" in out


def test_quiet_bar_checks_only_show_with_all() -> None:
    """Bar checks go to the logs, not the feed, unless --all asks for them."""
    _, default = _run([], demo_flag="ok-two-positions")
    _, everything = _run(["--all"], demo_flag="ok-two-positions")

    assert ("4h check, no signal" in default, "4h check, no signal" in everything) == (False, True)


def test_demo_json_has_days_and_net_for_the_operator() -> None:
    """--json mirrors the feed; the CLI is an operator surface, so net is present."""
    _, out = _run(["--json"], demo_flag="ok-two-positions")
    data = json.loads(out)
    closed = next(i for d in data["days"] for i in d["items"] if i["kind"] == "closed")

    assert (data["demo_scenario"], closed["net_eur"]) == ("ok-two-positions", "3.18")


def test_ledger_feed_shows_opened_and_closed_from_signals(tmp_path: Path) -> None:
    """Entry and exit signals are the shadow position's open and close."""
    path = write_ledger(
        tmp_path / "hermes.sqlite3",
        signals=(
            signal(ETH, SignalKind.ENTRY, LAST_4H - timedelta(hours=8, milliseconds=1)),
            signal(ETH, SignalKind.EXIT, LAST_4H - timedelta(milliseconds=1)),
        ),
    )

    code, out = _run(["--db", str(path), "--json", "--utc"], now=NOW)
    items = [i for d in json.loads(out)["days"] for i in d["items"]]

    assert code == 0
    assert items == [
        {"at": "2026-10-03T16:00:00+00:00", "kind": "closed", "coin": "ETH"},
        {"at": "2026-10-03T08:00:00+00:00", "kind": "opened", "coin": "ETH"},
    ]


def test_ledger_feed_with_all_includes_bar_checks(tmp_path: Path) -> None:
    """--all adds one quiet row per processed bar."""
    path = write_ledger(
        tmp_path / "hermes.sqlite3", signals=(signal(BTC, SignalKind.ENTRY, LAST_4H),)
    )

    _, out = _run(["--db", str(path), "--json", "--all", "--utc"], now=NOW)
    kinds = sorted(i["kind"] for d in json.loads(out)["days"] for i in d["items"])

    assert kinds == ["bar"] * 6 + ["opened", "signal"]


def test_ledger_reader_cannot_write(tmp_path: Path) -> None:
    """mode=ro: a stray write through the reader's connection raises."""
    path = write_ledger(tmp_path / "hermes.sqlite3")

    with LedgerActivityReader.open(path) as reader, pytest.raises(sqlite3.OperationalError):
        reader._conn.execute("DELETE FROM events")


def test_missing_ledger_exits_three_and_creates_nothing(tmp_path: Path) -> None:
    """A wrong --db is an error, never a new empty ledger."""
    path = tmp_path / "absent.sqlite3"

    code, out = _run(["--db", str(path)], now=NOW)

    assert (code, path.exists()) == (3, False)
    assert out.startswith("ERROR: no ledger at")


@pytest.mark.parametrize("args", [["--nope"], ["--days", "0"]])
def test_bad_arguments_exit_two(args: list[str]) -> None:
    """Unknown flags and an empty window are usage errors."""
    code, out = _run(args, demo_flag="ok-two-positions")

    assert code == 2
    assert "Usage: hermes [--demo SCENARIO] activity" in out


def test_unknown_scenario_exits_two() -> None:
    """A typo in the scenario name is a usage error, not a crash."""
    code, out = _run([], demo_flag="no-such-scenario")

    assert code == 2
    assert out.startswith("ERROR:")


def test_cli_passes_a_leading_demo_flag_to_activity() -> None:
    """`hermes --demo SCENARIO activity` reaches the command with its scenario."""
    out = io.StringIO()

    code = cli.main(["--demo", "ok-two-positions", "activity"], stream=out)

    assert code == 0
    assert "DEMO DATA" in out.getvalue()
