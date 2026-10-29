"""Unit tests for scripts/dev/migration-sql-check.py (#483).

Invariants under test:

- Every shipped migration passes: legacy 0001-0006 names and no transaction
  control outside trigger bodies.
- A new numbered name, or one not shaped ``YYYYMMDDTHHMM_<slug>.sql``, fails.
- ``BEGIN;``, ``COMMIT;``, ``END;``, ``ROLLBACK`` and savepoints fail; the same
  words inside triggers, comments, strings or CASE expressions do not.
- Timestamp names sort after every legacy name, so existing ledgers apply new
  migrations last without renaming anything.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT


def _load():
    """Import the script module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "migration_sql_check", REPO_ROOT / "scripts" / "dev" / "migration-sql-check.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["migration_sql_check"] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load()
_SHIPPED = sorted(
    (REPO_ROOT / "src" / "hermes" / "adapters" / "ledger" / "migrations").glob("*.sql")
)


def test_every_shipped_migration_passes() -> None:
    """The live set, including 0003's triggers, has no violation."""
    assert _SHIPPED
    assert CHECK.check(_SHIPPED) == []


@pytest.mark.parametrize(
    "name",
    ["20261004T0900_lots_add_x.sql", "0001_initial_schema.sql", "0006_capital_seeds.sql"],
)
def test_allowed_names(name: str, tmp_path: Path) -> None:
    """Timestamp names and the frozen legacy numbers are accepted."""
    path = tmp_path / name
    path.write_text("CREATE TABLE a (id INTEGER);", encoding="utf-8")
    assert CHECK.check([path]) == []


@pytest.mark.parametrize(
    "name",
    [
        "0007_next_free_number.sql",
        "2026104T0900_short_date.sql",
        "20261004T0900_Upper_Case.sql",
        "20261004_0900_no_t.sql",
        "lots_add_x.sql",
    ],
)
def test_rejected_names(name: str) -> None:
    """A new numbered file or a malformed timestamp is refused, so numbers cannot collide again."""
    message = CHECK.name_violation(Path(name))
    assert message == (
        f"{name}: name must be YYYYMMDDTHHMM_<slug>.sql (UTC timestamp, lowercase slug); "
        "numbered names stop at 0006"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "BEGIN;\nCREATE TABLE a (id INTEGER);",
        "CREATE TABLE a (id INTEGER);\nCOMMIT;",
        "begin immediate transaction;",
        "END TRANSACTION;",
        "CREATE TABLE a (id INTEGER); END;",
        "ROLLBACK;",
        "SAVEPOINT s1;",
        "RELEASE s1;",
    ],
)
def test_transaction_control_is_reported(sql: str) -> None:
    """Each transaction-control statement would escape the runner's wrapper."""
    assert len(CHECK.transaction_statements(sql)) == 1


def test_trigger_comment_string_and_case_are_not_transaction_control() -> None:
    """BEGIN/END inside a trigger, a comment, a string or a CASE are ordinary SQL."""
    sql = """
    -- BEGIN; this comment mentions COMMIT;
    /* END; */
    CREATE TRIGGER t BEFORE UPDATE ON a BEGIN SELECT RAISE(ABORT, 'no'); END;
    INSERT INTO a (label) VALUES ('BEGIN; COMMIT;');
    UPDATE a SET label = CASE WHEN id > 1 THEN 'x' ELSE 'y' END;
    """
    assert CHECK.transaction_statements(sql) == []


def test_timestamp_names_sort_after_legacy_names() -> None:
    """The runner orders by name; '0' < '2' puts every legacy file first."""
    names = ["20261004T0900_b.sql", "0006_capital_seeds.sql", "0001_initial_schema.sql"]
    assert sorted(names) == ["0001_initial_schema.sql", "0006_capital_seeds.sql", names[0]]


def test_main_exits_1_on_a_bad_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI fails and names the file and the statement."""
    bad = tmp_path / "20261004T0900_bad.sql"
    bad.write_text("BEGIN;\nCREATE TABLE a (id INTEGER);\nCOMMIT;\n", encoding="utf-8")
    assert CHECK.main([str(bad)]) == 1
    out = capsys.readouterr().out
    assert out.count(str(bad)) == 2
    assert "'BEGIN'" in out
