"""Tests for ``hermes status`` end to end over a real ledger file.

Invariants protected here:

- exit code 0 only when the verdict is all good; 1 when late or down; 3 when
  the ledger is missing (and nothing is created) or the config is broken;
- the human view leads with the verdict and shows UTC and Madrid times;
- ``--json`` carries the same facts, and neither output has any P&L field;
- the systemd probe maps ActiveState/SubState to the verdict's unit states.

The rendered views are pinned with syrupy snapshots (plain text and a
terminal render) so a layout change shows up in review.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from syrupy.assertion import SnapshotAssertion

from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.application.status import UnitState, build_report
from hermes.domain.strategy.signal import SignalKind
from hermes.entrypoints import cli
from hermes.entrypoints import status as status_module
from hermes.entrypoints.status_view import render_status
from tests.support.status_ledger import (
    ETH,
    INTERVALS,
    LAST_4H,
    NOW,
    PAIRS,
    signal,
    write_ledger,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG = _PROJECT_ROOT / "config"
_PNL_WORDS = re.compile(r"p&l|pnl|profit|loss|equity|sharpe|drawdown|win rate|return", re.I)


def _run(
    args: list[str], *, now: Any = NOW, unit: UnitState = UnitState.RUNNING
) -> tuple[int, str]:
    """Run ``hermes status`` with a fixed clock and unit state; return code and output."""
    out = io.StringIO()
    code = status_module.main(
        ["--config-dir", str(_CONFIG), *args],
        stream=out,
        now=now,
        unit_state=lambda: unit,
        bot_state=lambda: UnitState.UNKNOWN,
    )
    return code, out.getvalue()


def _run_split(args: list[str]) -> tuple[int, str, str]:
    """Like :func:`_run` but also return what went to the error stream."""
    out, err = io.StringIO(), io.StringIO()
    code = status_module.main(
        ["--config-dir", str(_CONFIG), *args],
        stream=out,
        err=err,
        now=NOW,
        unit_state=lambda: UnitState.RUNNING,
    )
    return code, out.getvalue(), err.getvalue()


def _rstrip_lines(text: str) -> str:
    """Drop Rich's right padding so the snapshot has no trailing whitespace."""
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def _ledger(tmp_path: Path, **kwargs: Any) -> Path:
    """Write the fixture ledger with one open ETH entry."""
    signals = (signal(ETH, SignalKind.ENTRY, LAST_4H - timedelta(hours=4)),)
    return write_ledger(tmp_path / "hermes.sqlite3", signals=signals, **kwargs)


def test_current_ledger_exits_zero_and_leads_with_all_good(tmp_path: Path) -> None:
    """Every stream current and the unit running: exit 0, verdict first."""
    code, out = _run(["--db", str(_ledger(tmp_path))])

    assert code == 0
    assert out.splitlines()[2] == "● All good"


def test_overdue_ledger_exits_one_and_says_late(tmp_path: Path) -> None:
    """Past the 20:00 bar's due time plus grace, the check reads late and exits 1."""
    late_now = LAST_4H + timedelta(hours=4, minutes=30)
    code, out = _run(["--db", str(_ledger(tmp_path))], now=late_now)

    assert code == 1
    assert "▲ The 22:00 check is 29 minutes late" in out


def test_empty_ledger_exits_one_and_says_never_run(tmp_path: Path) -> None:
    """A migrated ledger with no bars: every check has never run."""
    code, out = _run(["--db", str(_ledger(tmp_path, last_4h=None, last_1d=None))])

    assert code == 1
    assert "A check has never run" in out


def test_failed_unit_exits_one_and_says_down(tmp_path: Path) -> None:
    """A failed unit is down even when the ledger looks current."""
    code, out = _run(["--db", str(_ledger(tmp_path))], unit=UnitState.FAILED)

    assert code == 1
    assert "■ hermes is down" in out


def test_missing_ledger_exits_three_and_creates_nothing(tmp_path: Path) -> None:
    """A wrong --db reports down with the path and leaves no file behind."""
    path = tmp_path / "absent.sqlite3"
    code, out = _run(["--db", str(path)])

    assert code == 3
    assert "hermes has no ledger" in out
    assert not path.exists()


def test_broken_config_exits_three(tmp_path: Path) -> None:
    """An unreadable config dir is a config error, exit 3."""
    out, err = io.StringIO(), io.StringIO()
    code = status_module.main(
        ["--config-dir", str(tmp_path), "--db", str(tmp_path / "x.sqlite3")],
        stream=out,
        err=err,
        now=NOW,
        unit_state=lambda: UnitState.RUNNING,
    )

    assert (code, out.getvalue()) == (3, "")
    assert err.getvalue().startswith("ERROR: config load failed")


def test_bad_arguments_exit_two() -> None:
    """Unknown flags are a usage error."""
    code, out, err = _run_split(["--nope"])

    assert (code, out) == (2, "")
    assert "Usage: hermes status" in err


def test_human_view_shows_utc_and_madrid_times(tmp_path: Path) -> None:
    """The 20:00 UTC next close also shows as 22:00 Madrid (CEST in October)."""
    _, out = _run(["--db", str(_ledger(tmp_path))])
    btc_4h = next(line for line in out.splitlines() if line.strip().startswith("BTC/USDT 4h"))

    assert "2026-10-03 20:00" in btc_4h
    assert "Sat 22:00" in btc_4h


