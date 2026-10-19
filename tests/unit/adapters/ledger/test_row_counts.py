"""Unit tests for the shared table row counter (#1187).

Invariants under test:

- every user table is counted, with its exact number of rows;
- SQLite's own ``sqlite_%`` tables (``sqlite_sequence``) are never counted,
  so two copies of one ledger compare equal whatever the file bookkeeping;
- a database with no table counts nothing.
"""

import sqlite3
from contextlib import closing

from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.ledger.row_counts import table_row_counts


def test_each_table_is_counted_with_its_exact_rows() -> None:
    """Every user table maps to its own row count (#1187)."""
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE TABLE trades (id INTEGER)")
        conn.execute("CREATE TABLE events (id INTEGER)")
        conn.executemany("INSERT INTO trades VALUES (?)", [(1,), (2,), (3,)])
        conn.execute("INSERT INTO events VALUES (1)")
        assert table_row_counts(conn) == {"events": 1, "trades": 3}, "one count per table"


def test_sqlite_internal_tables_are_never_counted() -> None:
    """An AUTOINCREMENT table's ``sqlite_sequence`` is file bookkeeping, not data (#1187)."""
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT)")
        conn.execute("INSERT INTO events (kind) VALUES ('a')")
        assert table_row_counts(conn) == {"events": 1}, "sqlite_sequence is left out"


def test_a_database_with_no_table_counts_nothing() -> None:
    """An empty database has no table to count (#1187)."""
    with closing(sqlite3.connect(":memory:")) as conn:
        assert table_row_counts(conn) == {}, "no table, no entry"


@given(rows=st.dictionaries(st.from_regex(r"t_[a-z]{1,8}", fullmatch=True), st.integers(0, 20)))
def test_counts_equal_the_rows_inserted_per_table(rows: dict[str, int]) -> None:
    """Whatever the tables and row counts, each count is the rows inserted (#1187)."""
    with closing(sqlite3.connect(":memory:")) as conn:
        # Names come from the t_[a-z]+ strategy above, never from input.
        for name, count in rows.items():
            conn.execute(f'CREATE TABLE "{name}" (v INTEGER)')
            insert = f'INSERT INTO "{name}" VALUES (?)'  # noqa: S608
            conn.executemany(insert, [(i,) for i in range(count)])
        assert table_row_counts(conn) == rows, "counts match the inserts"
