"""Fixtures for the post-deploy smoke tier.

Where these run: on the CD runner, inside the ``Deploy to VPS`` job,
right after ``install_release.sh`` returns. That job has already joined
the tailnet and written the SSH key, so each test runs read-only shell
commands on hermes-01 over the same SSH path the deploy used. Nothing is
installed on the VPS for testing, and nothing here writes there or calls
an exchange (AGENTS.md rule 7).

Environment (set by ``.github/workflows/cd.yml``):

- ``HERMES_SMOKE_SSH``: the ssh arguments that reach hermes-01, e.g.
  ``-i ~/.ssh/vps_id -o StrictHostKeyChecking=yes root@hermes-01``.
- ``HERMES_SMOKE_RELEASE``: the sha12 this deploy installed.
- ``HERMES_SMOKE_SINCE``: Unix time taken just before the install, so
  the startup log line must come from this restart.

Without ``HERMES_SMOKE_SSH`` the tier skips, so a local ``pytest`` run
never reaches for a server. CD sets ``HERMES_SMOKE_REQUIRED=1``, which
turns that skip into a failure: a deploy whose smoke tests could not run
must not pass.
"""

import os
import shlex
import shutil
import subprocess

import pytest

from tests.smoke._remote import RemoteResult, RunOnVps


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        if os.environ.get("HERMES_SMOKE_REQUIRED") == "1":
            pytest.fail(f"{name} is not set; CD must provide it")
        pytest.skip(f"{name} not set; smoke tests run only from CD")
    return value


@pytest.fixture(scope="session")
def run_on_vps() -> RunOnVps:
    """Run a shell command on hermes-01 over the deploy's SSH path."""
    ssh_args = shlex.split(_required_env("HERMES_SMOKE_SSH"))
    ssh = shutil.which("ssh")
    if ssh is None:
        pytest.fail("ssh is not installed on this runner")

    def run(command: str) -> RemoteResult:
        proc = subprocess.run(  # noqa: S603 — fixed argv, command is test-authored
            [ssh, "-o", "BatchMode=yes", *ssh_args, command],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return RemoteResult(proc.returncode, proc.stdout, proc.stderr)

    return run


@pytest.fixture(scope="session")
def deployed_release() -> str:
    """Return the sha12 this deploy installed."""
    return _required_env("HERMES_SMOKE_RELEASE")


@pytest.fixture(scope="session")
def deploy_started_at() -> int:
    """Return the Unix time taken just before this deploy's install started."""
    return int(_required_env("HERMES_SMOKE_SINCE"))
