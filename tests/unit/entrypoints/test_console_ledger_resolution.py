"""Which read model console commands get once a ledger exists (#546 D-8, D-9).

Invariants:

- without a demo and with a ledger file, the console reads the ledger (no DEMO
  DATA tag); a demo still wins; no file means the "use --demo" error;
- ``hermes positions --db PATH`` reads that file; a missing ``--db`` value is a
  usage error and an unreadable ledger exits 3;
- ``hermes tui`` stays demo-only until the home slice, even with a ledger.
"""

from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path

import pytest

from hermes.entrypoints.activity import run_activity
from hermes.entrypoints.commands.positions import run_positions
from hermes.entrypoints.commands.tui import run_tui
from hermes.entrypoints.console import (
    DEMO_ENV_VAR,
    LiveReadModelUnavailableError,
    demo_read_model,
    resolve_read_model,
)
from tests.unit.adapters.console._ledger_builder import (
    ETH,
    T0,
    Held,
    LedgerBuilder,
    one_btc_position,
)


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """Return a ledger with one BTC position."""
    path = tmp_path / "hermes.sqlite3"
    one_btc_position(path)
    return path


def test_ledger_is_read_when_no_demo_is_selected(ledger: Path) -> None:
    """No flag, no variable, a ledger file: the live model, untagged (A-5.1)."""
    model = resolve_read_model(demo_flag=None, env={}, db_path=ledger)
    assert (model.demo_scenario, [row.coin for row in model.positions()]) == (None, ["BTC"])


def test_demo_wins_over_the_ledger(ledger: Path) -> None:
    """A selected scenario is shown even when a ledger exists."""
    model = resolve_read_model(demo_flag=None, env={DEMO_ENV_VAR: "empty"}, db_path=ledger)
    assert model.demo_scenario == "empty"


def test_missing_ledger_names_the_path_and_demo(tmp_path: Path) -> None:
    """No file at the path: the error says where it looked and points at --demo."""
    path = tmp_path / "absent.sqlite3"
    with pytest.raises(LiveReadModelUnavailableError, match=r"absent\.sqlite3.*--demo"):
        resolve_read_model(demo_flag=None, env={}, db_path=path)


def test_demo_read_model_is_none_without_a_demo() -> None:
    """Commands with their own ledger readers learn there is no demo, not a live model."""
    blank = demo_read_model(demo_flag=None, env={DEMO_ENV_VAR: " "})
    named = demo_read_model(demo_flag="empty", env={})
    assert (blank, None if named is None else named.demo_scenario) == (None, "empty")


def test_positions_reads_the_db_option(ledger: Path) -> None:
    """``positions --db PATH`` lists the ledger's position without a DEMO tag."""
    stream = io.StringIO()
    rc = run_positions(["--db", str(ledger)], stream, env={})
    out = stream.getvalue()
    assert (rc, "BTC" in out, "DEMO DATA" in out) == (0, True, False)


def test_positions_db_without_a_path_is_a_usage_error() -> None:
    """``--db`` with no value exits 2 with the usage line."""
    stream = io.StringIO()
    rc = run_positions(["--db"], stream, env={})
    assert (rc, "--db PATH" in stream.getvalue()) == (2, True)


def test_positions_db_pointing_nowhere_is_the_empty_state(tmp_path: Path) -> None:
    """No ledger at the path: no position can exist, exit 0 with the empty state."""
    stream = io.StringIO()
    rc = run_positions([f"--db={tmp_path / 'absent.sqlite3'}"], stream, env={})
    assert (rc, stream.getvalue().startswith("No open shadow positions.")) == (0, True)


def test_positions_unreadable_ledger_exits_3(tmp_path: Path) -> None:
    """A held coin with no entry signal is a ledger inconsistency: exit 3, not a guess."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    b.mark(T0, (Held(ETH, "1", "2500", "2550", T0),), pair=ETH)
    b.close()
    stream = io.StringIO()
    rc = run_positions(["--db", str(path)], stream, env={})
    assert (rc, "could not read the ledger" in stream.getvalue()) == (3, True)


def test_tui_stays_demo_only_with_a_ledger(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The home screen needs every view, so a ledger alone still asks for --demo (D-8)."""
    monkeypatch.chdir(ledger.parent)
    stream = io.StringIO()
    rc = run_tui([], stream, env={}, runner=lambda model: None)
    assert (rc, "use --demo <scenario>" in stream.getvalue()) == (2, True)


def test_activity_keeps_its_own_ledger_reader(
    ledger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the default ledger present, ``activity`` reads it itself, not via the live model."""
    monkeypatch.chdir(ledger.parent)
    stream = io.StringIO()
    rc = run_activity(["--all", "--days", "30"], stream, env={}, now=T0 + timedelta(hours=9))
    assert (rc, "4h check" in stream.getvalue()) == (0, True)
