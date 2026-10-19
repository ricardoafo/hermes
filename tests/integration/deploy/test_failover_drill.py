"""Tier 2 invariants for ``scripts/deploy/failover_drill.sh`` (#31).

The drill restarts ``hermes.service`` on hermes-01, so what it refuses to do
matters as much as what it does. These tests run the real script under bash
with stub ``systemctl``, health gate and ``hermes`` commands that log every
call, and assert on that log and on the report the script writes.

The stubs are the only mocks. The seam they replace is the real unit and
release on the box, which only the operator's monthly drill covers
(docs/runbook/failover-to-kraken.md).

Reference: openspec/archive/31-failover-drill/change.md (Test plan).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.deploy._bash import (
    RUN_ON_THIS_PLATFORM,
    SCRIPT_DIR,
    SKIP_REASON,
    clean_env,
    output,
    posix,
    run_bash,
    write_stubs,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN_ON_THIS_PLATFORM, reason=SKIP_REASON),
]

_SCRIPT = SCRIPT_DIR / "failover_drill.sh"
# 2026-10-04 02:00Z: two hours into the 00:00Z bar, clear of both guards.
_SAFE_EPOCH = 1_791_079_200
_POSITION = {
    "coin": "BTC",
    "timeframe": "4h",
    "side": "long",
    "size": "0.01",
    "opened_at": "2026-10-03T20:00:00+00:00",
    "entry_price": "84575.5",
    "stop_price": "82000",
}

# Dispatches on the hermes subcommand; ``$2`` is the token after the binary.
_STUB_RUN = """\
#!/usr/bin/env bash
echo "run $*" >> "$STUB_LOG"
case "$2" in
    failover-check)
        echo "verdict: ${STUB_FAILOVER_VERDICT:-READY}"
        [ "${STUB_FAILOVER_VERDICT:-READY}" = READY ]
        ;;
    positions)
        n=$(grep -c '^run .* positions' "$STUB_LOG")
        if [ "$n" -gt 1 ] && [ -n "${STUB_POSITIONS_AFTER:-}" ]; then
            cat "$STUB_POSITIONS_AFTER"
        else
            cat "$STUB_POSITIONS_BEFORE"
        fi
        ;;
