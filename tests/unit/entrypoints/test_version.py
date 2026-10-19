"""Tests for ``hermes version`` (#539).

Invariants protected here:

- the report carries package version, release, Python version and ledger level;
- the release is the resolved target name of the release link, ``unknown`` when
  the link is absent or dangling;
- the ledger level is the latest applied migration with the pending count,
  ``no ledger`` when the file is absent, empty or not a ledger; it is never written;
- ``--json`` is one document with the same fields; ``--version`` and ``-V``
  print the plain report; the command exits 0 except 2 on bad usage.
"""

from __future__ import annotations

import io
import json
import platform
import sqlite3
from importlib import metadata
from pathlib import Path

import pytest

import hermes
from hermes.adapters.ledger.migration_runner import MIGRATIONS_DIR, apply_migrations
from hermes.entrypoints import _release as release_module
from hermes.entrypoints import cli
from hermes.entrypoints import version as version_module
from hermes.entrypoints.commands import discover


def _link(tmp_path: Path, name: str = "abc123def456") -> Path:
    target = tmp_path / "versions" / name
    target.mkdir(parents=True)
    link = tmp_path / "current"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    return link


def _run(argv: list[str], link: Path, err: io.StringIO | None = None) -> tuple[int, str]:
    out = io.StringIO()
    code = version_module.main(argv, stream=out, release_link=link, err=err)
    return code, out.getvalue()


def test_release_is_the_resolved_link_target_name(tmp_path: Path) -> None:
    """The release field is the directory name `current` points at."""
    info = version_module.collect_info(db=tmp_path / "none.db", release_link=_link(tmp_path))
    assert info.release == "abc123def456"


def test_absent_link_reports_unknown(tmp_path: Path) -> None:
    """No release link means `unknown`, not an error."""
    info = version_module.collect_info(db=tmp_path / "none.db", release_link=tmp_path / "current")
    assert info.release == "unknown"


def test_dangling_link_reports_unknown(tmp_path: Path) -> None:
    """A link to a removed release reports `unknown`."""
    link = _link(tmp_path)
    (tmp_path / "versions" / "abc123def456").rmdir()
    assert version_module.collect_info(db=tmp_path / "x.db", release_link=link).release == "unknown"


def test_python_and_package_version_fields(tmp_path: Path) -> None:
    """The Python field is the running interpreter; the package version is non-empty."""
    info = version_module.collect_info(db=tmp_path / "none.db", release_link=tmp_path / "c")
    assert info.python == platform.python_version()
    assert info.version == hermes.__version__


def test_package_version_unknown_when_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing distribution degrades to `unknown`."""

    def _missing(_name: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(release_module.metadata, "version", _missing)
    info = version_module.collect_info(db=tmp_path / "none.db", release_link=tmp_path / "c")
    assert info.version == "unknown", "the shared lookup degrades instead of raising"


def test_missing_ledger_reports_no_ledger(tmp_path: Path) -> None:
    """An absent database file means `no ledger`."""
    assert version_module._ledger_level(tmp_path / "none.db") == "no ledger"


def test_non_ledger_database_reports_no_ledger(tmp_path: Path) -> None:
    """A database without schema_migrations means `no ledger`."""
    db = tmp_path / "other.db"
    sqlite3.connect(db).close()
    assert version_module._ledger_level(db) == "no ledger"


def test_empty_bookkeeping_reports_no_ledger(tmp_path: Path) -> None:
    """A schema_migrations table with no rows means `no ledger`."""
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE schema_migrations (filename TEXT PRIMARY KEY, applied_at TEXT)")
    conn.commit()
    conn.close()
    assert version_module._ledger_level(db) == "no ledger"


def test_fully_migrated_ledger_shows_latest_and_zero_pending(tmp_path: Path) -> None:
    """After every migration the level is the newest file with 0 pending."""
    db = tmp_path / "ledger.db"
    conn = sqlite3.connect(db)
    apply_migrations(conn)
    conn.close()
    latest = max(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    assert version_module._ledger_level(db) == f"{latest} (0 pending)"


def test_reading_the_ledger_does_not_modify_it(tmp_path: Path) -> None:
    """The ledger bytes are identical before and after the report."""
    db = tmp_path / "ledger.db"
    conn = sqlite3.connect(db)
    apply_migrations(conn)
    conn.close()
    before = db.read_bytes()
    version_module._ledger_level(db)
    assert db.read_bytes() == before


def test_plain_output_has_four_lines_and_exits_zero(tmp_path: Path) -> None:
    """The plain report is four labelled lines."""
    code, text = _run(["--db", str(tmp_path / "none.db")], _link(tmp_path))
    assert code == 0
    assert text == (
        f"hermes {hermes.__version__}\n"
        "release: abc123def456\n"
        f"python: {platform.python_version()}\n"
        "ledger: no ledger\n"
    )


def test_json_output_is_one_document(tmp_path: Path) -> None:
    """--json emits one JSON object with the four fields."""
    code, text = _run(["--db", str(tmp_path / "none.db"), "--json"], tmp_path / "current")
    assert code == 0
    assert json.loads(text) == {
        "version": hermes.__version__,
        "release": "unknown",
        "python": platform.python_version(),
        "ledger": "no ledger",
    }


def test_help_exits_zero_with_exit_codes(tmp_path: Path) -> None:
    """-h prints usage naming the exit codes and returns 0."""
    code, text = _run(["-h"], tmp_path / "current")
    assert code == 0
    assert "Exit codes: 0 always, 2 usage." in text


def test_bad_usage_exits_two(tmp_path: Path) -> None:
    """An unknown flag returns 2 with an ERROR line."""
    err = io.StringIO()
    code, text = _run(["--bogus"], tmp_path / "current", err)
    assert (code, text) == (2, "")
    assert err.getvalue().startswith("ERROR: could not parse `hermes version` arguments.")


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_cli_version_flags_print_plain_report(flag: str) -> None:
    """`hermes --version` and `-V` delegate to the version command's plain output."""
    out = io.StringIO()
    assert cli.main([flag], stream=out) == 0
    assert out.getvalue().splitlines()[0] == f"hermes {hermes.__version__}"


def test_command_is_registered_and_runs(tmp_path: Path) -> None:
    """The registry exposes `version` and its run hook reaches the implementation."""
    command = discover()["version"]
    out = io.StringIO()
    assert command.run(["--db", str(tmp_path / "none.db"), "--json"], stream=out) == 0
    assert json.loads(out.getvalue())["ledger"] == "no ledger"


def test_read_release_without_a_link_has_no_live_since(tmp_path: Path) -> None:
    """A missing link yields `unknown` and no time, never an exception."""
    info = version_module.read_release(tmp_path / "current")

    assert (info.name, info.live_since) == ("unknown", None)


def test_read_release_of_a_dangling_link_has_no_live_since(tmp_path: Path) -> None:
    """A link whose target is gone yields `unknown` and no time."""
    link = tmp_path / "current"
    try:
        link.symlink_to(tmp_path / "gone", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    info = version_module.read_release(link)

    assert (info.name, info.live_since) == ("unknown", None)
