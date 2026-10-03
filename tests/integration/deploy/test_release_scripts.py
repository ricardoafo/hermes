"""Tier 2 invariants for the release scripts that run on hermes-01.

``install_release.sh``, ``health_gate.sh`` and ``rollback.sh`` decide
whether a deploy goes live, and nothing else in CI runs them before the
first real deploy. These tests run the real scripts under bash against a
scratch ``HERMES_ROOT``, with stub ``uv``, ``systemctl``, ``journalctl``,
``stat``, ``id`` and ``sleep`` binaries first on ``PATH``. The stubs log
every call to one file, so tests can assert what the scripts did, and
read per-test switches from ``STUB_*`` environment variables.

The stubs are the only mocks. The seam they replace is the real unit and
the real uv, which only the operator acceptance run on the box covers
(openspec/changes/179-cd-first-real-deploy/change.md, Requirements
R-1 to R-4).

CI runs this file on Linux and skips it on Windows; see ``_bash.py`` for
how a Windows contributor can run it.

Reference: openspec/changes/179-cd-first-real-deploy/change.md (Test plan).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.integration.deploy._bash import (
    RUN_ON_THIS_PLATFORM,
    SKIP_REASON,
    clean_env,
    run_bash,
    write_stubs,
)
from tests.integration.deploy._bash import SCRIPT_DIR as _SCRIPT_DIR
from tests.integration.deploy._bash import output as _output
from tests.integration.deploy._bash import posix as _posix

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN_ON_THIS_PLATFORM, reason=SKIP_REASON),
]
_RELEASE_SCRIPTS = ("install_release.sh", "health_gate.sh", "rollback.sh")
_BOOTSTRAP_SCRIPT = "vps_bootstrap.sh"

_SHA_A = "a" * 12
_SHA_B = "b" * 12
_SHA_C = "c" * 12
_WHEEL = "hermes-0.0.0-py3-none-any.whl"
_STRATEGIES_YAML = "strategies: []\n"
_ENV_FILE_DEFAULT = 'HERMES_ENV="prod"\nHERMES_ALLOW_MAINNET="no"\n'

# One short hold keeps the gate loop at two iterations; the stub sleep is
# a no-op, so the loop never waits.
_GATE_HOLD = "4"

_STUB_UV = """\
#!/usr/bin/env bash
echo "uv $*" >> "$STUB_LOG"
case "$1" in
    venv)
        mkdir -p "${@: -1}/bin"
        ;;
    pip)
        if [ -n "${STUB_UV_PIP_FAIL:-}" ]; then
            echo "error: hash mismatch" >&2
            exit 2
        fi
        ;;
esac
exit 0
"""

_STUB_SYSTEMCTL = """\
#!/usr/bin/env bash
echo "systemctl $*" >> "$STUB_LOG"
case "$1" in
    cat)
        [ -z "${STUB_NO_UNIT:-}" ] || exit 1
        ;;
    is-active)
        [ -z "${STUB_INACTIVE:-}" ] || exit 3
        name=$(basename "$(readlink -f "$HERMES_ROOT/current" 2>/dev/null)" 2>/dev/null || true)
        case " ${STUB_BAD_RELEASES:-} " in
            *" $name "*) exit 3 ;;
        esac
        ;;
    show)
        if [ -n "${STUB_PID_CHANGES:-}" ]; then
            n=$(cat "$STUB_STATE/pid" 2>/dev/null || echo 4000)
            n=$((n + 1))
            echo "$n" > "$STUB_STATE/pid"
            echo "$n"
        else
            echo 4242
        fi
        ;;
esac
exit 0
"""

_STUB_JOURNALCTL = """\
#!/usr/bin/env bash
echo "journalctl $*" >> "$STUB_LOG"
echo "2026-10-02 23:20:25 [info     ] config.loaded                  env=prod"
if [ -z "${STUB_NO_STARTED_LINE:-}" ]; then
    echo "2026-10-02 23:20:26 [info     ] serve.run.started              env=prod"
