"""Post-deploy smoke tests: the release CD just installed is the one running.

Each test protects one claim a green deploy makes about hermes-01. They
run read-only commands over SSH (see ``conftest.py``) and never touch an
exchange or write anything on the server.

Not covered yet: ``hermes status`` reporting a fresh heartbeat (#370),
added once that command has merged.
"""

from __future__ import annotations

import pytest

from tests.smoke._remote import RunOnVps

pytestmark = pytest.mark.smoke

ROOT = "/opt/hermes"
UNIT = "hermes.service"


def test_current_points_at_the_deployed_release(
    run_on_vps: RunOnVps, deployed_release: str
) -> None:
    """`current` resolves to this deploy's release, not the previous one.

    A deploy that rolled back, or never flipped the symlink, would leave
    systemd running old code while CD reports success.
    """
    result = run_on_vps(f"readlink -f {ROOT}/current")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{ROOT}/versions/{deployed_release}"


def test_installed_wheel_imports_and_reports_a_version(run_on_vps: RunOnVps) -> None:
    """The release venv has an importable hermes with a package version.

    A half-installed venv (missing wheel, broken interpreter link) fails
    here before it fails at the next restart.
    """
    result = run_on_vps(
        f"{ROOT}/current/.venv/bin/python -c "
        "'import importlib.metadata as m, hermes; print(m.version(\"hermes\"))'"
    )

    assert result.returncode == 0, result.stderr
    version = result.stdout.strip()
    assert version
    assert version[0].isdigit(), version


def test_production_config_loads_without_printing_anything(run_on_vps: RunOnVps) -> None:
    """The shipped config plus the production env loads cleanly and silently.

    Loading goes through the same ``load_config`` the unit runs. The only
    output allowed is the marker line, so a loader that echoed a secret
    or warned about a missing key fails this test.
    """
    result = run_on_vps(
        "set -a && . /etc/hermes/env.production && set +a && "
        f"cd /tmp && {ROOT}/current/.venv/bin/python -c "
        "'from pathlib import Path; "
        "from hermes.infrastructure.config.loader import load_config; "
        f'load_config(Path("{ROOT}/current/config")); print("config-ok")\''
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "config-ok\n"
    assert result.stderr == ""


def test_unit_is_active(run_on_vps: RunOnVps) -> None:
    """hermes.service is running after the deploy."""
    result = run_on_vps(f"systemctl is-active {UNIT}")

    assert result.stdout.strip() == "active", result.stdout + result.stderr


def test_unit_logged_a_startup_after_this_deploy(
    run_on_vps: RunOnVps, deploy_started_at: int
) -> None:
    """The journal shows `serve.run.started` from a start after this deploy began.

    An `active` unit that never reached the serve loop, or a startup line
    left over from before the deploy, would otherwise pass.
    """
    result = run_on_vps(f"journalctl -u {UNIT} --since @{deploy_started_at} --no-pager -o cat")

    assert result.returncode == 0, result.stderr
    assert "serve.run.started" in result.stdout
