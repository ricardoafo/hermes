"""``deploy/hermes-console.sh``: the plain `hermes` the operator types on the VPS (#610).

The script is the read-only door to the release: it must run the console
commands as the service user with the service env, and refuse everything that
could write. The real script runs under bash; ``runuser`` and the
release binary are stand-ins, and the production paths are rewritten to a
scratch directory in a copy.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hermes.entrypoints import console as console_module
from hermes.entrypoints import vps as vps_module

_SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "hermes-console.sh"
_BASH = os.environ.get("HERMES_TEST_BASH") or shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="the wrapper needs bash")

_STUB_RUNUSER = (
    "#!/usr/bin/env bash\n"
    'echo "runuser $*"\n'
    'echo "db=$HERMES_DB_PATH config=$HERMES_CONFIG_DIR marker=${MARKER:-unset}"\n'
    'echo "cwd=$(pwd)"\n'
)


def _run(
    script: Path, args: list[str], path_dir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    env = dict(os.environ)
    if path_dir is not None:
        env["PATH"] = f"{path_dir.as_posix()}{os.pathsep}{env['PATH']}"
    return subprocess.run(  # noqa: S603
        [_BASH, script.as_posix(), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> tuple[Path, Path]:
    """Return a copy of the script pointed at scratch paths, and the stub PATH dir."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("runuser", _STUB_RUNUSER),):
        (stubs / name).write_text(body, encoding="utf-8", newline="\n")
        (stubs / name).chmod(0o755)
    workdir = tmp_path / "work"
    workdir.mkdir()
    env_file = tmp_path / "env.production"
    env_file.write_text("MARKER=from-env-file\n", encoding="utf-8", newline="\n")
    text = _SCRIPT.read_text(encoding="utf-8")
    for name, value in {
        "RELEASE_BIN": "/release/hermes",
        "ENV_FILE": env_file.as_posix(),
        "WORKDIR": workdir.as_posix(),
        "DB_PATH": "/state/hermes.sqlite3",
        "CONFIG_DIR": "/release/config",
    }.items():
        text, count = re.subn(rf"^{name}=.*$", f"{name}={value}", text, flags=re.M)
        assert count == 1, name
    # Pretend to be root: the real check needs uid 0, which a test run never has.
    assert '"$(id -u)"' in text
    text = text.replace('"$(id -u)"', "0")
    copy = tmp_path / "hermes"
    copy.write_text(text, encoding="utf-8", newline="\n")
    return copy, stubs


def test_script_is_valid_bash() -> None:
    """Invariant: the wrapper parses, so a deploy never installs a broken `hermes`."""
    assert _BASH is not None
    done = subprocess.run(  # noqa: S603
        [_BASH, "-n", _SCRIPT.as_posix()], capture_output=True, check=False
    )
    assert done.returncode == 0, done.stderr


def test_allowed_commands_are_exactly_the_registry_console_commands() -> None:
    """Invariant: the wrapper's allow-list is the registry's `wants_demo` set, no more."""
    match = re.search(r'^CONSOLE_COMMANDS="([^"]*)"$', _SCRIPT.read_text(encoding="utf-8"), re.M)
    assert match is not None
    assert frozenset(match.group(1).split()) == console_module.console_commands()


@pytest.mark.parametrize(
    ("args", "offender"),
    [
        (["serve"], "serve"),
        (["migrate", "--yes"], "migrate"),
        (["--demo", "ok-two-positions", "serve"], "serve"),
        (["--demo=x", "bot"], "bot"),
    ],
)
def test_writers_are_refused_with_one_line_and_exit_two(args: list[str], offender: str) -> None:
    """Invariant: serve, migrate and every non-console command never reach runuser (exit 2)."""
    done = _run(_SCRIPT, args)
    assert done.returncode == 2
    assert done.stdout == ""
    assert done.stderr.count("\n") == 1
    assert f"'{offender}' is not available here" in done.stderr


@pytest.mark.parametrize("command", sorted(console_module.console_commands()))
def test_console_commands_run_as_hermes_with_the_service_env(
    sandbox: tuple[Path, Path], command: str
) -> None:
    """Invariant: a console command execs the release binary via runuser, env and cwd set."""
    script, stubs = sandbox
    done = _run(script, [command, "--utc"], stubs)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[0] == f"runuser -u hermes -- /release/hermes {command} --utc"
    assert lines[1] == "db=/state/hermes.sqlite3 config=/release/config marker=from-env-file"
    assert lines[2].startswith("cwd=") and lines[2].endswith("/work")


def test_leading_demo_still_reaches_the_command(sandbox: tuple[Path, Path]) -> None:
    """Invariant: `hermes --demo S status` is allowed and forwarded untouched."""
    script, stubs = sandbox
    done = _run(script, ["--demo", "ok-two-positions", "status"], stubs)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[0] == (
        "runuser -u hermes -- /release/hermes --demo ok-two-positions status"
    )


def test_no_arguments_and_help_show_the_release_help(sandbox: tuple[Path, Path]) -> None:
    """Invariant: bare `hermes` and `--help` pass `--help` to the release; help is read-only."""
    script, stubs = sandbox
    bare = _run(script, [], stubs)
    explicit = _run(script, ["--help"], stubs)
    assert bare.stdout.splitlines()[0] == "runuser -u hermes -- /release/hermes --help"
    assert explicit.stdout.splitlines()[0] == "runuser -u hermes -- /release/hermes --help"


def test_wrapper_and_vps_remote_script_share_the_same_recipe() -> None:
    """Invariant: `hermes --vps` and the VPS wrapper run the same user, env, cwd and paths."""
    text = _SCRIPT.read_text(encoding="utf-8")
    remote = vps_module.build_ssh_argv(vps_module.VpsSettings("h", "u", Path("k")), "status", [])[
        -1
    ]
    for fragment in (
        "/etc/hermes/env.production",
        "/var/lib/hermes/hermes.sqlite3",
        "/opt/hermes/current/config",
        vps_module.REMOTE_BIN,
    ):
        assert fragment in text, fragment
        assert fragment in remote, fragment
    assert "RUN_AS=hermes" in text
    assert "runuser -u hermes" in remote
