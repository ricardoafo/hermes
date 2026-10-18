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
import sqlite3
import subprocess
import sys
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
_SCHEMA_GUARD = "schema_guard.py"
_NOTIFY_SCRIPT = "hermes-notify-failure.sh"
_CRASH_SCRIPT = "hermes-notify-crash.sh"
_EXPERIMENT_DIR = Path("docs/experiments/swing-mvp1")
_BOOTSTRAP_SCRIPT = "vps_bootstrap.sh"
_UNIT = "hermes.service"
# One hour into a 4h bar: clear of every close, so no test holds by accident.
_QUIET_EPOCH = str(14400 * 1000 + 3600)
_TIMER = "hermes-daily.timer"

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
        case " ${STUB_BAD_UNITS:-} " in
            *" ${@: -1} "*) exit 3 ;;
        esac
        ;;
    daemon-reload)
        # The unit backup exists only between the unit sync and the end of
        # the deploy; record its mode while it is there.
        if [ -d "$HERMES_ROOT/units-before-deploy" ]; then
            /usr/bin/stat -c %a "$HERMES_ROOT/units-before-deploy" > "$STUB_STATE/unit-backup-mode"
        fi
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

# `runuser -u hermes -- <cmd>` drops privileges on the box; here it logs
# the call and runs <cmd> as the test user. On the box the staging dir is
# root-only (0700), so the service user cannot open a path inside it: an
# argument under STUB_RUNUSER_DENY fails the way python does there.
_STUB_RUNUSER = """\
#!/usr/bin/env bash
echo "runuser $*" >> "$STUB_LOG"
[ -z "${STUB_RUNUSER_FAIL:-}" ] || exit 1
for arg in "$@"; do
    if [ -n "${STUB_RUNUSER_DENY:-}" ] && [[ "$arg" == "$STUB_RUNUSER_DENY"/* ]]; then
        echo "can't open file '$arg': [Errno 13] Permission denied" >&2
        exit 2
    fi
done
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done
shift
exec "$@"
"""

# `hermes migrate`: `--status` prints STUB_MIGRATE_STATUS (default: nothing
# pending); a run logs its arguments and fails when STUB_MIGRATE_FAIL is set,
# or when its --db is STUB_MIGRATE_FAIL_DB. With STUB_MIGRATE_MARK set, a run
# rewrites the ledger's marker to "migrated", so a test can see a restore.
_STUB_HERMES = """#!/usr/bin/env bash
echo "hermes $*" >> "$STUB_LOG"
db=""
prev=""
for arg in "$@"; do
    [ "$prev" != "--db" ] || db=$arg
    prev=$arg
done
case " $* " in
    *" --status "*) echo "${STUB_MIGRATE_STATUS:-No pending migrations.}" ;;
    *)
        [ -z "${STUB_MIGRATE_FAIL:-}" ] || exit 1
        [ "$db" != "${STUB_MIGRATE_FAIL_DB:-}" ] || exit 1
        if [ -n "${STUB_MIGRATE_MARK:-}" ]; then
            "$HERMES_BACKUP_PYTHON" -c '
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
with conn:
    conn.execute("UPDATE marker SET value = ?", ("migrated",))
conn.close()
' "$db"
        fi
        ;;
esac
exit 0
"""

# `install` wraps the real one and records each call. On Git Bash over NTFS
# the real one cannot set some modes ("cannot change permissions"); only
# there, and only after it failed, the call is retried without -m/-o/-g and
# the dropped mode is recorded, so the test box runs and no test mistakes
# that for the 0700 the VPS gets (#683).
_STUB_INSTALL = """\
#!/usr/bin/env bash
echo "install $*" >> "$STUB_STATE/install.log"
/usr/bin/install "$@" 2>/dev/null && exit 0
case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*) ;;
    *) exec /usr/bin/install "$@" ;;
esac
args=()
skip=0
for arg in "$@"; do
    if [ "$skip" = 1 ]; then skip=0; continue; fi
    case "$arg" in
        -m | -o | -g) skip=1 ;;
        -m* | -o* | -g* | --mode=* | --owner=* | --group=*) ;;
        *) args+=("$arg") ;;
    esac
done
echo "mode dropped: install $*" >> "$STUB_STATE/install.log"
exec /usr/bin/install "${args[@]}"
"""

