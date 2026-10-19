"""Boot helpers every ``hermes`` command shares: ``--db``, ``--config-dir`` and their failures.

A command that reads the ledger or the config adds its flags here and opens
them through these helpers, so "no ledger" and "bad config" read and exit the
same way in every command (#1187).

Exit codes every command keeps:

- ``0`` (:data:`EXIT_OK`): done.
- ``1`` (:data:`EXIT_FAILURE`): the command ran and found a problem, or a
  read failed after the ledger opened; each command documents which.
- ``2`` (:data:`EXIT_USAGE`): bad arguments.
- ``3`` (:data:`EXIT_CONFIG`): the config cannot be loaded.
- ``3`` (:data:`EXIT_NO_LEDGER`): no ledger at ``--db``, or it cannot be
  opened.

Codes above 3 are a command's own (``4`` incomplete, ``5`` frozen) and are
documented in its usage text.

The helpers return the value or the exit code, so ``main`` stays a plain
``int``-returning function the tests call directly.
"""

import argparse
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Final, TextIO

from hermes.adapters.ledger.connection import open_ledger
from hermes.entrypoints.serve import DEFAULT_DB_PATH
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.config.root import HermesConfig

__all__ = [
    "EXIT_CONFIG",
    "EXIT_FAILURE",
    "EXIT_NO_LEDGER",
    "EXIT_OK",
    "EXIT_USAGE",
    "add_config_dir_arg",
    "add_db_arg",
    "config_or_exit",
    "load_config_or_exit",
    "open_ledger_or_exit",
    "require_ledger",
]

EXIT_OK: Final = 0
EXIT_FAILURE: Final = 1
EXIT_USAGE: Final = 2
EXIT_CONFIG: Final = 3
EXIT_NO_LEDGER: Final = 3


def add_db_arg(parser: argparse.ArgumentParser, *, default: Path | None = DEFAULT_DB_PATH) -> None:
    """Add ``--db PATH``, the ledger file.

    Args:
        parser: The command's parser.
        default: The path when the flag is absent; ``None`` lets the command
            resolve it later (from the environment, for the console commands).
    """
    parser.add_argument("--db", type=Path, default=default)


def add_config_dir_arg(
    parser: argparse.ArgumentParser, *, default: Path | None = DEFAULT_CONFIG_DIR
) -> None:
    """Add ``--config-dir DIR``, the directory of the YAML config.

    Args:
        parser: The command's parser.
        default: The directory when the flag is absent; ``None`` lets the
            command resolve it later.
    """
    parser.add_argument("--config-dir", type=Path, default=default)


def require_ledger(db: Path, err: TextIO) -> int | None:
    """Return :data:`EXIT_NO_LEDGER` with an ERROR line when ``db`` is not a file.

    A command that opens the ledger through its own reader checks this first,
    so a missing ledger is reported, never created.

    Args:
        db: The ``--db`` path.
        err: Stream for the ERROR line.

    Returns:
        ``None`` when the file exists, else the exit code.
    """
    if db.is_file():
        return None
    err.write(f"ERROR: no ledger at {db}\n")
    return EXIT_NO_LEDGER


def open_ledger_or_exit(
    db: Path, err: TextIO, *, read_only: bool = True
) -> sqlite3.Connection | int:
    """Open the existing ledger at ``db``, or return :data:`EXIT_NO_LEDGER`.

    Args:
        db: The ``--db`` path.
        err: Stream for the ERROR line.
        read_only: Open with SQLite's ``mode=ro``, safe next to ``hermes serve``.

    Returns:
        The open connection, which the caller closes, or the exit code when
        there is no file at ``db`` or SQLite cannot open it.
    """
    missing = require_ledger(db, err)
    if missing is not None:
        return missing
    try:
        return open_ledger(db, read_only=read_only)
    except sqlite3.Error as exc:
        err.write(f"ERROR: could not open the ledger at {db}: {exc}\n")
        return EXIT_NO_LEDGER


def config_or_exit[T](
    load: Callable[[], T],
    err: TextIO,
    *,
    on_error: Callable[[ConfigError], None] | None = None,
) -> T | int:
    """Run ``load``, or return :data:`EXIT_CONFIG` when it raises :class:`ConfigError`.

    For a command that loads more than ``load_config`` (the tax scales, the
    filing config) as one step.

    Args:
        load: Loads whatever config the command needs.
        err: Stream for the ERROR line.
        on_error: Called with the error after the ERROR line, for a command
            that also reports the failure elsewhere (a dead-man ping).

    Returns:
        What ``load`` returned, or the exit code.
    """
    try:
        return load()
    except ConfigError as exc:
        err.write(f"ERROR: config load failed: {exc}\n")
        if on_error is not None:
            on_error(exc)
        return EXIT_CONFIG


def load_config_or_exit(
    config_dir: Path,
    err: TextIO,
    *,
    env: dict[str, str] | None = None,
    on_error: Callable[[ConfigError], None] | None = None,
) -> HermesConfig | int:
    """Load the config under ``config_dir``, or return :data:`EXIT_CONFIG`.

    Args:
        config_dir: The ``--config-dir`` directory.
        err: Stream for the ERROR line.
        env: Env snapshot passed to :func:`load_config`; ``None`` reads the
            process environment after ``.env.local``.
        on_error: Called with the error after the ERROR line.

    Returns:
        The loaded config, or the exit code.
    """
    return config_or_exit(lambda: load_config(config_dir, env=env), err, on_error=on_error)
