"""``hermes serve``: run the shadow bar loop until SIGTERM or SIGINT."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any, TextIO

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints._cli import add_config_dir_arg, add_db_arg, load_config_or_exit
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.commands import Command
from hermes.infrastructure.logging import configure_logging

__all__ = ["COMMAND", "EXIT_LEDGER_IN_USE", "serve_main"]

EXIT_LEDGER_IN_USE = 4
"""Another serve holds the ledger's serve lock (#855).

systemd retries this one (``Restart=on-failure``): the holder may be a manual
run that ends soon, after which the unit's next start takes the lock.
"""


def _run(rest: list[str], *, stream: Any, err: TextIO | None = None) -> int:
    """Handle the ``hermes serve`` subcommand.

    Parses ``--config-dir``, ``--db``, ``--strategy`` and ``--new-ledger``, loads the composite
    :class:`~hermes.infrastructure.config.HermesConfig`, checks it can run
    the shadow loop, wires the composition root, installs signal handlers,
    and runs the bar loop. Returns 0 on a clean shutdown, 1 on a runtime
    failure, 2 on argument-parse failure, 3 on a config load or startup
    check failure, 4 when another serve already runs on the ledger.
    ``--strategy`` names the roster entry to run, in place of the one
    ``strategies.yaml`` enables, so one release can run each lab shadow as its
    own serve on its own ledger (#1076). The
    environment tier comes from ``HERMES_ENV`` only. The signed
    pre-registration and the fee history are read from
    ``--config-dir``'s parent (the release directory on the VPS).

    Args:
        rest: Residual argv after the ``serve`` token.
        stream: Output stream for the help text.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = argparse.ArgumentParser(prog="hermes serve", add_help=False)
    add_config_dir_arg(parser)
    add_db_arg(parser)
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--new-ledger", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")

    try:
        parsed = parser.parse_args(rest)
    except SystemExit:
        err.write("ERROR: could not parse `hermes serve` arguments.\n")
        return 2

    if parsed.help:
        stream.write(
            "Usage: hermes serve [--config-dir DIR] [--db PATH] [--strategy NAME] [--new-ledger]\n"
            "\n"
            "Runs the shadow bar loop until SIGTERM or SIGINT arrives.\n"
            "--strategy runs that roster entry instead of the enabled one.\n"
            "A missing ledger is refused unless --new-ledger is given.\n"
            "The environment tier comes from HERMES_ENV.\n"
            "Exit codes: 0 clean shutdown, 1 runtime failure, 2 usage, 3 config,\n"
            "4 another hermes serve already runs on this ledger.\n"
        )
        return 0

    # One JSON object per line (ADR-008 §1): the daily line's journal count
    # and `hermes logs` parse serve's journal as JSON and skip anything else.
    configure_logging(env="prod")

    config = load_config_or_exit(parsed.config_dir, err)
    if isinstance(config, int):
        return config

    experiment_root = parsed.config_dir.resolve().parent
    try:
        if parsed.strategy is not None:
            config = serve_module.with_only_enabled(config, parsed.strategy)
        serve_module.check_startup(
            config,
            dict(os.environ),
            certification_store=JsonCertificationStore(
                serve_module.certification_dir_for(parsed.db)
            ),
            experiment_root=experiment_root,
        )
        if config.allow_mainnet:
            # ADR-024 §7: real money only once every position's stop is held by
            # the exchange, and this command builds no venue to hold it yet.
            raise serve_module.StartupError(
                "allow_mainnet is set, but every real-money position needs a stop held by "
                "the exchange (#1080) and hermes serve wires no exchange venue yet (#1220)"
            )
    except serve_module.StartupError as exc:
        err.write(f"ERROR: startup check failed: {exc}\n")
        return 3

    return asyncio.run(
        serve_main(
            config,
            db_path=parsed.db,
            experiment_root=experiment_root,
            err=err,
            new_ledger=parsed.new_ledger,
        )
    )


async def serve_main(
    config: Any,
    *,
    db_path: Path,
    experiment_root: Path | None = None,
    err: TextIO | None = None,
    new_ledger: bool = False,
) -> int:
    """Wire the composition root and run the bar loop to its exit code.

    Extracted from the argument handling so tests can drive the
    composition + run steps without going through argparse.

    Args:
        config: The loaded :class:`~hermes.infrastructure.config.HermesConfig`.
        db_path: Path to the SQLite ledger file.
        experiment_root: Directory holding the signed pre-registration and
            the fee history. ``None`` finds the repository root.
        err: Stream for the refusal line; defaults to ``sys.stderr``.
        new_ledger: Create the ledger when ``db_path`` does not exist.

    Returns:
        :func:`~hermes.entrypoints.serve.run`'s exit code: 0 on clean
        shutdown, 1 on a runtime failure (including a failed build), or
        :data:`EXIT_LEDGER_IN_USE` when another serve holds the ledger.
    """
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    try:
        composition = serve_module.build_composition(
            config, db_path=db_path, experiment_root=experiment_root, new_ledger=new_ledger
        )
    except serve_module.LedgerInUseError as exc:
        resolve_err(err).write(f"ERROR: {exc}\n")
        return EXIT_LEDGER_IN_USE
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
