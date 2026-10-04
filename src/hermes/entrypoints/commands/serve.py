"""``hermes serve``: run the shadow bar loop until SIGTERM or SIGINT."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints.commands import Command
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.logging import configure_logging

__all__ = ["COMMAND", "serve_main"]


def _run(rest: list[str], *, stream: Any) -> int:
    """Handle the ``hermes serve`` subcommand.

    Parses ``--config-dir`` and ``--db``, loads the composite
    :class:`~hermes.infrastructure.config.HermesConfig`, checks it can run
    the shadow loop, wires the composition root, installs signal handlers,
    and runs the bar loop. Returns 0 on a clean shutdown, 1 on a runtime
    failure, 2 on argument-parse failure, 3 on a config load or startup
    check failure. The environment tier comes from ``HERMES_ENV`` only.

    Args:
        rest: Residual argv after the ``serve`` token.
        stream: Output stream — errors are written here so tests can
            capture them without touching real stdout.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="hermes serve", add_help=False)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("-h", "--help", action="store_true")

    try:
        parsed = parser.parse_args(rest)
    except SystemExit:
        stream.write("ERROR: could not parse `hermes serve` arguments.\n")
        return 2

    if parsed.help:
        stream.write(
            "Usage: hermes serve [--config-dir DIR] [--db PATH]\n"
            "\n"
            "Runs the shadow bar loop until SIGTERM or SIGINT arrives.\n"
            "The environment tier comes from HERMES_ENV.\n"
            "Exit codes: 0 clean shutdown, 1 runtime failure, 2 usage, 3 config.\n"
        )
        return 0

    # One JSON object per line (ADR-008 §1): the daily line's journal count
    # and `hermes logs` parse serve's journal as JSON and skip anything else.
    configure_logging(env="prod")

    try:
        config = load_config(parsed.config_dir)
    except ConfigError as exc:
        stream.write(f"ERROR: config load failed: {exc}\n")
        return 3

    try:
        serve_module.check_startup(
            config,
            dict(os.environ),
            certification_store=JsonCertificationStore(
                serve_module.certification_dir_for(parsed.db)
            ),
        )
    except serve_module.StartupError as exc:
        stream.write(f"ERROR: startup check failed: {exc}\n")
        return 3

    return asyncio.run(serve_main(config, db_path=parsed.db))


async def serve_main(config: Any, *, db_path: Path) -> int:
    """Wire the composition root and run the bar loop to its exit code.

    Extracted from the argument handling so tests can drive the
    composition + run steps without going through argparse.

    Args:
        config: The loaded :class:`~hermes.infrastructure.config.HermesConfig`.
        db_path: Path to the SQLite ledger file.

    Returns:
        :func:`~hermes.entrypoints.serve.run`'s exit code: 0 on clean
        shutdown, 1 on a runtime failure (including a failed build).
    """
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    try:
        composition = serve_module.build_composition(config, db_path=db_path)
    except Exception as exc:
        serve_module.report_failure(exc)
        return 1
    serve_module.install_signal_handlers(loop, shutdown)
    return await serve_module.run(composition, shutdown)


COMMAND = Command(
    name="serve",
    summary="Run the paper-trading engine until SIGTERM/SIGINT.",
    run=_run,
)
