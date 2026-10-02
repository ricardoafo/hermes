"""Invariants for the systemd unit files shipped in ``deploy/systemd/``.

The units are the only thing that starts ``hermes serve`` on the VPS and
nothing in CI starts them. These tests pin the paths and flags the first
real deploy depends on (issue #306).

Reference: openspec/changes/306-serve-systemd-boot/change.md.
"""

from __future__ import annotations

import configparser
import io
import shlex
import tomllib
from pathlib import Path, PurePosixPath

import pytest

from hermes.entrypoints import cli as cli_module

_REPO_ROOT = Path(__file__).resolve().parents[3]
_UNIT_DIR = _REPO_ROOT / "deploy" / "systemd"
_UNIT_FILES = sorted([*_UNIT_DIR.glob("*.service"), *_UNIT_DIR.glob("*.timer")])
_SERVICE_FILES = [path for path in _UNIT_FILES if path.suffix == ".service"]

_ENV_FILE = "/etc/hermes/env.production"
_EXEC_PREFIXES = "@-:+!"


def _parse(name: str) -> configparser.ConfigParser:
    """Parse one unit file; ``strict=False`` because systemd allows repeated keys."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string((_UNIT_DIR / name).read_text(encoding="utf-8"))
    return parser


def _hermes_exec_argv() -> list[str]:
    """Return the ``hermes.service`` ExecStart split into argv tokens."""
    return shlex.split(_parse("hermes.service")["Service"]["ExecStart"])


def _flag(argv: list[str], name: str) -> str:
    """Return the value that follows ``name`` in ``argv``."""
    return argv[argv.index(name) + 1]


def test_all_expected_unit_files_are_discovered() -> None:
    """Parametrised tests below never silently run on an empty file set."""
    names = {path.name for path in _UNIT_FILES}
    assert names >= {
        "hermes.service",
        "hermes-backup.service",
        "hermes-health.service",
        "hermes-backup.timer",
        "hermes-health.timer",
    }


@pytest.mark.parametrize("path", _SERVICE_FILES, ids=lambda p: p.name)
def test_service_exec_start_is_an_absolute_path(path: Path) -> None:
    """A relative ExecStart binary fails at start with 203/EXEC under systemd."""
    exec_start = _parse(path.name)["Service"]["ExecStart"]
    binary = exec_start.split()[0].lstrip(_EXEC_PREFIXES)
    assert PurePosixPath(binary).is_absolute(), f"{path.name} ExecStart binary {binary!r}"


def test_hermes_service_runs_the_serve_subcommand_of_the_console_script() -> None:
    """The unit starts `hermes serve` from the active release's virtualenv."""
    argv = _hermes_exec_argv()
    assert argv[:2] == ["/opt/hermes/current/.venv/bin/hermes", "serve"]


