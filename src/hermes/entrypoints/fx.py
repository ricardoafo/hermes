"""``hermes fx backfill`` — fill pending EUR values in the ledger.

Opens the SQLite ledger, prices every trade stored with ``eur_status =
'pending'`` from the ECB rate of its fill date (Frankfurter, USDT read as USD),
and re-projects the lots and consumptions that depended on it. Safe to run any
number of times: a trade whose rate is still missing stays pending.

Exit codes: 0 done (some rows may stay pending), 1 ledger failure, 2 usage,
3 config error.

Reference: change ``trade-eur-values`` R-3, ADR-016 item 5.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from hermes.adapters.fx.frankfurter import FrankfurterAdapter
from hermes.adapters.fx.frankfurter import FrankfurterConfig as FrankfurterWireConfig
from hermes.adapters.fx.rate_limiter import RateLimitConfig
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.ledger.eur_backfill import BackfillReport, backfill_pending_eur
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.errors import LedgerError
from hermes.domain.fx.eur_pricing import fill_date_madrid
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.config.models import FrankfurterConfig
from hermes.infrastructure.time import now_utc
from hermes.ports.fx import FxRateProviderPort

__all__ = ["build_fx", "main", "run_backfill"]

_USAGE = (
    "Usage: hermes fx backfill [--config-dir DIR] [--db PATH]\n"
    "\n"
    "Prices every trade whose EUR values are pending from the ECB rate of its\n"
    "fill date, then re-projects its lots and consumptions.\n"
    "Exit codes: 0 done, 1 ledger failure, 2 usage, 3 config.\n"
)

FxFactory = Callable[[FrankfurterConfig], FxRateProviderPort]


def build_fx(config: FrankfurterConfig) -> FxRateProviderPort:
    """Build the Frankfurter adapter that prices USDT as USD.

    Args:
        config: The ``frankfurter.yaml`` section of the loaded config.

    Returns:
        A Frankfurter adapter with the USDT -> USD wire code.
    """
    return FrankfurterAdapter.create(
        FrankfurterWireConfig(
            base_url=config.base_url,
            timeout_sec=config.timeout_sec,
            rate_limit=RateLimitConfig(max_weight=config.requests_per_minute, window_seconds=60),
            wire_codes=FrankfurterWireConfig.USDT_AS_USD,
        )
    )


def run_backfill(db_path: Path, fx: FxRateProviderPort) -> BackfillReport:
    """Run one backfill against the ledger at ``db_path``.

    Args:
        db_path: SQLite ledger file.
        fx: Rate provider.

    Returns:
        The backfill outcome.

    Raises:
        LedgerError: When the ledger cannot be read or written.
    """
    connection = sqlite3.connect(db_path)
    try:
        ledger = SqliteLedger(connection)
        return backfill_pending_eur(ledger, EurPricer(fx), today=fill_date_madrid(now_utc()))
    finally:
        connection.close()


def main(argv: list[str], *, stream: TextIO, fx_factory: FxFactory = build_fx) -> int:
    """Parse ``argv`` (after the ``fx`` token) and run the subcommand.

    Args:
        argv: Arguments after ``fx``.
        stream: Output stream.
        fx_factory: Builds the rate provider from config; tests inject a fake.

    Returns:
        Process exit code.
    """
    parsed, code = _parse(argv, stream)
    if parsed is None:
        return code

    try:
        config = load_config(parsed.config_dir)
    except ConfigError as exc:
        stream.write(f"ERROR: config load failed: {exc}\n")
        return 3

    fx = fx_factory(config.frankfurter)
    try:
        report = run_backfill(parsed.db, fx)
    except LedgerError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 1
    finally:
        close = getattr(fx, "close", None)
        if callable(close):
            close()
    stream.write(f"filled={len(report.filled)} pending={len(report.pending)}\n")
    return 0


def _parse(argv: list[str], stream: TextIO) -> tuple[argparse.Namespace | None, int]:
    """Parse ``fx`` arguments; return ``(None, exit_code)`` when there is nothing to run."""
    if not argv or argv[0] in {"-h", "--help"}:
        stream.write(_USAGE)
        return None, 0 if argv else 2
    if argv[0] != "backfill":
        stream.write(f"ERROR: unknown `hermes fx` command {argv[0]!r}.\n{_USAGE}")
        return None, 2

    parser = argparse.ArgumentParser(prog="hermes fx backfill", add_help=False)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv[1:])
    except SystemExit:
        stream.write("ERROR: could not parse `hermes fx backfill` arguments.\n")
        return None, 2
    if parsed.help:
        stream.write(_USAGE)
        return None, 0
    return parsed, 0