def test_json_carries_the_same_facts(tmp_path: Path) -> None:
    """--json has the verdict, one entry per stream and the open entry."""
    code, out = _run(["--db", str(_ledger(tmp_path)), "--json"])
    data = json.loads(out)

    assert code == 0
    assert data["verdict"]["level"] == "ok"
    assert len(data["streams"]) == len(PAIRS) * len(INTERVALS)
    assert data["streams"][0] == {
        "pair": "BTC/USDT",
        "interval": "4h",
        "last_close": "2026-10-03T16:00:00+00:00",
        "next_close": "2026-10-03T20:00:00+00:00",
        "due": "2026-10-03T20:00:05+00:00",
        "state": "on time",
        "late_by_seconds": 0,
    }
    assert data["open_positions"] == [
        {
            "pair": "ETH/USDT",
            "entry_time": "2026-10-03T12:00:00+00:00",
            "entry_price": "2618.45",
        }
    ]


@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_no_pnl_field_in_any_output(tmp_path: Path, as_json: bool) -> None:
    """Blind until certification: neither view may name an outcome figure."""
    args = ["--db", str(_ledger(tmp_path))] + (["--json"] if as_json else [])
    _, out = _run(args)

    assert _PNL_WORDS.findall(out) == []


def test_status_is_reachable_from_the_top_level_cli(tmp_path: Path) -> None:
    """`hermes status --help` dispatches through the main CLI."""
    out = io.StringIO()

    assert cli.main(["status", "--help"], stream=out) == 0
    assert out.getvalue().startswith("Usage: hermes status")


def test_plain_output_snapshot(tmp_path: Path, snapshot: SnapshotAssertion) -> None:
    """Pins the non-terminal (pasteable) layout."""
    code, out = _run(["--db", str(_ledger(tmp_path))])

    assert (code, _rstrip_lines(out)) == (0, snapshot)


def test_terminal_render_snapshot(tmp_path: Path, snapshot: SnapshotAssertion) -> None:
    """Pins the terminal layout: the verdict sits in a panel at 100 columns."""
    with SqliteStatusReader.open(_ledger(tmp_path)) as reader:
        report = build_report(
            reader,
            pairs=PAIRS,
            intervals=INTERVALS,
            now=NOW,
            settle=timedelta(seconds=5),
            grace=timedelta(seconds=900),
            unit=UnitState.RUNNING,
        )
    console = Console(file=io.StringIO(), force_terminal=True, width=100, record=True)

    render_status(report, console)

    assert _rstrip_lines(console.export_text()) == snapshot


@pytest.mark.parametrize(
    ("active", "sub", "expected"),
    [
        ("active", "running", UnitState.RUNNING),
        ("failed", "failed", UnitState.FAILED),
        ("activating", "auto-restart", UnitState.RESTARTING),
        ("inactive", "dead", UnitState.STOPPED),
        ("", "", UnitState.UNKNOWN),
    ],
)
def test_unit_state_mapping(active: str, sub: str, expected: UnitState) -> None:
    """Systemd's ActiveState/SubState map onto the verdict's unit states."""
    assert status_module.unit_state_from(active, sub) is expected


def test_systemd_probe_parses_systemctl_show(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe reads `systemctl show` key=value lines."""
    monkeypatch.setattr(status_module.shutil, "which", lambda _: "/usr/bin/systemctl")

    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "ActiveState=failed\nSubState=failed\n", "")

    assert status_module.systemd_unit_state(run=fake_run) is UnitState.FAILED


def test_systemd_probe_is_unknown_without_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dev box without systemd yields UNKNOWN, so the ledger alone decides."""
    monkeypatch.setattr(status_module.shutil, "which", lambda _: None)

    assert status_module.systemd_unit_state() is UnitState.UNKNOWN


@pytest.mark.parametrize(
    ("scenario", "code", "verdict"),
    [
        ("ok-two-positions", 0, "● live · checked 14:00 · next 18:00"),
        ("late", 1, "◐ 18:00 check late · 12m"),
        ("down-stale", 1, "✖ hermes down since 18:04"),
    ],
)
def test_demo_status_shows_the_scenario_heartbeat(scenario: str, code: int, verdict: str) -> None:
    """`hermes --demo SCENARIO status` shows that heartbeat, tagged, with matching exit code."""
    out = io.StringIO()

    rc = cli.main(["--demo", scenario, "status"], stream=out)

    assert rc == code
    assert "DEMO DATA" in out.getvalue().splitlines()[0]
    assert verdict in out.getvalue()


def test_demo_status_reads_the_environment_variable() -> None:
    """HERMES_CONSOLE_DEMO selects a scenario like the flag does."""
    out = io.StringIO()

    rc = status_module.main(["--json"], stream=out, env={"HERMES_CONSOLE_DEMO": "late"})
    data = json.loads(out.getvalue())

    assert (rc, data["demo_scenario"], data["heartbeat"]["state"]) == (1, "late", "late")


def test_demo_status_with_an_unknown_scenario_exits_two() -> None:
    """A typo in the scenario is a usage error."""
    out, err = io.StringIO(), io.StringIO()

    rc = status_module.main([], stream=out, err=err, demo_flag="no-such-scenario")

    assert (rc, out.getvalue()) == (2, "")
    assert err.getvalue().startswith("ERROR:")