def test_hermes_service_binary_is_a_declared_console_script() -> None:
    """ExecStart cannot drift from the `[project.scripts]` table in pyproject.toml."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    binary = PurePosixPath(_hermes_exec_argv()[0]).name
    assert binary in pyproject["project"]["scripts"], f"{binary!r} is not a console script"


def test_hermes_service_passes_absolute_config_dir_and_db_paths() -> None:
    """Relative CLI defaults must never decide where the VPS reads or writes."""
    argv = _hermes_exec_argv()
    assert _flag(argv, "--config-dir") == "/opt/hermes/current/config"
    assert _flag(argv, "--db") == "/var/lib/hermes/hermes.sqlite3"


def test_hermes_service_flags_are_accepted_by_the_real_cli_parser() -> None:
    """An unknown flag in ExecStart would crash-loop; the real parser must accept them."""
    out = io.StringIO()
    rc = cli_module.main([*_hermes_exec_argv()[1:], "--help"], stream=out)
    assert rc == 0, out.getvalue()


@pytest.mark.parametrize("path", _SERVICE_FILES, ids=lambda p: p.name)
def test_service_environment_file_is_the_bootstrap_path(path: Path) -> None:
    """Every unit that loads secrets reads the file vps_bootstrap.sh writes, with no '-'.

    A leading '-' would boot the process with no secrets instead of failing the start.
    """
    service = _parse(path.name)["Service"]
    if "EnvironmentFile" not in service:
        pytest.skip(f"{path.name} loads no environment file")
    assert service["EnvironmentFile"] == _ENV_FILE


def test_hermes_service_loads_the_environment_file() -> None:
    """The engine must not start without the secrets file (no EnvironmentFile= means no secrets)."""
    assert "EnvironmentFile" in _parse("hermes.service")["Service"]


def test_hermes_service_keeps_the_strict_filesystem_sandbox() -> None:
    """Loosening ProtectSystem would widen the blast radius of a compromised process."""
    assert _parse("hermes.service")["Service"]["ProtectSystem"] == "strict"


def test_hermes_service_db_directory_is_covered_by_read_write_paths() -> None:
    """With ProtectSystem=strict the DB directory is read-only unless listed here."""
    service = _parse("hermes.service")["Service"]
    db_dir = PurePosixPath(_flag(_hermes_exec_argv(), "--db")).parent
    writable = [PurePosixPath(p) for p in service["ReadWritePaths"].split()]
    assert any(db_dir == path or path in db_dir.parents for path in writable), (
        f"{db_dir} is not under ReadWritePaths {writable}"
    )


def test_hermes_service_working_directory_is_the_writable_state_directory() -> None:
    """A relative write must land in state, not in the read-only release."""
    service = _parse("hermes.service")["Service"]
    assert service["WorkingDirectory"] == "/var/lib/hermes"


def test_hermes_service_state_directory_is_created_by_systemd() -> None:
    """StateDirectory= avoids a 226/NAMESPACE failure when /var/lib/hermes is missing."""
    assert _parse("hermes.service")["Service"]["StateDirectory"] == "hermes"


def test_hermes_service_does_not_restart_on_argument_or_config_errors() -> None:
    """Exit 2 (usage) and 3 (config load failed) are deterministic: no restart loop."""
    service = _parse("hermes.service")["Service"]
    assert set(service["RestartPreventExitStatus"].split()) >= {"2", "3"}


def _seconds(value: str) -> int:
    """Convert a systemd time span such as ``30s`` or ``10min`` to seconds."""
    for suffix, factor in (("min", 60), ("s", 1)):
        if value.endswith(suffix):
            return int(value.removesuffix(suffix)) * factor
    return int(value)


def test_hermes_service_declares_a_finite_start_limit() -> None:
    """Without StartLimit* a crash loop restarts for ever (and re-fetches history each time)."""
    unit = _parse("hermes.service")["Unit"]
    assert 1 <= int(unit["StartLimitBurst"]) <= 10
    assert 0 < _seconds(unit["StartLimitIntervalSec"]) <= 3600


def test_hermes_service_start_limit_trips_inside_its_interval() -> None:
    """A burst that takes longer than the interval to reach never trips the limit."""
    parser = _parse("hermes.service")
    burst = int(parser["Unit"]["StartLimitBurst"])
    interval = _seconds(parser["Unit"]["StartLimitIntervalSec"])
    assert burst * _seconds(parser["Service"]["RestartSec"]) < interval


@pytest.mark.parametrize("path", _UNIT_FILES, ids=lambda p: p.name)
def test_unit_values_carry_no_inline_comments(path: Path) -> None:
    """A trailing `# ...` makes a unit value unparsable: units have no inline comments."""
    parser = _parse(path.name)
    offenders = [
        f"[{section}] {key}={value}"
        for section in parser.sections()
        for key, value in parser.items(section)
        if " #" in value
    ]
    assert offenders == []


@pytest.mark.parametrize("path", _UNIT_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize(
    "legacy",
    ["/etc/hermes.env", "/opt/hermes/.venv", "/opt/hermes/deploy/", "-m hermes serve"],
)
def test_unit_files_never_reference_the_pre_release_layout(path: Path, legacy: str) -> None:
    """The single-venv layout and the flat env file are gone; no unit may name them."""
    assert legacy not in path.read_text(encoding="utf-8")
