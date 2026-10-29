"""Unit tests for the shared command boot helpers (#1187).

Invariants under test:

- ``--db`` and ``--config-dir`` parse to paths, with the documented defaults;
- no file at ``--db`` is exit 3 with one ERROR line, and the file is never
  created; an existing ledger opens read-only unless asked otherwise;
- SQLite refusing to open the file is exit 3, not a traceback;
- a ``ConfigError`` while loading is exit 3 with one ERROR line, and the
  ``on_error`` hook sees that same error.
"""

import argparse
import io
import sqlite3
from pathlib import Path

import pytest

from hermes.entrypoints import _cli
from hermes.entrypoints._cli import (
    add_config_dir_arg,
    add_db_arg,
    config_or_exit,
    load_config_or_exit,
    open_ledger_or_exit,
    require_ledger,
)
from hermes.infrastructure.config.errors import ConfigError
from tests.support.paths import REPO_ROOT


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    add_db_arg(parser)
    add_config_dir_arg(parser)
    return parser


def _ledger(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE events (id INTEGER)")
        conn.execute("INSERT INTO events VALUES (7)")
    conn.close()
    return db


def test_flags_default_to_the_server_ledger_and_the_local_config() -> None:
    """Without flags, --db is hermes.sqlite3 and --config-dir is ./config (#1187)."""
    parsed = _parser().parse_args([])
    assert (parsed.db, parsed.config_dir) == (Path("hermes.sqlite3"), Path("config")), "defaults"


def test_flags_parse_to_the_given_paths() -> None:
    """--db and --config-dir become the paths given on the command line (#1187)."""
    parsed = _parser().parse_args(["--db", "/x/l.db", "--config-dir", "/y/cfg"])
    assert (parsed.db, parsed.config_dir) == (Path("/x/l.db"), Path("/y/cfg")), "given paths"


def test_a_none_default_leaves_the_path_to_the_command() -> None:
    """``default=None`` lets the console commands resolve the path from the env (#1187)."""
    parser = argparse.ArgumentParser(add_help=False)
    add_db_arg(parser, default=None)
    add_config_dir_arg(parser, default=None)
    parsed = parser.parse_args([])
    assert (parsed.db, parsed.config_dir) == (None, None), "no default applied"


def test_require_ledger_on_a_missing_file_is_exit_3_with_one_error_line(tmp_path: Path) -> None:
    """No file at --db is the documented no-ledger code, 3 (#1187)."""
    missing = tmp_path / "absent.sqlite3"
    err = io.StringIO()
    assert (require_ledger(missing, err), err.getvalue()) == (
        3,
        f"ERROR: no ledger at {missing}\n",
    ), "exit 3 and the path named"


def test_require_ledger_on_a_directory_is_exit_3(tmp_path: Path) -> None:
    """A directory at --db is not a ledger (#1187)."""
    assert require_ledger(tmp_path, io.StringIO()) == 3, "a directory is no ledger"


def test_require_ledger_on_a_file_passes_silently(tmp_path: Path) -> None:
    """An existing ledger file lets the command go on and prints nothing (#1187)."""
    err = io.StringIO()
    assert (require_ledger(_ledger(tmp_path), err), err.getvalue()) == (None, ""), "no exit"


def test_open_on_a_missing_file_is_exit_3_and_creates_nothing(tmp_path: Path) -> None:
    """A wrong --db path is reported, never created as an empty ledger (#1187)."""
    missing = tmp_path / "absent.sqlite3"
    result = open_ledger_or_exit(missing, io.StringIO(), read_only=False)
    assert (result, missing.exists()) == (3, False), "exit 3, no file left behind"


def test_open_returns_a_connection_that_reads_the_ledger(tmp_path: Path) -> None:
    """An existing ledger opens and its rows read back (#1187)."""
    connection = open_ledger_or_exit(_ledger(tmp_path), io.StringIO())
    assert not isinstance(connection, int), "opened"
    try:
        assert connection.execute("SELECT id FROM events").fetchall() == [(7,)], "rows read"
    finally:
        connection.close()


def test_open_defaults_to_read_only(tmp_path: Path) -> None:
    """The default connection refuses writes, so a read command is safe next to serve (#1187)."""
    connection = open_ledger_or_exit(_ledger(tmp_path), io.StringIO())
    assert not isinstance(connection, int), "opened"
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO events VALUES (8)")
    finally:
        connection.close()


def test_sqlite_refusing_the_file_is_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An open that SQLite refuses is exit 3 with its reason (#1187).

    The refusal is faked at ``open_ledger``; a real unopenable file is covered
    by ``test_ledger.py`` and ``test_risk_inputs.py`` through the commands.
    """

    def refuse(_path: Path, *, read_only: bool) -> sqlite3.Connection:
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(_cli, "open_ledger", refuse)
    db = _ledger(tmp_path)
    err = io.StringIO()
    assert (open_ledger_or_exit(db, err), err.getvalue()) == (
        3,
        f"ERROR: could not open the ledger at {db}: unable to open database file\n",
    ), "exit 3 with SQLite's reason"


def test_config_error_is_exit_3_with_one_error_line() -> None:
    """A ConfigError while loading is the documented config code, 3 (#1187)."""

    def load() -> int:
        raise ConfigError("strategies.yaml: bad")

    err = io.StringIO()
    assert (config_or_exit(load, err), err.getvalue()) == (
        3,
        "ERROR: config load failed: Config error: strategies.yaml: bad\n",
    ), "exit 3 and the reason"


def test_on_error_sees_the_config_error() -> None:
    """The hook gets the same error, for a command that also pings a dead-man switch (#1187)."""
    seen: list[str] = []
    error = ConfigError("market_data.yaml: bad")

    def load() -> int:
        raise error

    config_or_exit(load, io.StringIO(), on_error=lambda exc: seen.append(str(exc)))
    assert seen == [str(error)], "the hook ran once with the error"


def test_a_successful_load_returns_its_value_and_prints_nothing() -> None:
    """When nothing fails, the loaded value comes back untouched (#1187)."""
    err = io.StringIO()
    assert (config_or_exit(lambda: ("scales", 2026), err), err.getvalue()) == (
        ("scales", 2026),
        "",
    ), "value returned"


def test_load_config_on_a_missing_dir_is_exit_3(tmp_path: Path) -> None:
    """A --config-dir with no config is exit 3 (#1187)."""
    err = io.StringIO()
    code = load_config_or_exit(tmp_path / "missing", err, env={})
    assert (code, err.getvalue().startswith("ERROR: config load failed: ")) == (3, True), "exit 3"


def test_load_config_reads_the_repository_config() -> None:
    """The real config loads, with its one enabled strategy (#1187)."""
    config = load_config_or_exit(REPO_ROOT / "config", io.StringIO(), env={})
    assert not isinstance(config, int), "loaded"
    enabled = [s.name for s in config.strategies.strategies if s.enabled]
    assert enabled == ["swing-donchian-4h"], "the repository roster"