_STUBS = {
    "install": _STUB_INSTALL,
    "hermes": _STUB_HERMES,
    "runuser": _STUB_RUNUSER,
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
    def db(self) -> Path:
        """Return the scratch ledger path (absent until a test creates it)."""
        return self.base / "var-lib-hermes" / "hermes.sqlite3"

    @property
    def backups(self) -> Path:
        """Return the scratch ledger snapshot directory."""
        return self.base / "var-lib-hermes" / "backups"

    def write_ledger(self, value: str, path: Path | None = None) -> Path:
        """Create a one-row SQLite ledger holding ``value``."""
        path = path or self.db
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        conn = sqlite3.connect(path)
        try:
            with conn:
                conn.execute("CREATE TABLE marker (value TEXT)")
                conn.execute("INSERT INTO marker VALUES (?)", (value,))
        finally:
            conn.close()
        return path

    @staticmethod
    def ledger_value(path: Path) -> str:
        """Return the single marker value stored in a ledger file."""
        conn = sqlite3.connect(path)
        try:
            (value,) = conn.execute("SELECT value FROM marker").fetchone()
        finally:
            conn.close()
        return str(value)

    @property
    def units(self) -> Path:
        """Return the scratch stand-in for /etc/systemd/system."""
        return self.base / "etc-systemd-system"

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
        for directory in (self.root, self._state, self.units):
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
        (stage / "deploy" / _NOTIFY_SCRIPT).write_text(f"#!/bin/bash\n# {sha}\n", encoding="utf-8")
        (stage / "deploy" / _CRASH_SCRIPT).write_text(f"#!/bin/bash\n# {sha}\n", encoding="utf-8")
        (stage / "config" / "strategies.yaml").write_text(_STRATEGIES_YAML, encoding="utf-8")
        (stage / "deploy" / "systemd").mkdir()
        (stage / "deploy" / "systemd" / _UNIT).write_text(f"# {sha}\n", encoding="utf-8")
        (stage / "deploy" / "systemd" / _TIMER).write_text("# timer\n", encoding="utf-8")
        experiment = stage / _EXPERIMENT_DIR
        experiment.mkdir(parents=True)
        (experiment / "pre-registration-v1.md").write_text(f"# {sha}\n", encoding="utf-8")
        (experiment / "fee-history.csv").write_text("effective_date\n", encoding="utf-8")
        (stage / "requirements.txt").write_text(
            "pydantic==2.0 --hash=sha256:00\n", encoding="utf-8"
        )
        for index in range(wheels):
            (stage / f"hermes-0.0.{index}-py3-none-any.whl").write_bytes(b"wheel")
        for script in (*_RELEASE_SCRIPTS, _SCHEMA_GUARD):
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
                "HERMES_DB": _posix(self.db),
                "HERMES_BACKUP_DIR": _posix(self.backups),
                "HERMES_BACKUP_PYTHON": _posix(Path(sys.executable)),
                "HERMES_MIGRATE_BIN": _posix(self._stubs / "hermes"),
                "HERMES_UNIT_DIR": _posix(self.units),
                "HERMES_NOW_EPOCH": _QUIET_EPOCH,
                "HERMES_SCHEMA_PYTHON": _posix(Path(sys.executable)),
                "STUB_RUNUSER_DENY": _posix(self.base / "stages"),
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


def test_good_deploy_installs_the_failure_alert_outside_the_release(box: Box) -> None:
    """Invariant (#364): the alert runs from bin/, so a broken release cannot silence it."""
    box.deploy(_SHA_A)

    notifier = box.root / "bin" / _NOTIFY_SCRIPT
    assert os.access(notifier, os.X_OK) is True
    assert notifier.read_text(encoding="utf-8") == f"#!/bin/bash\n# {_SHA_A}\n"


def test_good_deploy_restarts_a_running_bot_on_the_new_release(box: Box) -> None:
    """Invariant (#379): the bot runs from `current`, so a good deploy try-restarts it."""
    box.deploy(_SHA_A)

    assert "systemctl try-restart hermes-bot.service" in box.calls()


def test_good_deploy_installs_the_crash_notice_outside_the_release(box: Box) -> None:
    """Invariant (#380): ExecStopPost= runs the crash notice from bin/, like the failure alert."""
    box.deploy(_SHA_A)

    notice = box.root / "bin" / _CRASH_SCRIPT
    assert os.access(notice, os.X_OK) is True
    assert notice.read_text(encoding="utf-8") == f"#!/bin/bash\n# {_SHA_A}\n"


def test_failed_gate_keeps_the_previous_failure_alert(box: Box) -> None:
    """Invariant (#364): a release that fails its gate never replaces the working alert script."""
    box.deploy(_SHA_A)

    box.deploy(_SHA_B, STUB_BAD_RELEASES=_SHA_B)

    notifier = box.root / "bin" / _NOTIFY_SCRIPT
    assert notifier.read_text(encoding="utf-8") == f"#!/bin/bash\n# {_SHA_A}\n"


def test_release_without_the_failure_alert_is_refused(box: Box) -> None:
    """Invariant (#364): a bundle without the alert script fails before `current` moves."""
    box.deploy(_SHA_A)
    stage = box.make_stage(_SHA_B)
    (stage / "deploy" / _NOTIFY_SCRIPT).unlink()

    result = box.deploy(_SHA_B, stage=stage)

    assert result.returncode != 0, _output(result)
    assert box.target("current") == _SHA_A


def test_release_carries_the_experiment_files_serve_reads(box: Box) -> None:
    """Invariant (#353 D-6): serve reads the pre-registration and fees from the release root."""
    box.deploy(_SHA_A)

    shipped = box.root / "versions" / _SHA_A / _EXPERIMENT_DIR
    assert sorted(p.name for p in shipped.iterdir()) == [
        "fee-history.csv",
        "pre-registration-v1.md",
    ]


@pytest.mark.parametrize("name", ["pre-registration-v1.md", "fee-history.csv"])
def test_release_without_an_experiment_file_is_refused(box: Box, name: str) -> None:
    """Invariant (#353 D-6): a bundle missing either file fails before `current` moves."""
    box.deploy(_SHA_A)
    stage = box.make_stage(_SHA_B)
    (stage / _EXPERIMENT_DIR / name).unlink()

    result = box.deploy(_SHA_B, stage=stage)

    assert result.returncode != 0, _output(result)
    assert box.target("current") == _SHA_A


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


def test_a_seventh_deploy_keeps_the_three_newest_releases(box: Box) -> None:
    """Invariant (A-3.2): old releases are pruned down to the 3 newest."""
    # Six earlier deploys are seeded rather than run: each deploy spawns dozens
    # of processes, and seven of them outran the timeout under Git Bash (#683).
    # The prune rule itself is covered by the property test below.
    shas = [f"{index:012x}" for index in range(1, 7)]
    epoch = time.time() - 10_000
    for index, sha in enumerate(shas):
        release = box.seed_release(sha)
        # Distinct, increasing mtimes so "newest" never depends on clock resolution.
        os.utime(release, (epoch + index, epoch + index))
    box.link("previous", shas[4])
    box.link("current", shas[5])

    result = box.deploy(f"{7:012x}")

    assert result.returncode == 0, _output(result)

    kept = sorted(path.name for path in (box.root / "versions").iterdir())
    assert kept == [f"{index:012x}" for index in (5, 6, 7)]


def _prune(
    root: Path, script: Path, *, proc: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Source ``install_release.sh`` and run only ``prune_versions`` against ``root``.

    ``proc`` stands in for /proc; by default an empty one, so no real process
    on the test host can keep a scratch release alive.
    """
    proc_dir = proc or root / "proc"
    proc_dir.mkdir(exist_ok=True)
    return run_bash(
        ["-c", 'source "$1" && prune_versions', "bash", _posix(script)],
        clean_env(
            PATH=os.environ["PATH"], HERMES_ROOT=_posix(root), HERMES_PROC_DIR=_posix(proc_dir)
        ),
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


def test_prune_keeps_a_release_a_running_process_still_maps(tmp_path: Path) -> None:
    """Invariant: a release an open console still runs from is never pruned (#652).

    The console keeps the release it started on; its extension modules stay
    mapped from that directory, which /proc/<pid>/maps shows by real path.
    """
    root = tmp_path / "opt-hermes"
    names = [f"{index:012x}" for index in range(6)]
    epoch = time.time() - 10_000
    for rank, name in enumerate(names):
        release = root / "versions" / name
        release.mkdir(parents=True)
        os.utime(release, (epoch + rank, epoch + rank))
    (root / "current").symlink_to(root / "versions" / names[5], target_is_directory=True)
    (root / "previous").symlink_to(root / "versions" / names[4], target_is_directory=True)
    held = root / "versions" / names[0]
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242" / "maps").write_text(
        f"7f00-7f01 r-xp 00000000 08:01 1 {_posix(held.resolve())}/.venv/lib/core.so\n",
        encoding="utf-8",
    )
    (proc / "self").mkdir()

    result = _prune(root, _SCRIPT_DIR / "install_release.sh", proc=proc)

    kept = sorted(path.name for path in (root / "versions").iterdir())
    assert result.returncode == 0, result.stderr
    assert kept == sorted([names[0], names[3], names[4], names[5]])
    assert f"keeping {_posix(held)}: process 4242 still runs from it" in result.stdout


def test_prune_is_not_fooled_by_a_release_whose_name_extends_another(tmp_path: Path) -> None:
    """Invariant: a process mapping release `abc1` does not keep release `abc` alive."""
    root = tmp_path / "opt-hermes"
    names = ["aaaaaaaaaaaa", "aaaaaaaaaaaab", "c0", "c1", "c2"]
    epoch = time.time() - 10_000
    for rank, name in enumerate(names):
        release = root / "versions" / name
        release.mkdir(parents=True)
        os.utime(release, (epoch + rank, epoch + rank))
    proc = tmp_path / "proc"
    (proc / "77").mkdir(parents=True)
    longer = (root / "versions" / names[1]).resolve()
    (proc / "77" / "maps").write_text(f"7f00 r-xp 0 0 1 {_posix(longer)}/x.so\n", encoding="utf-8")

    result = _prune(root, _SCRIPT_DIR / "install_release.sh", proc=proc)

    kept = sorted(path.name for path in (root / "versions").iterdir())
    assert result.returncode == 0, result.stderr
    assert kept == sorted(names[1:])


# ─────────────────────────────────────────────────────────────────────
# Ledger snapshot before activate, restore on rollback.
# ─────────────────────────────────────────────────────────────────────


def test_deploy_snapshots_the_ledger_before_the_restart(box: Box) -> None:
    """Invariant: the ledger is copied before the new release can migrate it.

    The snapshot holds the pre-deploy rows, and the `runuser` call that
    takes it comes before any `systemctl restart`.
    """
    box.write_ledger("before-deploy")

    result = box.deploy(_SHA_A)

    assert result.returncode == 0, _output(result)
    assert box.ledger_value(box.backups / f"pre-{_SHA_A}.sqlite3") == "before-deploy"
    calls = box.calls()
    backup_at = next(i for i, call in enumerate(calls) if call.startswith("runuser -u hermes"))
    restart_at = next(i for i, call in enumerate(calls) if call.startswith("systemctl restart"))
    assert backup_at < restart_at


def test_deploy_without_a_ledger_skips_the_snapshot(box: Box) -> None:
    """A first deploy has no ledger yet; it must go live without a snapshot."""
    result = box.deploy(_SHA_A)

    assert result.returncode == 0, _output(result)
    assert not box.backups.exists()


def test_failed_snapshot_stops_the_deploy_before_current_moves(box: Box) -> None:
    """Invariant: no snapshot, no deploy. `current` stays on the old release."""
    box.seed_release(_SHA_A)
    box.link("current", _SHA_A)
    box.write_ledger("live")

    result = box.deploy(_SHA_B, STUB_RUNUSER_FAIL="1")

    assert result.returncode == 1
    assert "ledger snapshot" in _output(result)
    assert box.target("current") == _SHA_A
    assert not any(call.startswith("systemctl restart") for call in box.calls())


def test_only_the_five_newest_snapshots_survive(box: Box) -> None:
    """Snapshots are pruned to the newest five; the two oldest go."""
    box.write_ledger("live")
    box.backups.mkdir(parents=True)
    for index in range(6):
        old = box.backups / f"pre-{index:012x}.sqlite3"
        old.write_bytes(b"old")
        os.utime(old, (1_000_000 + index, 1_000_000 + index))

    result = box.deploy(_SHA_A)

    assert result.returncode == 0, _output(result)
    survivors = sorted(path.name for path in box.backups.iterdir())
    assert survivors == sorted(
        [f"pre-{_SHA_A}.sqlite3", *(f"pre-{index:012x}.sqlite3" for index in range(2, 6))]
    )


def test_failed_gate_rolls_back_and_restores_the_snapshot(box: Box) -> None:
    """Invariant: a release that fails the gate leaves the ledger as it found it.

    The rollback runs with the snapshot this deploy took, so a schema the
    bad release migrated never meets the old code.
    """
    box.seed_release(_SHA_A)
    box.link("current", _SHA_A)
    box.write_ledger("before-deploy")

    result = box.deploy(_SHA_B, STUB_BAD_RELEASES=_SHA_B)

    assert result.returncode == 1
    assert box.target("current") == _SHA_A
    assert "restored ledger" in _output(result)
    assert box.ledger_value(box.db) == "before-deploy"


def test_rollback_with_a_snapshot_stops_restores_then_restarts(box: Box) -> None:
    """Invariant: the copy happens with the unit stopped, and stale WAL files go with it."""
    box.seed_release(_SHA_A)
    box.seed_release(_SHA_B)
    box.link("previous", _SHA_A)
    box.link("current", _SHA_B)
    box.write_ledger("migrated")
    snapshot = box.write_ledger("pre-migration", box.backups / f"pre-{_SHA_B}.sqlite3")
    wal = Path(f"{box.db}-wal")
    wal.write_bytes(b"stale")

    result = box.rollback(HERMES_RESTORE_DB=_posix(snapshot))

    assert result.returncode == 0, _output(result)
    assert box.ledger_value(box.db) == "pre-migration"
    assert not wal.exists()
    stop_at, restart_at = _systemctl_order(box.calls(), "stop", "restart")
    assert stop_at < restart_at


def test_rollback_refuses_a_missing_snapshot(box: Box) -> None:
    """A named snapshot that is not there stops the rollback before anything moves."""
    box.seed_release(_SHA_A)
    box.seed_release(_SHA_B)
    box.link("previous", _SHA_A)
    box.link("current", _SHA_B)

    result = box.rollback(HERMES_RESTORE_DB=_posix(box.backups / "missing.sqlite3"))

    assert result.returncode == 1
    assert box.target("current") == _SHA_B


def test_rollback_without_a_snapshot_leaves_the_ledger_alone(box: Box) -> None:
    """A manual rollback with no snapshot named must not touch the ledger."""
    box.seed_release(_SHA_A)
    box.seed_release(_SHA_B)
    box.link("previous", _SHA_A)
    box.link("current", _SHA_B)
    box.write_ledger("untouched")

    result = box.rollback()

    assert result.returncode == 0, _output(result)
    assert box.ledger_value(box.db) == "untouched"
    assert not any(call.startswith("systemctl stop") for call in box.calls())


_DESTRUCTIVE_PENDING = "pending: 0005_lots_drop_strategy_default.sql (destructive)"


def test_destructive_migration_runs_through_hermes_migrate_before_the_restart(box: Box) -> None:
    """#474: the deploy runs a pending destructive migration after the snapshot, unit stopped."""
    box.seed_release(_SHA_A)
    box.link("current", _SHA_A)
    box.write_ledger("before")

    result = box.deploy(_SHA_B, STUB_MIGRATE_STATUS=_DESTRUCTIVE_PENDING)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = box.calls()
    migrate = next(
        i
        for i, c in enumerate(calls)
        if c.startswith("hermes migrate --db") and "--snapshot-dir" in c
    )
    stop, restart = _systemctl_order(calls, "stop", "restart")
    assert stop < migrate < restart
    assert (box.backups / f"pre-{_SHA_B}.sqlite3").exists()
    assert box.target("current") == _SHA_B


def test_no_destructive_migration_means_no_stop_and_no_migrate_run(box: Box) -> None:
    """Without a destructive migration the deploy only asks for the status, as before."""
    box.seed_release(_SHA_A)
    box.link("current", _SHA_A)
    box.write_ledger("before")

    result = box.deploy(_SHA_B)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = box.calls()
    assert [c for c in calls if c.startswith("hermes migrate")] == [
        f"hermes migrate --db {_posix(box.db)} --status"
    ]
    assert not any(c.split()[:2] == ["systemctl", "stop"] for c in calls)


def test_failed_migration_keeps_the_old_release_running_and_exits_1(box: Box) -> None:
    """A failed `hermes migrate` leaves current on the old release and starts its unit again."""
    box.seed_release(_SHA_A)
    box.link("current", _SHA_A)
    box.write_ledger("before")

    result = box.deploy(_SHA_B, STUB_MIGRATE_STATUS=_DESTRUCTIVE_PENDING, STUB_MIGRATE_FAIL="1")

    assert result.returncode == 1
    assert "hermes migrate failed" in result.stdout + result.stderr
    assert box.target("current") == _SHA_A
    assert any(c.split()[:2] == ["systemctl", "start"] for c in box.calls())


def test_first_deploy_without_a_ledger_skips_the_migrate_check(box: Box) -> None:
    """No ledger yet means nothing to migrate: serve creates it with every migration."""
    result = box.deploy(_SHA_A)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(c.startswith("hermes migrate") for c in box.calls())


# ─────────────────────────────────────────────────────────────────────
# Staging target (#192): HERMES_TARGET=staging never reaches prod.
# openspec/changes/192-staging-env/change.md, D-3 and R-2.
# ─────────────────────────────────────────────────────────────────────

_STAGING = {"HERMES_TARGET": "staging"}
_STAGING_UNIT = "hermes-staging.service"
_STAGING_ENV_FILE = 'HERMES_ENV="staging"\nHERMES_ALLOW_MAINNET="no"\n'


@pytest.fixture
def staging_box(box: Box) -> Box:
    """Return the scratch box with an env file that names the staging tier."""
    box.env_file.write_text(_STAGING_ENV_FILE, encoding="utf-8", newline="\n")
    return box


def _units_named(calls: list[str]) -> set[str]:
    """Return every ``*.service`` argument that systemctl or journalctl was called with."""
    return {
        word
        for call in calls
        if call.split()[0] in {"systemctl", "journalctl"}
        for word in call.split()[1:]
        if word.endswith(".service")
    }


def _files_and_bytes(base: Path) -> dict[str, bytes]:
    """Return every regular file under ``base`` with its content, keyed by relative path."""
    return {
        str(path.relative_to(base)): path.read_bytes()
        for path in base.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_staging_deploy_writes_only_under_the_staging_root_and_backups(staging_box: Box) -> None:
    """A-2.1: every file a staging deploy adds or changes lies in its root or its backups."""
    staging_box.write_ledger("staging-ledger")
    stage = staging_box.make_stage(_SHA_A)
    before = _files_and_bytes(staging_box.base)

    result = staging_box.deploy(_SHA_A, stage=stage, **_STAGING)

    assert result.returncode == 0, _output(result)
    after = _files_and_bytes(staging_box.base)
    changed = {path for path, data in after.items() if before.get(path) != data}
    # The stubs' own call log and state are the test harness, not the deploy.
    allowed = (staging_box.root, staging_box.backups, staging_box.log, staging_box._state)
    stray = sorted(
        path
        for path in changed
        if not any(
            staging_box.base / path == base or base in (staging_box.base / path).parents
            for base in allowed
        )
    )
    assert stray == []


def test_staging_deploy_restarts_only_the_staging_unit(staging_box: Box) -> None:
    """A-2.1: a staging deploy names hermes-staging.service and never prod's unit."""
    result = staging_box.deploy(_SHA_A, **_STAGING)

    assert result.returncode == 0, _output(result)
    assert f"systemctl restart {_STAGING_UNIT}" in staging_box.calls()
    assert _units_named(staging_box.calls()) == {_STAGING_UNIT}


def test_staging_deploy_takes_the_snapshot_as_the_staging_user(staging_box: Box) -> None:
    """A-2.1: the ledger snapshot and migrate check run as hermes-staging, never as hermes."""
    staging_box.write_ledger("staging-ledger")

    result = staging_box.deploy(_SHA_A, **_STAGING)

    assert result.returncode == 0, _output(result)
    users = {call.split()[2] for call in staging_box.calls() if call.startswith("runuser -u")}
    assert users == {"hermes-staging"}


def test_staging_failed_gate_rolls_back_the_staging_unit_only(staging_box: Box) -> None:
    """The automatic rollback inherits the target: it restarts and gates staging, not prod."""
    staging_box.deploy(_SHA_A, **_STAGING)

    result = staging_box.deploy(_SHA_B, STUB_BAD_RELEASES=_SHA_B, **_STAGING)

    assert result.returncode == 1, _output(result)
    assert staging_box.target("current") == _SHA_A
    assert _units_named(staging_box.calls()) == {_STAGING_UNIT}


@pytest.mark.parametrize(
    "env_text",
    [
        'HERMES_ENV="prod"\n',
        "HERMES_ENV=prod\n",
        "HERMES_ALLOW_MAINNET=no\n",
        'HERMES_ENV="staging"\nHERMES_ENV="prod"\n',
        "# HERMES_ENV=staging\nHERMES_ENV=sandbox\n",
        "HERMES_ENV=STAGING\n",
    ],
    ids=["quoted-prod", "bare-prod", "unset", "last-line-prod", "commented-staging", "uppercase"],
)
def test_staging_preflight_refuses_an_env_file_that_is_not_staging(
    staging_box: Box, env_text: str
) -> None:
    """A-2.2: a staging run whose env file would boot another tier changes nothing."""
    staging_box.env_file.write_text(env_text, encoding="utf-8", newline="\n")
    stage = staging_box.make_stage(_SHA_A)
    before = staging_box.disk()

    result = staging_box.deploy(_SHA_A, stage=stage, **_STAGING)

    assert result.returncode == 1, _output(result)
    assert "expected 'staging'" in result.stdout
    assert staging_box.disk() == before
    assert not any(call.startswith("systemctl restart") for call in staging_box.calls())


@pytest.mark.parametrize(
    "env_text",
    ["HERMES_ENV=staging\n", "HERMES_ENV='staging'\n", '  HERMES_ENV = "staging"\n'],
    ids=["bare", "single-quoted", "spaced"],
)
def test_staging_preflight_accepts_every_spelling_of_the_staging_tier(
    staging_box: Box, env_text: str
) -> None:
    """The tier check reads the env file the way systemd does, quoted or not."""
    staging_box.env_file.write_text(env_text, encoding="utf-8", newline="\n")

    result = staging_box.deploy(_SHA_A, **_STAGING)

    assert result.returncode == 0, _output(result)


@pytest.mark.parametrize(
    ("seam", "prod_value"),
    [
        ("HERMES_ROOT", "/opt/hermes"),
        ("HERMES_ROOT", "/opt/hermes-staging/../hermes"),
        ("HERMES_ROOT", "/opt/hermes/versions"),
        ("HERMES_DB", "/var/lib/hermes/hermes.sqlite3"),
        ("HERMES_BACKUP_DIR", "/var/lib/hermes/backups"),
        ("HERMES_ENV_FILE", "/etc/hermes/env.production"),
    ],
)
def test_staging_deploy_refuses_any_path_that_resolves_to_prod(
    staging_box: Box, seam: str, prod_value: str
) -> None:
    """D-3: no seam or typo lets a staging deploy write prod's tree or read prod's secrets.

    The guard runs before every other check, so not even `systemctl cat` is called.
    """
    stage = staging_box.make_stage(_SHA_A)
    before = staging_box.disk()

    result = staging_box.deploy(_SHA_A, stage=stage, **_STAGING, **{seam: prod_value})

    assert result.returncode == 1, _output(result)
    assert "refusing" in result.stdout
    assert staging_box.disk() == before
    assert staging_box.calls() == []


@pytest.mark.parametrize("script", ["install_release.sh", "rollback.sh", "health_gate.sh"])
def test_every_release_script_refuses_an_unknown_target(box: Box, script: str) -> None:
    """A-2.2: a mistyped target is refused outright, never mapped to prod by default."""
    _two_releases(box)
    stage = box.make_stage(_SHA_C)
    args = {
        "install_release.sh": [_posix(stage / "bin" / script), _SHA_C, _posix(stage)],
        "rollback.sh": [_posix(_SCRIPT_DIR / script)],
        "health_gate.sh": [_posix(_SCRIPT_DIR / script), "0"],
    }[script]
    before = box.disk()

    result = box.run(args, HERMES_TARGET="production")

    assert result.returncode == 1, _output(result)
    assert "unknown HERMES_TARGET 'production'" in result.stdout
    assert box.disk() == before
    assert box.calls() == []


def test_staging_rollback_restarts_and_gates_only_the_staging_unit(box: Box) -> None:
    """A manual staging rollback flips staging's `current` and touches no prod unit."""
    _two_releases(box)

    result = box.rollback(**_STAGING)

    assert result.returncode == 0, _output(result)
    assert box.target("current") == _SHA_A
    assert f"systemctl restart {_STAGING_UNIT}" in box.calls()
    assert _units_named(box.calls()) == {_STAGING_UNIT}


@pytest.mark.parametrize(
    ("seam", "prod_value"),
    [("HERMES_ROOT", "/opt/hermes"), ("HERMES_DB", "/var/lib/hermes/hermes.sqlite3")],
)
def test_staging_rollback_refuses_any_path_that_resolves_to_prod(
    box: Box, seam: str, prod_value: str
) -> None:
    """D-3: a staging rollback pointed at prod's root or ledger stops before it acts."""
    _two_releases(box)

    result = box.rollback(**_STAGING, **{seam: prod_value})

    assert result.returncode == 1, _output(result)
    assert "refusing" in result.stdout
    assert box.calls() == []


def test_staging_gate_watches_only_the_staging_unit(box: Box) -> None:
    """The staging gate reads hermes-staging.service's state and journal, never prod's."""
    result = box.gate(**_STAGING)

    assert result.returncode == 0, _output(result)
    assert _units_named(box.calls()) == {_STAGING_UNIT}


# ─────────────────────────────────────────────────────────────────────
# Unit sync (#626): a deploy installs the release's changed units and
# timers, and a failed gate puts the old files back.
# ─────────────────────────────────────────────────────────────────────


def test_good_deploy_installs_the_release_units(box: Box) -> None:
    """Invariant: a unit change ships with its release, no bootstrap run needed."""
    box.deploy(_SHA_A)

    result = box.deploy(_SHA_B)

    assert result.returncode == 0, _output(result)
    assert (box.units / _UNIT).read_text(encoding="utf-8") == f"# {_SHA_B}\n"
    assert (box.units / _TIMER).read_text(encoding="utf-8") == "# timer\n"


def test_unit_install_comes_before_the_daemon_reload_and_restart(box: Box) -> None:
    """Invariant: systemd reloads after the files change, so the restart runs the new unit."""
    result = box.deploy(_SHA_A)

    out = result.stdout
    assert out.index(f"unit {_UNIT}: installed") < out.index("current -> ")
    assert "systemctl daemon-reload" in box.calls()


def test_unit_backup_is_private_to_root_while_the_deploy_runs(box: Box) -> None:
    """Invariant: the copies of the old unit files sit in a 0700 directory (#683).

    The mode is read while the deploy runs, at the daemon-reload. Only Git
    Bash over NTFS may refuse it, and then the install stub says it dropped
    the mode; on Linux, as on the VPS, the directory must really be 0700.
    """
    result = box.deploy(_SHA_A)

    install_log = (box._state / "install.log").read_text(encoding="utf-8")
    backup = _posix(box.root / "units-before-deploy")
    assert result.returncode == 0, _output(result)
    assert f"install -d -m 0700 {backup}" in install_log
    if f"mode dropped: install -d -m 0700 {backup}" not in install_log:
        assert (box._state / "unit-backup-mode").read_text(encoding="utf-8").strip() == "700"


def test_unchanged_units_are_left_alone(box: Box) -> None:
    """Invariant: a redeploy with the same unit content rewrites nothing in the unit dir."""
    box.deploy(_SHA_A)

    result = box.deploy(_SHA_A)

    assert result.returncode == 0, _output(result)
    assert f"unit {_UNIT}: installed" not in result.stdout


def test_failed_gate_restores_the_previous_unit(box: Box) -> None:
    """Invariant: after a rollback the old release runs under the unit it was built for."""
    box.deploy(_SHA_A)

    result = box.deploy(_SHA_B, STUB_BAD_RELEASES=_SHA_B)

    assert result.returncode == 1, _output(result)
    assert (box.units / _UNIT).read_text(encoding="utf-8") == f"# {_SHA_A}\n"


def test_failed_gate_removes_a_unit_the_release_added(box: Box) -> None:
    """Invariant: a unit that only the failed release carried does not stay behind."""
    box.deploy(_SHA_A)
    stage = box.make_stage(_SHA_B)
    (stage / "deploy" / "systemd" / "hermes-new.timer").write_text("# new\n", encoding="utf-8")

    result = box.deploy(_SHA_B, stage=stage, STUB_BAD_RELEASES=_SHA_B)

    assert result.returncode == 1, _output(result)
    assert not (box.units / "hermes-new.timer").exists()


def test_new_unit_is_flagged_for_bootstrap_to_enable(box: Box) -> None:
    """Invariant: a deploy installs a new unit but leaves enabling it to vps-bootstrap."""
    stage = box.make_stage(_SHA_A)
    (stage / "deploy" / "systemd" / "hermes-new.timer").write_text("# new\n", encoding="utf-8")

    result = box.deploy(_SHA_A, stage=stage)

    assert result.returncode == 0, _output(result)
    assert "::warning::new unit hermes-new.timer installed but not enabled" in result.stdout
    assert not [call for call in box.calls() if call.startswith("systemctl enable")]


def test_good_deploy_leaves_no_unit_backup_behind(box: Box) -> None:
    """Invariant: the replaced unit copies live only for the length of one deploy."""
    box.deploy(_SHA_A)
    box.deploy(_SHA_B)

    assert not (box.root / "units-before-deploy").exists()


def test_release_without_units_deploys_and_leaves_the_unit_dir_alone(box: Box) -> None:
    """Invariant: a bundle built before #626 still deploys; its units stay as they are."""
    (box.units / _UNIT).write_text("# installed by bootstrap\n", encoding="utf-8")
    stage = box.make_stage(_SHA_A)
    shutil.rmtree(stage / "deploy" / "systemd")

    result = box.deploy(_SHA_A, stage=stage)

    assert result.returncode == 0, _output(result)
    assert (box.units / _UNIT).read_text(encoding="utf-8") == "# installed by bootstrap\n"


def test_staging_deploy_never_touches_the_unit_dir(staging_box: Box) -> None:
    """Invariant: staging shares prod's unit dir, so only a prod deploy syncs units."""
    result = staging_box.deploy(_SHA_A, **_STAGING)

    assert result.returncode == 0, _output(result)
    assert list(staging_box.units.iterdir()) == []


# ─────────────────────────────────────────────────────────────────────
# Quiet bar (#626): the unit is never stopped from 5 minutes before a 4h
# bar close until 5 minutes after it.
# ─────────────────────────────────────────────────────────────────────

_BAR = 14400


@pytest.mark.parametrize(
    ("into_bar", "held"),
    [
        (3600, None),
        (_BAR - 301, None),
        (_BAR - 300, 600),
        (_BAR - 10, 310),
        (0, 300),
        (299, 1),
        (300, None),
    ],
)
def test_prod_deploy_holds_around_the_bar_close(box: Box, into_bar: int, held: int | None) -> None:
    """Invariant: a restart never lands within 5 minutes of a 4h close, before or after."""
    result = box.deploy(_SHA_A, HERMES_NOW_EPOCH=str(_BAR * 1000 + into_bar))

    assert result.returncode == 0, _output(result)
    if held is None:
        assert "holding" not in result.stdout
    else:
        assert f"holding {held}s for the 4h bar close" in result.stdout


def test_hold_comes_before_the_unit_is_stopped_or_restarted(box: Box) -> None:
    """Invariant: the wait sits right before the restart, not minutes earlier in CI."""
    result = box.deploy(_SHA_A, HERMES_NOW_EPOCH=str(_BAR * 1000 - 10))

    out = result.stdout
    assert out.index("holding") < out.index("current -> ")


def test_staging_deploy_never_holds_for_the_bar(staging_box: Box) -> None:
    """Invariant: staging trades nothing, so its deploys do not wait on prod's bars."""
    result = staging_box.deploy(_SHA_A, HERMES_NOW_EPOCH=str(_BAR * 1000 - 10), **_STAGING)

    assert result.returncode == 0, _output(result)
    assert "holding" not in result.stdout


# Plain `hermes` wrapper for the operator (#610).
_CONSOLE_SCRIPT = "hermes-console.sh"


def _stage_with_console(box: Box, sha: str) -> Path:
    stage = box.make_stage(sha)
    (stage / "deploy" / _CONSOLE_SCRIPT).write_text(f"#!/bin/bash\n# {sha}\n", encoding="utf-8")
    return stage


def test_deploy_installs_the_console_wrapper_as_hermes(box: Box, tmp_path: Path) -> None:
    """Invariant: a prod deploy puts the release's console wrapper on PATH as `hermes`."""
    bin_dir = tmp_path / "usr-local-bin"
    bin_dir.mkdir()
    result = box.deploy(
        _SHA_A,
        stage=_stage_with_console(box, _SHA_A),
        HERMES_CONSOLE_BIN_DIR=_posix(bin_dir),
    )

    assert result.returncode == 0, _output(result)
    installed = bin_dir / "hermes"
    assert installed.read_text(encoding="utf-8") == f"#!/bin/bash\n# {_SHA_A}\n"
    if sys.platform != "win32":  # NTFS stores no POSIX modes
        assert installed.stat().st_mode & 0o111


def test_deploy_without_a_console_script_installs_no_wrapper(box: Box, tmp_path: Path) -> None:
    """Invariant: a stage that lacks the wrapper still deploys and writes nothing to PATH."""
    bin_dir = tmp_path / "usr-local-bin"
    bin_dir.mkdir()
    result = box.deploy(_SHA_A, HERMES_CONSOLE_BIN_DIR=_posix(bin_dir))

    assert result.returncode == 0, _output(result)
    assert list(bin_dir.iterdir()) == []


def test_staging_deploy_never_installs_the_console_wrapper(box: Box, tmp_path: Path) -> None:
    """Invariant: the wrapper points at prod's release, so a staging deploy leaves it alone."""
    bin_dir = tmp_path / "usr-local-bin"
    bin_dir.mkdir()
    box.env_file.write_text(_STAGING_ENV_FILE, encoding="utf-8", newline="\n")
    result = box.deploy(
        _SHA_A,
        stage=_stage_with_console(box, _SHA_A),
        HERMES_CONSOLE_BIN_DIR=_posix(bin_dir),
        **_STAGING,
    )

    assert result.returncode == 0, _output(result)
    assert list(bin_dir.iterdir()) == []


# ─────────────────────────────────────────────────────────────────────
# Schema guard (#655): never put live a release that cannot read the
# ledger's events.
# ─────────────────────────────────────────────────────────────────────


def _write_events(box: Box, version: int) -> None:
    """Add an events table holding one FillQualityRecorded row at ``version``."""
    conn = sqlite3.connect(box.db)
    with conn:
        conn.execute("CREATE TABLE events (event_type TEXT, event_schema_version INTEGER)")
        conn.execute("INSERT INTO events VALUES ('FillQualityRecorded', ?)", (version,))
    conn.close()


def test_deploy_of_a_release_older_than_the_ledger_is_refused(box: Box) -> None:
    """Invariant: an old commit deployed after newer events exist never goes live."""
    box.deploy(_SHA_A)
    box.write_ledger("live")
    _write_events(box, 999)

    result = box.deploy(_SHA_B)

    assert result.returncode == 1, _output(result)
    assert "cannot read the events" in result.stdout
    assert box.target("current") == _SHA_A


def test_deploy_runs_the_schema_guard_although_the_service_user_cannot_open_the_stage(
    box: Box,
) -> None:
    """Invariant: a current ledger deploys; the guard never needs a path in the root-only stage.

    deploy 4bbcb6106736 failed on hermes-01 because the service user could
    not open bin/schema_guard.py inside the 0700 staging dir (#655).
    """
    box.deploy(_SHA_A)
    box.write_ledger("live")
    _write_events(box, 1)

    result = box.deploy(_SHA_B)

    assert result.returncode == 0, _output(result)
    assert box.target("current") == _SHA_B


def test_a_guard_that_cannot_run_is_reported_as_such(box: Box) -> None:
    """Invariant: a crashed guard still stops the deploy, and says it crashed, not refused."""
    box.deploy(_SHA_A)
    box.write_ledger("live")
    _write_events(box, 1)

    result = box.deploy(_SHA_B, HERMES_SCHEMA_PYTHON=_posix(box.base / "no-such-python"))

    assert result.returncode == 1, _output(result)
    assert "the schema guard failed to run" in result.stdout
    assert "cannot read the events" not in result.stdout
    assert box.target("current") == _SHA_A


def test_a_guard_that_raises_is_reported_as_a_crash_not_a_refusal(box: Box) -> None:
    """Invariant: an uncaught exception (exit 1) is not read as "the ledger is too new"."""
    box.deploy(_SHA_A)
    box.write_ledger("live")
    _write_events(box, 1)
    raising = box.base / "raising-python"
    raising.write_text(
        "#!/usr/bin/env bash\necho Traceback >&2\nexit 1\n", encoding="utf-8", newline="\n"
    )
    raising.chmod(0o755)

    result = box.deploy(_SHA_B, HERMES_SCHEMA_PYTHON=_posix(raising))

    assert result.returncode == 1, _output(result)
    assert "the schema guard failed to run (exit 1)" in result.stdout
    assert "cannot read the events" not in result.stdout
    assert box.target("current") == _SHA_A


def test_rollback_onto_a_release_older_than_the_ledger_is_refused(box: Box) -> None:
    """Invariant: without a snapshot, rollback refuses a previous that would crash-loop."""
    box.deploy(_SHA_A)
    box.deploy(_SHA_B)
    box.write_ledger("live")
    _write_events(box, 999)

    result = box.rollback()

    assert result.returncode == 1, _output(result)
    assert "HERMES_RESTORE_DB" in result.stdout
    assert box.target("current") == _SHA_B


def test_rollback_with_a_snapshot_skips_the_schema_guard(box: Box) -> None:
    """Invariant: restoring the matching snapshot is the safe way back, so it is allowed."""
    box.deploy(_SHA_A)
    box.deploy(_SHA_B)
    snapshot = box.write_ledger("old", box.base / "snapshot.sqlite3")
    box.write_ledger("live")
    _write_events(box, 999)

    result = box.rollback(HERMES_RESTORE_DB=_posix(snapshot))

    assert result.returncode == 0, _output(result)
    assert box.target("current") == _SHA_A


def test_rollback_override_skips_the_schema_guard(box: Box) -> None:
    """Invariant: the operator can still force a rollback, by name."""
    box.deploy(_SHA_A)
    box.deploy(_SHA_B)
    box.write_ledger("live")
    _write_events(box, 999)

    result = box.rollback(HERMES_SKIP_SCHEMA_GUARD="1")

    assert result.returncode == 0, _output(result)
    assert box.target("current") == _SHA_A


def test_good_deploy_installs_the_schema_guard_for_manual_rollbacks(box: Box) -> None:
    """Invariant: /opt/hermes/bin/rollback.sh finds the guard next to it."""
    box.deploy(_SHA_A)

    assert (box.root / "bin" / _SCHEMA_GUARD).is_file()


# ─────────────────────────────────────────────────────────────────────
# Lab shadows (#1150): a staging release reaches every enabled
# hermes-shadow@ instance, each on its own ledger (#1076, ADR-024 §6).
# ─────────────────────────────────────────────────────────────────────

_SHADOWS = ("swing-a", "swing-b")


def _shadow_unit(name: str) -> str:
    return f"hermes-shadow@{name}.service"


def _shadow_db(box: Box, name: str) -> Path:
    return box.base / "var-lib-hermes-staging" / "shadows" / name / "hermes.sqlite3"


def _shadow_env(box: Box, **extra: str) -> dict[str, str]:
    """Return the staging switches plus the scratch stand-in for the shadows' state dir."""
    return {
        **_STAGING,
        "HERMES_SHADOWS_DIR": _posix(box.base / "var-lib-hermes-staging" / "shadows"),
        **extra,
    }


def _enable_shadows(box: Box, *names: str) -> None:
    """Enable each instance as `systemctl enable` does: a link in multi-user.target.wants."""
    wants = box.units / "multi-user.target.wants"
    wants.mkdir(parents=True, exist_ok=True)
    for name in names:
        (wants / _shadow_unit(name)).symlink_to(box.units / "hermes-shadow@.service")
        box.write_ledger(f"{name}-before", _shadow_db(box, name))


@pytest.fixture
def lab_box(staging_box: Box) -> Box:
    """Return a staging box with release A live and two enabled shadows on their own ledgers."""
    result = staging_box.deploy(_SHA_A, **_shadow_env(staging_box))
    assert result.returncode == 0, _output(result)
    staging_box.write_ledger("staging-before")
    _enable_shadows(staging_box, *_SHADOWS)
    staging_box.log.unlink()
    return staging_box


def test_staging_deploy_snapshots_every_shadow_ledger_beside_it(lab_box: Box) -> None:
    """#1150: each shadow's ledger gets its own pre-<sha> snapshot in shadows/<name>/backups."""
    result = lab_box.deploy(_SHA_B, **_shadow_env(lab_box))

    assert result.returncode == 0, _output(result)
    for name in _SHADOWS:
        snapshot = _shadow_db(lab_box, name).parent / "backups" / f"pre-{_SHA_B}.sqlite3"
        assert Box.ledger_value(snapshot) == f"{name}-before"


def test_staging_deploy_restarts_and_gates_every_shadow_after_the_flip(lab_box: Box) -> None:
    """#1150: every enabled shadow restarts on the new release and passes its own gate."""
    result = lab_box.deploy(_SHA_B, **_shadow_env(lab_box))

    assert result.returncode == 0, _output(result)
    calls = lab_box.calls()
    for name in _SHADOWS:
        unit = _shadow_unit(name)
        assert f"systemctl restart {unit}" in calls
        assert any(c.startswith(f"journalctl -u {unit} ") for c in calls)
    assert lab_box.target("current") == _SHA_B


def test_staging_deploy_migrates_every_shadow_ledger_with_its_instance_stopped(
    lab_box: Box,
) -> None:
    """#1150: a destructive migration reaches each shadow ledger, its instance stopped first."""
    result = lab_box.deploy(
        _SHA_B, STUB_MIGRATE_STATUS=_DESTRUCTIVE_PENDING, **_shadow_env(lab_box)
    )

    assert result.returncode == 0, _output(result)
    calls = lab_box.calls()
    for name in _SHADOWS:
        db = _posix(_shadow_db(lab_box, name))
        stop = calls.index(f"systemctl stop {_shadow_unit(name)}")
        migrate = next(
            i for i, c in enumerate(calls) if c.startswith(f"hermes migrate --db {db} --snapshot")
        )
        restart = calls.index(f"systemctl restart {_shadow_unit(name)}")
        assert stop < migrate < restart


def test_a_failed_gate_on_one_shadow_rolls_back_every_instance(lab_box: Box) -> None:
    """#1150 Done-when: one shadow failing its gate puts the release and both ledgers back."""
    result = lab_box.deploy(
        _SHA_B,
        STUB_MIGRATE_STATUS=_DESTRUCTIVE_PENDING,
        STUB_MIGRATE_MARK="1",
        STUB_BAD_UNITS=_shadow_unit("swing-b"),
        **_shadow_env(lab_box),
    )

    assert result.returncode == 1, _output(result)
    assert lab_box.target("current") == _SHA_A
    values = {name: Box.ledger_value(_shadow_db(lab_box, name)) for name in _SHADOWS}
    assert values == {name: f"{name}-before" for name in _SHADOWS}
    assert Box.ledger_value(lab_box.db) == "staging-before"
    restarts = {
        name: lab_box.calls().count(f"systemctl restart {_shadow_unit(name)}") for name in _SHADOWS
    }
    assert restarts == {name: 2 for name in _SHADOWS}


def test_a_failed_shadow_migration_restores_the_ledgers_already_migrated(lab_box: Box) -> None:
    """#1150: the old release never meets a ledger migrated by a deploy that then stopped."""
    result = lab_box.deploy(
        _SHA_B,
        STUB_MIGRATE_STATUS=_DESTRUCTIVE_PENDING,
        STUB_MIGRATE_MARK="1",
        STUB_MIGRATE_FAIL_DB=_posix(_shadow_db(lab_box, "swing-b")),
        **_shadow_env(lab_box),
    )

    assert result.returncode == 1, _output(result)
    assert "hermes migrate failed" in result.stdout
    assert lab_box.target("current") == _SHA_A
    assert Box.ledger_value(lab_box.db) == "staging-before"
    assert Box.ledger_value(_shadow_db(lab_box, "swing-a")) == "swing-a-before"
    started = {c.split()[2] for c in lab_box.calls() if c.startswith("systemctl start ")}
    assert started == {_STAGING_UNIT, *(_shadow_unit(n) for n in _SHADOWS)}


def test_staging_deploy_with_shadows_holds_around_the_bar_close(lab_box: Box) -> None:
    """#1150: a shadow restarted inside a bar close would miss that bar, so the deploy waits."""
    result = lab_box.deploy(_SHA_B, HERMES_NOW_EPOCH=str(_BAR * 1000 - 10), **_shadow_env(lab_box))

    assert result.returncode == 0, _output(result)
    assert "holding 310s for the 4h bar close" in result.stdout


@pytest.mark.parametrize("name", ["..", "Swing", "a\\x2fb", "-x", "a_b"])
def test_staging_deploy_refuses_a_shadow_name_that_is_not_a_plain_name(
    staging_box: Box, name: str
) -> None:
    """#1150: an instance name becomes a path, so anything but a plain name changes nothing."""
    wants = staging_box.units / "multi-user.target.wants"
    wants.mkdir(parents=True)
    (wants / f"hermes-shadow@{name}.service").touch()
    stage = staging_box.make_stage(_SHA_A)
    before = staging_box.disk()

    result = staging_box.deploy(_SHA_A, stage=stage, **_shadow_env(staging_box))

    assert result.returncode == 1, _output(result)
    assert "not a plain shadow name" in result.stdout
    assert staging_box.disk() == before


def test_prod_deploy_never_touches_a_shadow(box: Box) -> None:
    """#1150: shadows run from staging's release, so a prod deploy leaves them alone."""
    _enable_shadows(box, *_SHADOWS)

    result = box.deploy(_SHA_A, **{"HERMES_SHADOWS_DIR": _posix(box.base / "shadows")})

    assert result.returncode == 0, _output(result)
    assert not any("hermes-shadow@" in c for c in box.calls())


def test_staging_deploy_refuses_a_shadows_dir_inside_prods_state(staging_box: Box) -> None:
    """#1150: the staging guard covers the shadows' directory like every other write path."""
    result = staging_box.deploy(
        _SHA_A, **{**_shadow_env(staging_box), "HERMES_SHADOWS_DIR": "/var/lib/hermes/shadows"}
    )

    assert result.returncode == 1, _output(result)
    assert "inside prod's" in result.stdout
