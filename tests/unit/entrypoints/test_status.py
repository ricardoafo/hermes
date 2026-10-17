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
import os
import re
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from syrupy.assertion import SnapshotAssertion

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.application.status import UnitState, build_report
from hermes.domain.events.risk_halt_recorded import HaltKind, RiskHaltRecorded
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
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
_NO_LINK = _PROJECT_ROOT / "no-such-release-link"
_FLIPPED_AT = datetime(2026, 10, 3, 12, 22, tzinfo=UTC)
# Fixed so the snapshots do not change at every release cut.
_PACKAGE_VERSION = "1.0.0a1"
_PNL_WORDS = re.compile(
    r"p&l|pnl|profit|loss|equity|sharpe|drawdown|win rate|return", re.IGNORECASE
)


def _run(
    args: list[str],
    *,
    now: Any = NOW,
    unit: UnitState = UnitState.RUNNING,
    release_link: Path | None = None,
    package_version: str = _PACKAGE_VERSION,
) -> tuple[int, str]:
    """Run ``hermes status`` with a fixed clock and unit state; return code and output."""
    out = io.StringIO()
    code = status_module.main(
        ["--config-dir", str(_CONFIG), *args],
        stream=out,
        now=now,
        unit_state=lambda: unit,
        bot_state=lambda: UnitState.UNKNOWN,
        release_link=release_link or _NO_LINK,
        package_version=package_version,
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
        release_link=_NO_LINK,
        package_version=_PACKAGE_VERSION,
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
    assert out.splitlines()[4] == "● All good"


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


def _release_link(tmp_path: Path) -> Path:
    """Create a ``current`` link to ``abc123def456`` whose own mtime is ``_FLIPPED_AT``."""
    target = tmp_path / "releases" / "abc123def456"
    target.mkdir(parents=True)
    link = tmp_path / "current"
    try:
        link.symlink_to(target, target_is_directory=True)
        os.utime(link, (_FLIPPED_AT.timestamp(),) * 2, follow_symlinks=False)
    except OSError, NotImplementedError:
        pytest.skip("symlinks or symlink mtimes unavailable")
    return link


def test_text_header_shows_release_and_live_since_in_madrid(tmp_path: Path) -> None:
    """The release line names the link target and the flip time in Madrid (CEST, UTC+2)."""
    link = _release_link(tmp_path)

    code, out = _run(["--db", str(_ledger(tmp_path))], release_link=link)

    assert code == 0
    assert "release: abc123def456   live since 2026-10-03 14:22 Madrid" in out.splitlines()


def test_json_carries_release_and_live_since_in_utc(tmp_path: Path) -> None:
    """--json has the release name and the link's mtime as ISO-8601 UTC."""
    link = _release_link(tmp_path)

    _, out = _run(["--db", str(_ledger(tmp_path)), "--json"], release_link=link)
    data = json.loads(out)

    assert (data["release"], data["live_since"]) == ("abc123def456", "2026-10-03T12:22:00+00:00")


def test_text_header_names_the_running_version_like_its_tag(tmp_path: Path) -> None:
    """Package 1.0.0a1 shows as `version: v1.0.0-shadow.1`, the tag CD gives it (ADR-022 §4)."""
    _, out = _run(["--db", str(_ledger(tmp_path))], package_version="1.0.0a2")

    assert "version: v1.0.0-shadow.2" in out.splitlines()


def test_json_carries_the_running_version(tmp_path: Path) -> None:
    """--json names the version the same way the text view does."""
    _, out = _run(["--db", str(_ledger(tmp_path)), "--json"], package_version="1.0.0")

    assert json.loads(out)["version"] == "v1.0.0"


def test_a_development_version_is_shown_as_it_is(tmp_path: Path) -> None:
    """A version no cut wrote, such as a local build, still prints and never fails status."""
    code, out = _run(["--db", str(_ledger(tmp_path))], package_version="1.0.1.dev4")

    assert (code, "version: 1.0.1.dev4" in out.splitlines()) == (0, True)


def test_text_without_a_release_link_says_unknown_and_exits_unchanged(tmp_path: Path) -> None:
    """No link: `release: unknown`, no live-since, and the verdict exit code stays 0."""
    code, out = _run(["--db", str(_ledger(tmp_path))])

    assert code == 0
    assert "release: unknown" in out.splitlines()
    assert "live since" not in out


def test_json_without_a_release_link_has_unknown_and_null_live_since(tmp_path: Path) -> None:
    """No link: the JSON release is `unknown` and live_since is null."""
    _, out = _run(["--db", str(_ledger(tmp_path)), "--json"])
    data = json.loads(out)

    assert (data["release"], data["live_since"]) == ("unknown", None)


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


def _ledger_with_kill(tmp_path: Path) -> Path:
    """Write the fixture ledger plus one kill on BTC an hour before the 16:00 bar."""
    path = _ledger(tmp_path)
    connection = sqlite3.connect(path)
    SqliteLedger(connection).append_event(
        RiskHaltRecorded(
            occurred_at=LAST_4H - timedelta(hours=1),
            correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
            strategy_id=StrategyId("swing-donchian-4h"),
            pair=Pair(base=Currency.BTC, quote=Currency.USDT),
            kind=HaltKind.KILL,
            rule="max_drawdown",
            reason="drawdown_kill_threshold",
            pre_registration_ref="§10",
        )
    )
    connection.close()
    return path


def test_text_shows_the_halt_count_and_the_last_halt(tmp_path: Path) -> None:
    """The halts line names the 24-hour count and the newest halt's kind, pair and rule (#749)."""
    code, out = _run(["--db", str(_ledger_with_kill(tmp_path))])

    lines = out.splitlines()
    at = next(i for i, text in enumerate(lines) if text.startswith("halts"))
    assert (code, lines[at], lines[at + 1]) == (
        0,
        "halts      24h: 1   30d last: kill BTC/USDT max_drawdown",
        "  2026-10-03 15:00 UTC (Sat 17:00 Madrid)  drawdown_kill_threshold (pre-reg §10)",
    )


def test_json_carries_the_last_halt(tmp_path: Path) -> None:
    """--json has the same halt facts as the text view."""
    _, out = _run(["--db", str(_ledger_with_kill(tmp_path)), "--json"])

    assert json.loads(out)["halts"] == {
        "last_24h": 1,
        "last": {
            "kind": "kill",
            "pair": "BTC/USDT",
            "rule": "max_drawdown",
            "reason": "drawdown_kill_threshold",
            "pre_registration_ref": "§10",
            "occurred_at": "2026-10-03T15:00:00+00:00",
        },
    }