esac
"""

_STUB_SYSTEMCTL = """\
#!/usr/bin/env bash
echo "systemctl $*" >> "$STUB_LOG"
[ "$1" != "${STUB_SYSTEMCTL_FAIL:-none}" ]
"""

_STUB_GATE = """\
#!/usr/bin/env bash
echo "gate $*" >> "$STUB_LOG"
if [ -n "${STUB_GATE_FAIL:-}" ]; then echo "::error::no serve.run.started"; exit 1; fi
echo "gate OK: hermes.service active 30s"
"""


def _positions(path: Path, rows: list[dict[str, str]]) -> Path:
    path.write_text(json.dumps({"positions": rows, "as_of": "x"}), encoding="utf-8")
    return path


def _ledger(path: Path) -> Path:
    sqlite3.connect(path).close()
    return path


def _run(
    tmp_path: Path, *args: str, **env: str
) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
    stubs = tmp_path / "stubs"
    write_stubs(stubs, {"run": _STUB_RUN, "systemctl": _STUB_SYSTEMCTL, "gate": _STUB_GATE})
    log = tmp_path / "calls.log"
    log.touch()
    report = tmp_path / "report"
    env_file = tmp_path / "env.production"
    env_file.write_text(env.pop("ENV_FILE_BODY", 'HERMES_ALLOW_MAINNET="no"\n'), encoding="utf-8")
    settings = {
        "STUB_LOG": posix(log),
        "STUB_POSITIONS_BEFORE": posix(_positions(tmp_path / "before.json", [_POSITION])),
        "SYSTEMCTL": posix(stubs / "systemctl"),
        "HEALTH_GATE": posix(stubs / "gate"),
        "HERMES_RUN": posix(stubs / "run"),
        "HERMES_BIN": "hermes",
        "HERMES_PYTHON": sys.executable,
        "HERMES_DB": posix(_ledger(tmp_path / "hermes.sqlite3")),
        "HERMES_ENV_FILE": posix(env_file),
        "NOW_EPOCH": str(_SAFE_EPOCH),
    }
    settings.update(env)
    result = run_bash([posix(_SCRIPT), "--report-dir", posix(report), *args], clean_env(**settings))
    return result, report, log.read_text(encoding="utf-8").splitlines()


def test_rehearsal_checks_kraken_and_never_touches_the_unit(tmp_path: Path) -> None:
    """Without --execute the drill is read-only: no systemctl call at all."""
    result, report, calls = _run(tmp_path)
    assert result.returncode == 0, output(result)
    assert [c for c in calls if c.startswith("systemctl")] == []
    assert calls == [
        "run hermes failover-check --config-dir /opt/hermes/current/config",
        "run hermes positions --json --utc",
    ]
    assert "Verdict: **PASSED**" in (report / "report.md").read_text(encoding="utf-8")


def test_execute_restarts_the_unit_in_order(tmp_path: Path) -> None:
    """--execute checks Kraken, stops, starts, gates and compares, in that order."""
    result, report, calls = _run(tmp_path, "--execute")
    assert result.returncode == 0, output(result)
    assert calls == [
        "run hermes failover-check --config-dir /opt/hermes/current/config",
        "run hermes positions --json --utc",
        "systemctl stop hermes.service",
        "systemctl start hermes.service",
        f"gate {_SAFE_EPOCH}",
        "run hermes positions --json --utc",
    ]
    assert (report / "ledger-before.sqlite3").is_file()


def test_execute_reports_the_missing_venue_switch(tmp_path: Path) -> None:
    """The venue switch that serve cannot do yet is in the report, not silently skipped."""
    _, report, _ = _run(tmp_path, "--execute")
    assert "| venue switch | NOT AVAILABLE |" in (report / "report.md").read_text(encoding="utf-8")


def test_kraken_not_ready_stops_before_the_unit_is_touched(tmp_path: Path) -> None:
    """A failing failover-check ends the drill with exit 1 and no restart."""
    result, report, calls = _run(tmp_path, "--execute", STUB_FAILOVER_VERDICT="NOT READY")
    assert result.returncode == 1, output(result)
    assert [c for c in calls if c.startswith("systemctl")] == []
    assert "| failover-check | FAIL |" in (report / "report.md").read_text(encoding="utf-8")


def test_changed_positions_fail_the_drill(tmp_path: Path) -> None:
    """A position whose size differs after the restart fails with the difference written out."""
    after = _positions(tmp_path / "after.json", [{**_POSITION, "size": "0.02"}])
    result, report, _ = _run(tmp_path, "--execute", STUB_POSITIONS_AFTER=posix(after))
    assert result.returncode == 1, output(result)
    diff = (report / "positions-diff.txt").read_text(encoding="utf-8")
    assert "before only: BTC 4h long 0.01" in diff
    assert "after only: BTC 4h long 0.02" in diff


def test_moved_stop_alone_is_not_a_mismatch(tmp_path: Path) -> None:
    """Only identity fields are compared; a trailing stop may move between snapshots."""
    after = _positions(tmp_path / "after.json", [{**_POSITION, "stop_price": "83000"}])
    result, _, _ = _run(tmp_path, "--execute", STUB_POSITIONS_AFTER=posix(after))
    assert result.returncode == 0, output(result)


def test_failed_health_gate_fails_the_drill(tmp_path: Path) -> None:
    """A unit that does not come back healthy is a failed drill."""
    result, report, _ = _run(tmp_path, "--execute", STUB_GATE_FAIL="1")
    assert result.returncode == 1, output(result)
    assert "| start and gate | FAIL |" in (report / "report.md").read_text(encoding="utf-8")


def test_failed_stop_does_not_try_to_start(tmp_path: Path) -> None:
    """When the unit will not stop, the drill ends there."""
    result, _, calls = _run(tmp_path, "--execute", STUB_SYSTEMCTL_FAIL="stop")
    assert result.returncode == 1, output(result)
    assert "systemctl start hermes.service" not in calls


@pytest.mark.parametrize(
    ("offset", "allowed"),
    [(0, False), (599, False), (600, True), (13_200, True), (13_201, False), (14_399, False)],
)
def test_bar_window_guard(tmp_path: Path, offset: int, allowed: bool) -> None:
    """The drill refuses from 20 min before to 10 min after a 4h bar close (exit 4)."""
    epoch = _SAFE_EPOCH - 7_200 + offset
    result, _, calls = _run(tmp_path, "--execute", NOW_EPOCH=str(epoch))
    assert (result.returncode == 0, bool(calls)) == (allowed, allowed), output(result)
    if not allowed:
        assert result.returncode == 4


@pytest.mark.parametrize("value", ["yes", '"yes"', "true", "1"])
def test_real_capital_mode_is_refused(tmp_path: Path, value: str) -> None:
    """With mainnet allowed the script does nothing and exits 5."""
    result, _, calls = _run(tmp_path, "--execute", ENV_FILE_BODY=f"HERMES_ALLOW_MAINNET={value}\n")
    assert (result.returncode, calls) == (5, []), output(result)


@pytest.mark.parametrize("args", [["--bogus"], ["--report-dir"]])
def test_bad_arguments_exit_2(tmp_path: Path, args: list[str]) -> None:
    """An unknown option or a missing value is a usage error."""
    stubs_env = clean_env(NOW_EPOCH=str(_SAFE_EPOCH))
    result = run_bash([posix(_SCRIPT), *args], stubs_env)
    assert result.returncode == 2, output(result)
