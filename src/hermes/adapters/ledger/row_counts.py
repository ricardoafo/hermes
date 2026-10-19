"""Row count of every user table in a SQLite database.

Two copies of the ledger hold the same data only if every table holds the
same number of rows, so the migration dry run (rows before and after) and
``hermes shadow-start`` (the archived copy against the ledger) both compare
these counts. SQLite's own ``sqlite_%`` tables are left out: they describe
the file, not the ledger's data.
"""

import sqlite3

__all__ = ["table_row_counts"]


def table_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Return the number of rows in each user table of ``conn``, by table name.

    Args:
        conn: An open connection; it is only read.

    Returns:
        Rows per table, in name order; empty for a database with no table.

    Raises:
        sqlite3.Error: The database cannot be read.
    """
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    # Table names come from sqlite_master itself, quoted as identifiers.
    return {
        name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])  # noqa: S608
        for name in tables
    }
