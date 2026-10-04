"""Console commands default `--db` and `--config-dir` from the service paths in the env (#610).

On the VPS the wrapper exports ``HERMES_DB_PATH`` and ``HERMES_CONFIG_DIR`` so
`hermes status` works bare; an explicit flag always wins.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from hermes.entrypoints import activity as activity_module
from hermes.entrypoints import console as console_module
from hermes.entrypoints import status as status_module
from hermes.entrypoints.commands import positions as positions_command
from hermes.entrypoints.serve import DEFAULT_DB_PATH
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR


def test_default_db_path_prefers_the_env_and_ignores_blank() -> None:
    """Invariant: HERMES_DB_PATH wins; unset or blank falls back to serve's default."""
    assert console_module.default_db_path({"HERMES_DB_PATH": "/var/lib/hermes/x.db"}) == Path(
        "/var/lib/hermes/x.db"
    )
    assert console_module.default_db_path({"HERMES_DB_PATH": "  "}) == DEFAULT_DB_PATH
    assert console_module.default_db_path({}) == DEFAULT_DB_PATH


def test_default_config_dir_prefers_the_env_and_ignores_blank() -> None:
    """Invariant: HERMES_CONFIG_DIR wins; unset or blank falls back to ./config."""
    assert console_module.default_config_dir({"HERMES_CONFIG_DIR": "/opt/c"}) == Path("/opt/c")
    assert console_module.default_config_dir({"HERMES_CONFIG_DIR": ""}) == DEFAULT_CONFIG_DIR


def test_status_reads_the_env_db_and_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant: bare `hermes status` loads HERMES_CONFIG_DIR and names HERMES_DB_PATH."""
    seen: list[Path | None] = []

    def fake_load(config_dir: Path | None = None, **_: object) -> object:
        seen.append(config_dir)
        raise ConfigError("stop here")

    monkeypatch.setattr(status_module, "load_config", fake_load)
    env = {"HERMES_CONFIG_DIR": str(tmp_path / "cfg"), "HERMES_DB_PATH": str(tmp_path / "l.db")}
    assert status_module.main([], stream=io.StringIO(), env=env) == 3
    assert seen == [tmp_path / "cfg"]


def test_status_explicit_flags_beat_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant: `--config-dir` on the command line overrides HERMES_CONFIG_DIR."""
    seen: list[Path | None] = []

    def fake_load(config_dir: Path | None = None, **_: object) -> object:
        seen.append(config_dir)
        raise ConfigError("stop here")

    monkeypatch.setattr(status_module, "load_config", fake_load)
    env = {"HERMES_CONFIG_DIR": str(tmp_path / "env")}
    status_module.main(["--config-dir", str(tmp_path / "flag")], stream=io.StringIO(), env=env)
    assert seen == [tmp_path / "flag"]


def test_activity_reads_the_ledger_named_by_the_env(tmp_path: Path) -> None:
    """Invariant: bare `hermes activity` looks for the ledger at HERMES_DB_PATH."""
    out = io.StringIO()
    path = tmp_path / "env-ledger.sqlite3"
    code = activity_module.run_activity([], out, env={"HERMES_DB_PATH": str(path)})
    assert code == 3
    assert str(path) in out.getvalue()


def test_positions_reads_the_ledger_named_by_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant: bare `hermes positions` opens the ledger at HERMES_DB_PATH."""
    opened: list[Path] = []

    def fake_open(path: Path) -> object:
        opened.append(path)
        raise FileNotFoundError(path)

    monkeypatch.setattr(console_module.LedgerReadModel, "open", fake_open)
    target = tmp_path / "nothing.sqlite3"
    out = io.StringIO()
    assert positions_command.run_positions([], out, env={"HERMES_DB_PATH": str(target)}) == 0
    assert opened == [target]
