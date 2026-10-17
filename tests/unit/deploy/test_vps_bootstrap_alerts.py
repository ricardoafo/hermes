"""Alert wiring in ``vps_bootstrap.sh``: the failure-alert script and the health timer.

``hermes-notify-failure@.service`` runs its script from ``/opt/hermes/bin`` so a
missing or broken release cannot silence the alert (#364). The bootstrap has to
put the script there before any release exists, and turn the healthchecks.io
timer on once the env file has a ping URL. These tests cut the two functions
out of the real script and run them under bash with ``systemctl`` and
``install`` replaced by recorders (both need root on a real systemd host).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "deploy" / "vps_bootstrap.sh"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "vps-bootstrap.yml"
_BASH = shutil.which("bash")

_needs_bash = pytest.mark.skipif(_BASH is None, reason="the bootstrap script needs bash")

_RECORDER = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$(dirname "$0")/calls.log"
"""


def _function(name: str) -> str:
    text = _SCRIPT.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _run(tmp_path: Path, function: str, call: str) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("systemctl", "install", "chown"):
        stub = bin_dir / name
        stub.write_text(_RECORDER, encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    snippet = tmp_path / "snippet.sh"
    snippet.write_text(
        f"set -euo pipefail\n{_function(function)}{call}\n", encoding="utf-8", newline="\n"
    )
    env = {**os.environ, "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ.get('PATH', '')}"}
    assert _BASH is not None
    result = subprocess.run(  # noqa: S603 - fixed argv, test-owned script
        [_BASH, snippet.as_posix()], capture_output=True, text=True, env=env, check=False
    )
    log = bin_dir / "calls.log"
    return result, log.read_text(encoding="utf-8") if log.exists() else ""


def _env_file(tmp_path: Path, body: str) -> str:
    path = tmp_path / "env.production"
    path.write_text(body, encoding="utf-8", newline="\n")
    return path.as_posix()


@_needs_bash
def test_health_timer_is_enabled_when_the_env_file_has_a_ping_url(tmp_path: Path) -> None:
    """With a ping URL in env.production the heartbeat must run, or downtime goes unnoticed."""
    env_file = _env_file(tmp_path, 'HERMES_HEALTHCHECKS__PING_URL="https://hc-ping.com/abc"\n')

    result, calls = _run(tmp_path, "ensure_health_timer", f"ensure_health_timer {env_file}")

    assert result.returncode == 0, result.stderr
    assert calls == "systemctl enable --now hermes-health.timer\n"


@pytest.mark.parametrize(
    "body",
    ['HERMES_ENV="prod"\n', 'HERMES_HEALTHCHECKS__PING_URL=""\n'],
    ids=["missing", "empty"],
)
@_needs_bash
def test_health_timer_stays_off_without_a_ping_url(tmp_path: Path, body: str) -> None:
    """Without a URL every ping fails; the timer must stay off rather than fail every 5 min."""
    env_file = _env_file(tmp_path, body)

    result, calls = _run(tmp_path, "ensure_health_timer", f"ensure_health_timer {env_file}")

    assert result.returncode == 0, result.stderr
    assert calls == ""


@_needs_bash
def test_health_timer_stays_off_when_the_env_file_is_absent(tmp_path: Path) -> None:
    """A first bootstrap with no secrets writes no env file; enabling the timer then is wrong."""
    missing = (tmp_path / "absent").as_posix()

    result, calls = _run(tmp_path, "ensure_health_timer", f"ensure_health_timer {missing}")

    assert result.returncode == 0, result.stderr
    assert calls == ""


@_needs_bash
def test_notifier_is_installed_root_owned_and_executable_when_it_changed(tmp_path: Path) -> None:
    """The alert script must land in /opt/hermes/bin before any release exists (#364)."""
    src = tmp_path / "hermes-notify-failure.sh"
    src.write_text("#!/bin/bash\n", encoding="utf-8")
    dst = tmp_path / "bin-dst" / "hermes-notify-failure.sh"

    result, calls = _run(
        tmp_path, "install_notifier", f"install_notifier {src.as_posix()} {dst.as_posix()}"
    )

    assert result.returncode == 0, result.stderr
    assert calls == f"install -m 0755 -o root -g root {src.as_posix()} {dst.as_posix()}\n"


@_needs_bash
def test_notifier_is_left_alone_when_unchanged(tmp_path: Path) -> None:
    """A re-run must not rewrite an identical script."""
    src = tmp_path / "src.sh"
    dst = tmp_path / "dst.sh"
    for path in (src, dst):
        path.write_text("#!/bin/bash\n", encoding="utf-8")

    result, calls = _run(
        tmp_path, "install_notifier", f"install_notifier {src.as_posix()} {dst.as_posix()}"
    )

    assert result.returncode == 0, result.stderr
    assert calls == ""


@_needs_bash
def test_missing_notifier_source_fails_the_bootstrap(tmp_path: Path) -> None:
    """If the workflow stops shipping the script, the bootstrap must fail, not skip the alert."""
    missing = (tmp_path / "absent.sh").as_posix()
    dst = (tmp_path / "dst.sh").as_posix()

    result, calls = _run(tmp_path, "install_notifier", f"install_notifier {missing} {dst}")

    assert result.returncode != 0
    assert calls == ""


def test_bootstrap_clears_a_stale_failed_notifier_unit() -> None:
    """A notifier that failed under the old layout must not stay listed in `systemctl --failed`."""
    assert "systemctl reset-failed 'hermes-notify-failure@*' || true" in _SCRIPT.read_text(
        encoding="utf-8"
    )


def test_bootstrap_workflow_ships_the_notifier_script() -> None:
    """The bootstrap reads the script from its upload dir; the workflow must send it."""
    steps = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["bootstrap"]["steps"]
    sparse = next(s for s in steps if "sparse-checkout" in s.get("with", {}))["with"]
    runs = "\n".join(str(s.get("run", "")) for s in steps)

    assert {"deploy/hermes-notify-failure.sh", "deploy/hermes-notify-crash.sh"} <= set(
        sparse["sparse-checkout"].split()
    )
    assert "tar -C deploy -cf - systemd hermes-notify-failure.sh hermes-notify-crash.sh" in runs


def test_bootstrap_uploads_to_a_root_only_dir_the_script_reads() -> None:
    """The unit and alert upload lands outside world-writable /var/tmp, where the script reads it.

    In /var/tmp another local user could create the directory between the
    workflow's rm and install -d and plant files root then installs (#782).
    """
    steps = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["bootstrap"]["steps"]
    runs = "\n".join(str(s.get("run", "")) for s in steps)

    assert "BOOTSTRAP_DIR=/var/lib/hermes-bootstrap\n" in _SCRIPT.read_text(encoding="utf-8")
    assert "tar -xf - -C /var/lib/hermes-bootstrap'" in runs
    assert "/var/tmp" not in runs  # noqa: S108 - asserts the path is absent


@_needs_bash
def test_daily_timer_is_enabled_when_the_env_file_has_a_slack_token(tmp_path: Path) -> None:
    """With a Slack token the daily alive line runs (#373)."""
    env_file = _env_file(tmp_path, 'HERMES_SLACK__BOT_TOKEN="xoxb-abc"\n')

    result, calls = _run(tmp_path, "ensure_daily_timer", f"ensure_daily_timer {env_file}")

    assert result.returncode == 0, result.stderr
    assert calls == "systemctl enable --now hermes-daily.timer\n"


@_needs_bash
def test_daily_timer_stays_off_without_a_slack_token(tmp_path: Path) -> None:
    """Without a token every daily run would fail; the timer stays off."""
    env_file = _env_file(tmp_path, 'HERMES_ENV="prod"\n')

    result, calls = _run(tmp_path, "ensure_daily_timer", f"ensure_daily_timer {env_file}")

    assert (result.returncode, calls) == (0, "")


@_needs_bash
def test_bot_env_file_holds_only_the_telegram_values_and_the_unit_starts(tmp_path: Path) -> None:
    """ADR-015 D-05: the bot gets its own env file with no exchange keys, then runs."""
    env_file = (tmp_path / "env.bot").as_posix()
    call = (
        "HERMES_TELEGRAM__BOT_TOKEN=123:fake HERMES_TELEGRAM__CHAT_ID=-100 "  # gitleaks:allow
        "HERMES_BINANCE_MAINNET__API_KEY=secret "
        f"ensure_bot {env_file}"
    )

    result, calls = _run(tmp_path, "ensure_bot", call)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "env.bot").read_text(encoding="utf-8") == (
        'HERMES_TELEGRAM__BOT_TOKEN="123:fake"\n'  # gitleaks:allow
        'HERMES_TELEGRAM__CHAT_ID="-100"\n'
        'HERMES_ENV="prod"\n'
    )
    assert "systemctl enable hermes-bot.service" in calls.splitlines()
    assert "systemctl restart hermes-bot.service" in calls.splitlines()


@_needs_bash
def test_bot_stays_off_and_its_env_file_is_removed_without_both_values(tmp_path: Path) -> None:
    """No chat id: the unit is disabled and no stale token file is left behind."""
    stale = tmp_path / "env.bot"
    stale.write_text('HERMES_TELEGRAM__BOT_TOKEN="old"\n', encoding="utf-8")
    call = (
        "HERMES_TELEGRAM__BOT_TOKEN=123:fake HERMES_TELEGRAM__CHAT_ID= "  # gitleaks:allow
        f"ensure_bot {stale.as_posix()}"
    )

    result, calls = _run(tmp_path, "ensure_bot", call)

    assert result.returncode == 0, result.stderr
    assert (stale.exists(), calls.splitlines()) == (
        False,
        ["systemctl disable --now hermes-bot.service"],
    )