fi
exit 0
"""

# The scripts read `stat` only for the env file, which a non-root test run
# cannot make root:root.
_STUB_STAT = """\
#!/usr/bin/env bash
echo "${STUB_ENV_STAT:-600 root:root}"
"""

_STUB_ID = """\
#!/usr/bin/env bash
[ -z "${STUB_NO_HERMES_USER:-}" ]
"""

_STUB_SLEEP = """\
#!/usr/bin/env bash
exit 0
"""

_STUBS = {
    "uv": _STUB_UV,
    "systemctl": _STUB_SYSTEMCTL,
    "journalctl": _STUB_JOURNALCTL,
    "stat": _STUB_STAT,
    "id": _STUB_ID,
    "sleep": _STUB_SLEEP,
}


@dataclass
class Box:
    """A scratch hermes-01: a ``HERMES_ROOT``, stub binaries and one call log."""

    base: Path

    @property
    def root(self) -> Path:
        """Return the scratch ``HERMES_ROOT``."""
        return self.base / "opt-hermes"

    @property
    def env_file(self) -> Path:
        """Return the scratch env file the scripts preflight."""
        return self.base / "env.production"

    @property
    def log(self) -> Path:
        """Return the file every stub appends its call to."""
        return self.base / "calls.log"

    @property
    def _stubs(self) -> Path:
        return self.base / "stubs"

    @property
    def _state(self) -> Path:
        return self.base / "state"

    def setup(self) -> Box:
        """Create the directories, the stubs and a valid env file."""
        for directory in (self.root, self._state):
            directory.mkdir(parents=True)
        write_stubs(self._stubs, _STUBS)
        self.env_file.write_text(_ENV_FILE_DEFAULT, encoding="utf-8", newline="\n")
        return self

    def make_stage(self, sha: str, *, wheels: int = 1) -> Path:
        """Build an untarred release bundle the way the ``bundle`` job lays it out."""
        stage = self.base / "stages" / sha
        shutil.rmtree(stage, ignore_errors=True)
        (stage / "bin").mkdir(parents=True)
        (stage / "config").mkdir()
        (stage / "deploy").mkdir()
        (stage / "deploy" / "hermes-health-ping.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        (stage / "config" / "strategies.yaml").write_text(_STRATEGIES_YAML, encoding="utf-8")
        (stage / "requirements.txt").write_text(
            "pydantic==2.0 --hash=sha256:00\n", encoding="utf-8"
        )
        for index in range(wheels):
            (stage / f"hermes-0.0.{index}-py3-none-any.whl").write_bytes(b"wheel")
        for script in _RELEASE_SCRIPTS:
            shutil.copy(_SCRIPT_DIR / script, stage / "bin" / script)
        return stage

    def seed_release(self, sha: str, *, installed: bool = True) -> Path:
        """Create ``versions/<sha>`` directly, as an earlier deploy would have."""
        release = self.root / "versions" / sha
        release.mkdir(parents=True)
        if installed:
            (release / ".installed").touch()
        return release

    def link(self, name: str, sha: str) -> None:
        """Point ``root/<name>`` (``current`` or ``previous``) at ``versions/<sha>``."""
        (self.root / name).symlink_to(self.root / "versions" / sha, target_is_directory=True)

    def target(self, name: str) -> str | None:
        """Return the release id ``root/<name>`` points at, or None when the link is absent."""
        link = self.root / name
        return link.resolve().name if link.is_symlink() else None

    def calls(self) -> list[str]:
        """Return every logged stub call, oldest first."""
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def disk(self) -> list[str]:
        """Return every path under the root, for before/after comparison."""
        return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))

    def env(self, **extra: str) -> dict[str, str]:
        """Return the subprocess environment: stubs first on PATH, ambient hermes vars removed."""
        return clean_env(
            **{
                "PATH": f"{_posix(self._stubs)}{os.pathsep}{os.environ['PATH']}",
                "HERMES_ROOT": _posix(self.root),
                "HERMES_ENV_FILE": _posix(self.env_file),
                "STUB_LOG": _posix(self.log),
                "STUB_STATE": _posix(self._state),
                "GATE_HOLD_SECONDS": _GATE_HOLD,
                **extra,
            }
        )

    def run(self, args: list[str], **extra: str) -> subprocess.CompletedProcess[str]:
        """Run ``bash <args>`` with the stub environment and capture its output."""
        return run_bash(args, self.env(**extra))

    def deploy(
        self, sha: str, *, stage: Path | None = None, **extra: str
    ) -> subprocess.CompletedProcess[str]:
        """Run ``install_release.sh`` from its stage, as the ``cd.yml`` ssh step does."""
        stage = stage or self.make_stage(sha)
        return self.run([_posix(stage / "bin" / "install_release.sh"), sha, _posix(stage)], **extra)

    def rollback(self, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run ``rollback.sh`` from the repo."""
        return self.run([_posix(_SCRIPT_DIR / "rollback.sh")], **extra)

    def gate(self, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run ``health_gate.sh`` with a ``since`` of 0."""
        return self.run([_posix(_SCRIPT_DIR / "health_gate.sh"), "0"], **extra)


@pytest.fixture
def box(tmp_path: Path) -> Box:
    """Return a scratch hermes-01 with a valid env file and no releases."""
    return Box(tmp_path).setup()


def _systemctl_order(calls: list[str], first: str, second: str) -> tuple[int, int]:
    """Return the log positions of the first ``first`` and the first ``second`` systemctl verb."""
    positions = {
        verb: next(i for i, call in enumerate(calls) if call.split()[:2] == ["systemctl", verb])
        for verb in (first, second)
    }
    return positions[first], positions[second]


@pytest.mark.parametrize("script", [*_RELEASE_SCRIPTS, _BOOTSTRAP_SCRIPT])
def test_every_deploy_script_passes_bash_syntax_check(box: Box, script: str) -> None:
    """A script with a syntax error must be caught before it reaches the VPS."""
    result = box.run(["-n", _posix(_SCRIPT_DIR / script)])

    assert result.returncode == 0, _output(result)


def test_first_deploy_points_current_at_the_release(box: Box) -> None:
    """Invariant: a successful deploy leaves `current` on the release it installed."""
    result = box.deploy(_SHA_A)

    assert result.returncode == 0, _output(result)
    assert box.target("current") == _SHA_A


def test_first_deploy_sets_no_previous_link(box: Box) -> None:
    """Invariant: with no earlier release, `previous` must not exist (never previous -> current)."""
    result = box.deploy(_SHA_A)

    assert result.returncode == 0, _output(result)
    assert box.target("previous") is None


def test_first_deploy_copies_the_release_config_and_marks_it_installed(box: Box) -> None:
    """Invariant: the release dir carries its own config and a final `.installed` marker."""
    box.deploy(_SHA_A)

    release = box.root / "versions" / _SHA_A
    assert (release / "config" / "strategies.yaml").read_text(encoding="utf-8") == _STRATEGIES_YAML
    assert (release / ".installed").is_file()


def test_release_carries_the_deploy_scripts_the_units_run(box: Box) -> None:
    """Invariant: units run /opt/hermes/current/deploy/*.sh, so every release ships its own copy."""
    box.deploy(_SHA_A)

    shipped = box.root / "versions" / _SHA_A / "deploy" / "hermes-health-ping.sh"
    assert shipped.read_text(encoding="utf-8") == "#!/bin/bash\n"


def test_deploy_installs_hashed_requirements_before_the_wheel_without_deps(box: Box) -> None:
    """Invariant (D-4): `--require-hashes` installs the lock first, then `--no-deps` the wheel."""
    box.deploy(_SHA_A)

    pip_calls = [call for call in box.calls() if call.startswith("uv pip install")]
    assert len(pip_calls) == 2
    assert "--require-hashes" in pip_calls[0]
    assert "-r" in pip_calls[0].split()
    assert "--no-deps" in pip_calls[1]
    assert pip_calls[1].endswith(_WHEEL)


def test_deploy_builds_the_venv_with_python_3_14(box: Box) -> None:
    """Invariant: the release venv is created for Python 3.14 inside the release dir."""
    box.deploy(_SHA_A)

    venv_calls = [call for call in box.calls() if call.startswith("uv venv")]
    expected = f"uv venv --python 3.14 {_posix(box.root)}/versions/{_SHA_A}/.venv"
    assert venv_calls == [expected]


def test_second_deploy_sets_current_to_the_new_release(box: Box) -> None:
    """Invariant: each deploy moves `current` to the release just installed."""
    box.deploy(_SHA_A)
    result = box.deploy(_SHA_B)

    assert result.returncode == 0, _output(result)
    assert box.target("current") == _SHA_B


def test_second_deploy_sets_previous_to_the_first_release(box: Box) -> None:
    """Invariant (A-3.1): the release `current` replaced becomes `previous`."""
    box.deploy(_SHA_A)
    box.deploy(_SHA_B)

    assert box.target("previous") == _SHA_A


def test_redeploy_of_the_same_sha_does_not_call_uv(box: Box) -> None:
    """Invariant (D-3): an `.installed` release is not rebuilt, so a rerun never calls uv."""
    box.deploy(_SHA_A)
    box.log.unlink()

    result = box.deploy(_SHA_A)

    assert result.returncode == 0, _output(result)
    assert [call for call in box.calls() if call.startswith("uv ")] == []


def test_redeploy_of_the_same_sha_keeps_previous_untouched(box: Box) -> None:
    """Invariant: redeploying the live release must not turn `previous` into the live release."""
    box.deploy(_SHA_A)
    box.deploy(_SHA_B)
    box.deploy(_SHA_B)

    assert box.target("previous") == _SHA_A


def test_failed_gate_restores_the_old_current(box: Box) -> None:
    """Invariant (R-4): a release that fails the gate leaves `current` on the old release."""
    box.deploy(_SHA_A)

    result = box.deploy(_SHA_B, STUB_BAD_RELEASES=_SHA_B)

    assert result.returncode == 1, _output(result)
    assert box.target("current") == _SHA_A


def test_failed_gate_restarts_the_unit_on_the_old_release(box: Box) -> None:
    """Invariant: after the automatic rollback the unit is restarted, so it runs the old code."""
    box.deploy(_SHA_A)
    box.log.unlink()

    box.deploy(_SHA_B, STUB_BAD_RELEASES=_SHA_B)

    restarts = [call for call in box.calls() if call == "systemctl restart hermes.service"]
    assert len(restarts) == 2


def test_failed_first_deploy_stops_the_unit_and_exits_1(box: Box) -> None:
    """Invariant: with no previous release, a failed gate stops the unit, not a crash loop."""
    result = box.deploy(_SHA_A, STUB_BAD_RELEASES=_SHA_A)

    assert result.returncode == 1, _output(result)
    assert "systemctl stop hermes.service" in box.calls()


def test_failed_gate_never_replaces_the_installed_helper_scripts(box: Box) -> None:
    """Invariant: /opt/hermes/bin only changes after a release passes its gate."""
    box.deploy(_SHA_A, STUB_BAD_RELEASES=_SHA_A)

    assert not (box.root / "bin" / "rollback.sh").exists()


def test_good_deploy_installs_the_helper_scripts_executable(box: Box) -> None:
    """Invariant (A-3.3): a good deploy leaves an executable `bin/rollback.sh` behind."""
    box.deploy(_SHA_A)

    rollback = box.root / "bin" / "rollback.sh"
    assert os.access(rollback, os.X_OK) is True
    assert (box.root / "bin" / "health_gate.sh").is_file()


def test_hash_mismatch_leaves_current_unchanged(box: Box) -> None:
    """Invariant (A-2.3): a failed hashed install exits non-zero and `current` stays put."""
    box.deploy(_SHA_A)

    result = box.deploy(_SHA_B, STUB_UV_PIP_FAIL="1")

    assert result.returncode != 0, _output(result)
    assert box.target("current") == _SHA_A


def test_hash_mismatch_removes_the_half_built_release(box: Box) -> None:
    """Invariant: an interrupted build is deleted, so a later prune or redeploy never trusts it."""
    box.deploy(_SHA_A)

    box.deploy(_SHA_B, STUB_UV_PIP_FAIL="1")

    assert (box.root / "versions" / _SHA_B).exists() is False


def test_hash_mismatch_never_restarts_the_unit(box: Box) -> None:
    """Invariant: a release that fails to install never reaches the restart."""
    box.deploy(_SHA_A)
    box.log.unlink()

    box.deploy(_SHA_B, STUB_UV_PIP_FAIL="1")

    assert "systemctl restart hermes.service" not in box.calls()


def _assert_deploy_refused(
    box: Box, result: subprocess.CompletedProcess[str], before: list[str]
) -> None:
    """Assert a preflight refusal: exit 1, nothing on disk changed, no restart."""
    assert result.returncode == 1, _output(result)
    assert box.disk() == before
    assert "systemctl restart hermes.service" not in box.calls()


@pytest.mark.parametrize(
    "line",
    [
        'HERMES_ALLOW_MAINNET="yes"',
        "HERMES_ALLOW_MAINNET=yes",
        "HERMES_ALLOW_MAINNET=YES",
        "HERMES_ALLOW_MAINNET=true",
        "HERMES_ALLOW_MAINNET='on'",
        "HERMES_ALLOW_MAINNET=1",
        "  HERMES_ALLOW_MAINNET = yes",
    ],
)
def test_preflight_refuses_every_spelling_that_enables_mainnet(box: Box, line: str) -> None:
    """Invariant (SEV-1 guard): a deploy never starts a service whose env file enables mainnet."""
    box.env_file.write_text(f'HERMES_ENV="prod"\n{line}\n', encoding="utf-8")
    before = box.disk()

    result = box.deploy(_SHA_A)

    _assert_deploy_refused(box, result, before)


@pytest.mark.parametrize(
    "line", ['HERMES_ALLOW_MAINNET="no"', "HERMES_ALLOW_MAINNET=false", "HERMES_ALLOW_MAINNET=0"]
)
def test_preflight_accepts_every_spelling_that_disables_mainnet(box: Box, line: str) -> None:
    """Invariant: the mainnet check refuses enabling values only, not every such line."""
    box.env_file.write_text(f'HERMES_ENV="prod"\n{line}\n', encoding="utf-8")

    result = box.deploy(_SHA_A)

    assert result.returncode == 0, _output(result)


def test_preflight_ignores_a_commented_mainnet_line(box: Box) -> None:
    """Invariant: a `# HERMES_ALLOW_MAINNET=yes` comment must not block a deploy."""
    box.env_file.write_text(
        '# HERMES_ALLOW_MAINNET=yes\nHERMES_ALLOW_MAINNET="no"\n', encoding="utf-8"
    )

    result = box.deploy(_SHA_A)

    assert result.returncode == 0, _output(result)


@pytest.mark.parametrize(
    "mode", ["400 hermes:hermes", "644 root:root", "600 hermes:hermes", "400 root:root"]
)
def test_preflight_refuses_an_env_file_that_is_not_600_root(box: Box, mode: str) -> None:
    """Invariant (D-2): the env file must be exactly `600 root:root` before any install."""
    before = box.disk()

    result = box.deploy(_SHA_A, STUB_ENV_STAT=mode)

    _assert_deploy_refused(box, result, before)


def test_preflight_refuses_a_missing_env_file(box: Box) -> None:
    """Invariant: a deploy without secrets must fail before it installs, not boot without them."""
    box.env_file.unlink()
    before = box.disk()

    result = box.deploy(_SHA_A)

    _assert_deploy_refused(box, result, before)


def test_preflight_refuses_a_missing_hermes_user(box: Box) -> None:
    """Invariant: no `hermes` user means vps-bootstrap has not run; nothing may be installed."""
    before = box.disk()

    result = box.deploy(_SHA_A, STUB_NO_HERMES_USER="1")

    _assert_deploy_refused(box, result, before)


def test_preflight_refuses_a_missing_unit(box: Box) -> None:
    """Invariant: no hermes.service on the box means vps-bootstrap has not run."""
    before = box.disk()

    result = box.deploy(_SHA_A, STUB_NO_UNIT="1")

    _assert_deploy_refused(box, result, before)


def test_preflight_refuses_a_stage_with_two_wheels(box: Box) -> None:
    """Invariant: the stage must hold exactly one wheel, so the installed code is unambiguous."""
    stage = box.make_stage(_SHA_A, wheels=2)
    before = box.disk()

    result = box.deploy(_SHA_A, stage=stage)

    _assert_deploy_refused(box, result, before)


def test_preflight_refuses_a_stage_with_no_wheel(box: Box) -> None:
    """Invariant: an unmatched wheel glob must not be installed as a literal filename."""
    stage = box.make_stage(_SHA_A, wheels=0)
    before = box.disk()

    result = box.deploy(_SHA_A, stage=stage)

    _assert_deploy_refused(box, result, before)


@pytest.mark.parametrize("sha", ["../escape12345", "ABCDEF123456", "abc", "abcdef1234567"])
def test_preflight_refuses_a_version_id_that_is_not_12_lowercase_hex(box: Box, sha: str) -> None:
    """Invariant: the version id names a directory `rm -rf` may delete, so it is 12 hex."""
    stage = box.make_stage(_SHA_A)
    before = box.disk()

    result = box.deploy(sha, stage=stage)

    _assert_deploy_refused(box, result, before)


def test_gate_passes_when_the_unit_is_stable_and_logged_start(box: Box) -> None:
    """Invariant: a unit that stays active with a steady PID and a start line passes the gate."""
    result = box.gate()

    assert result.returncode == 0, _output(result)
    assert "gate OK" in result.stdout


def test_gate_fails_when_the_journal_has_no_serve_run_started(box: Box) -> None:
    """Invariant (D-7): the journal line decides, because `is-active` alone passes at fork."""
    result = box.gate(STUB_NO_STARTED_LINE="1")

    assert result.returncode == 1, _output(result)
    assert "no serve.run.started since the restart" in result.stdout


def test_gate_fails_when_mainpid_changes(box: Box) -> None:
    """Invariant (D-7): a changing MainPID means the unit restarts itself, so it is unhealthy."""
    result = box.gate(STUB_PID_CHANGES="1")

    assert result.returncode == 1, _output(result)
    assert "restarted itself" in result.stdout


def test_gate_fails_when_the_unit_is_not_active(box: Box) -> None:
    """Invariant: an inactive unit fails the gate at once."""
    result = box.gate(STUB_INACTIVE="1")

    assert result.returncode == 1, _output(result)
    assert "not active at t=0s" in result.stdout


def test_gate_queries_the_journal_since_the_given_epoch(box: Box) -> None:
    """Invariant: the gate only trusts journal lines written after the restart."""
    box.run([_posix(_SCRIPT_DIR / "health_gate.sh"), "1767225600"], GATE_HOLD_SECONDS="0")

    journal_calls = [call for call in box.calls() if call.startswith("journalctl")]
    assert journal_calls == ["journalctl -u hermes.service --since @1767225600 --no-pager -o cat"]


def _two_releases(box: Box) -> None:
    """Seed `previous -> A`, `current -> B`, both installed."""
    box.seed_release(_SHA_A)
    box.seed_release(_SHA_B)
    box.link("previous", _SHA_A)
    box.link("current", _SHA_B)


def test_rollback_flips_current_back_to_previous(box: Box) -> None:
    """Invariant (A-3.3): rollback points `current` at the old `previous` and exits 0."""
    _two_releases(box)

    result = box.rollback()

    assert result.returncode == 0, _output(result)
    assert box.target("current") == _SHA_A


def test_rollback_leaves_previous_alone(box: Box) -> None:
    """Invariant: rollback leaves `previous` alone, so a second run refuses to toggle."""
    _two_releases(box)

    box.rollback()

    assert box.target("previous") == _SHA_A


def test_rollback_refuses_when_current_equals_previous(box: Box) -> None:
    """Invariant (A-3.3): a second rollback exits 1 and leaves `current` where it is."""
    box.seed_release(_SHA_A)
    box.link("previous", _SHA_A)
    box.link("current", _SHA_A)

    result = box.rollback()

    assert result.returncode == 1, _output(result)
    assert "already equals previous" in result.stdout
    assert box.target("current") == _SHA_A


def test_second_rollback_after_a_first_refuses(box: Box) -> None:
    """Invariant: two rollbacks in a row never flip back to the bad release."""
    _two_releases(box)
    box.rollback()

    result = box.rollback()

    assert result.returncode == 1, _output(result)
    assert box.target("current") == _SHA_A


def test_rollback_refuses_without_a_previous_link(box: Box) -> None:
    """Invariant: no previous release means nothing to roll back to, and `current` is untouched."""
    box.seed_release(_SHA_A)
    box.link("current", _SHA_A)

    result = box.rollback()

    assert result.returncode == 1, _output(result)
    assert box.target("current") == _SHA_A


def test_rollback_refuses_a_previous_that_was_never_fully_installed(box: Box) -> None:
    """Invariant: a previous dir without `.installed` is partial and must not go live."""
    box.seed_release(_SHA_A, installed=False)
    box.seed_release(_SHA_B)
    box.link("previous", _SHA_A)
    box.link("current", _SHA_B)

    result = box.rollback()

    assert result.returncode == 1, _output(result)
    assert box.target("current") == _SHA_B


def test_rollback_exits_1_when_the_old_release_fails_its_gate(box: Box) -> None:
    """Invariant: rollback reports failure when the old release does not come up healthy either."""
    _two_releases(box)

    result = box.rollback(STUB_BAD_RELEASES=_SHA_A)

    assert result.returncode == 1, _output(result)


def test_deploy_resets_failed_state_before_restarting(box: Box) -> None:
    """Invariant (A-8.2): `reset-failed` precedes `restart`, or a tripped start limit blocks."""
    box.deploy(_SHA_A)

    reset, restart = _systemctl_order(box.calls(), "reset-failed", "restart")

    assert reset < restart


def test_rollback_resets_failed_state_before_restarting(box: Box) -> None:
    """Invariant (A-8.2): the rollback restart is also preceded by `reset-failed`."""
    _two_releases(box)

    box.rollback()

    reset, restart = _systemctl_order(box.calls(), "reset-failed", "restart")
    assert reset < restart


def test_deploy_reloads_the_unit_files_before_restarting(box: Box) -> None:
    """Invariant: `daemon-reload` precedes `restart` so a refreshed unit file takes effect."""
    box.deploy(_SHA_A)

    reload, restart = _systemctl_order(box.calls(), "daemon-reload", "restart")

    assert reload < restart


def test_six_deploys_keep_the_three_newest_releases(box: Box) -> None:
    """Invariant (A-3.2): old releases are pruned down to the 3 newest."""
    shas = [f"{index:012x}" for index in range(1, 7)]
    epoch = time.time() - 10_000
    for index, sha in enumerate(shas):
        box.deploy(sha)
        # Distinct, increasing mtimes so "newest" never depends on clock resolution.
        os.utime(box.root / "versions" / sha, (epoch + index, epoch + index))

    box.deploy(f"{7:012x}")

    kept = sorted(path.name for path in (box.root / "versions").iterdir())
    assert kept == [f"{index:012x}" for index in (5, 6, 7)]


def _prune(root: Path, script: Path) -> subprocess.CompletedProcess[str]:
    """Source ``install_release.sh`` and run only ``prune_versions`` against ``root``."""
    return run_bash(
        ["-c", 'source "$1" && prune_versions', "bash", _posix(script)],
        clean_env(PATH=os.environ["PATH"], HERMES_ROOT=_posix(root)),
    )


@st.composite
def _release_sets(draw: st.DrawFn) -> tuple[int, list[int], int, int]:
    """Draw 1 to 8 releases with distinct mtime ranks and any `current` and `previous`."""
    count = draw(st.integers(min_value=1, max_value=8))
    ranks = draw(st.permutations(range(count)))
    current = draw(st.integers(min_value=0, max_value=count - 1))
    previous = draw(st.integers(min_value=0, max_value=count - 1))
    return count, list(ranks), current, previous


@pytest.mark.property
# timeout: each example spawns bash twice; 25 examples can pass the 60 s default on a slow runner.
@pytest.mark.timeout(120)
@settings(max_examples=25, deadline=None)
@given(_release_sets())
def test_prune_keeps_exactly_the_three_newest_plus_current_and_previous(
    release_set: tuple[int, list[int], int, int],
) -> None:
    """Property: prune keeps the 3 newest plus `current` and `previous`, and nothing else."""
    count, ranks, current, previous = release_set
    names = [f"{index:012x}" for index in range(count)]
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        epoch = time.time() - 10_000
        for index, name in enumerate(names):
            release = root / "versions" / name
            release.mkdir(parents=True)
            os.utime(release, (epoch + ranks[index], epoch + ranks[index]))
        (root / "current").symlink_to(root / "versions" / names[current], target_is_directory=True)
        (root / "previous").symlink_to(
            root / "versions" / names[previous], target_is_directory=True
        )

        result = _prune(root, _SCRIPT_DIR / "install_release.sh")

        newest_three = sorted(names, key=lambda name: ranks[names.index(name)], reverse=True)[:3]
        expected = sorted({*newest_three, names[current], names[previous]})
        kept = sorted(path.name for path in (root / "versions").iterdir())
    assert result.returncode == 0, result.stderr
    assert kept == expected
