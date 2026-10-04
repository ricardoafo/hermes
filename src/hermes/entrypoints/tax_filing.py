"""``hermes tax-filing --year YYYY``: the figures for Modelo 100 and Modelo 721.

Reads the ledger read-only, builds the year's transmissions (Modelo 100) and
31 December holdings (Modelo 721), and prints a Markdown report. With
``--out DIR`` it also writes ``report.md`` and the CSV files there. It never
submits anything and never reads the taxpayer profile.

Exit codes: 0 complete, 1 ledger failure, 2 usage, 3 config error,
4 incomplete (a sale with pending EUR values or a coin with no year-end price).

Reference: openspec/changes/36-modelo-100-721/change.md.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, TextIO

from returns.result import Failure

from hermes.adapters.ledger.filing_reader import LedgerFilingReader
from hermes.application.filing import (
    Custodian,
    FilingPack,
    build_filing_pack,
    render_csv_files,
    render_markdown,
)
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.errors import LedgerError
from hermes.domain.fx.eur_pricing import fill_date_madrid
from hermes.domain.value_objects.currency import Currency
from hermes.entrypoints import fx as fx_module
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.filing import load_filing_config, load_modelo721_history
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.time import now_utc

__all__ = ["EXIT_INCOMPLETE", "main"]

EXIT_INCOMPLETE: Final[int] = 4

_USAGE = (
    "Usage: hermes tax-filing --year YYYY [--db PATH] [--config-dir DIR] [--out DIR]\n"
    "                         [--price COIN=EUR ...] [--history PATH] [--live]\n"
    "\n"
    "Prints the figures for Modelo 100 (virtual-currency transmissions) and\n"
    "Modelo 721 (virtual currencies held abroad on 31 December). An estimate\n"
    "for your gestor: nothing is submitted.\n"
    "\n"
    "  --out DIR       also write report.md and CSV files to DIR\n"
    "  --price COIN=EUR  31 December value of one unit; wins over the ledger\n"
    "  --history PATH  your earlier 721 declarations (default\n"
    "                  ~/.config/hermes/modelo721.yaml or $HERMES_MODELO721_HISTORY)\n"
    "  --live          the ledger holds real fills (drops the shadow label)\n"
    "\n"
    "Exit codes: 0 complete, 1 ledger failure, 2 usage, 3 config, 4 incomplete.\n"
)


def main(
    argv: list[str],
    *,
    stream: TextIO,
    fx_factory: fx_module.FxFactory = fx_module.build_fx,
    env: Mapping[str, str] | None = None,
    today: date | None = None,
) -> int:
    """Parse ``argv`` (after the ``tax-filing`` token) and print the report.

    Args:
        argv: Arguments after ``tax-filing``.
        stream: Output stream.
        fx_factory: Builds the rate provider from config; tests inject a fake.
        env: Environment snapshot (defaults to ``os.environ``).
        today: Madrid date of the run (defaults to now).

    Returns:
        Process exit code.
    """
    parsed = _parse(argv, stream)
    if isinstance(parsed, int):
        return parsed
    overrides = _parse_prices(parsed.price, stream)
    if overrides is None:
        return 2
    built = _build(
        parsed,
        overrides,
        fx_factory=fx_factory,
        env=dict(os.environ if env is None else env),
        today=today if today is not None else fill_date_madrid(now_utc()),
        stream=stream,
    )
    if isinstance(built, int):
        return built
    report = render_markdown(built)
    stream.write(report)
    if parsed.out is not None:
        _write_outputs(parsed.out, report, render_csv_files(built))
        stream.write(f"\nWrote report.md and CSV files to {parsed.out}\n")
    return 0 if built.complete else EXIT_INCOMPLETE


def _build(
    parsed: argparse.Namespace,
    overrides: Mapping[Currency, Decimal],
    *,
    fx_factory: fx_module.FxFactory,
    env: dict[str, str],
    today: date,
    stream: TextIO,
) -> FilingPack | int:
    """Load config, read the ledger and build the pack; an exit code on failure."""
    try:
        config = load_config(parsed.config_dir)
        filing = load_filing_config(parsed.config_dir, env=env)
        history = load_modelo721_history(parsed.history, env=env)
    except ConfigError as exc:
        stream.write(f"ERROR: config load failed: {exc}\n")
        return 3

    fx = fx_factory(config.frankfurter)
    try:
        with LedgerFilingReader.open(parsed.db) as reader:
            built = build_filing_pack(
                parsed.year,
                reader,
                EurPricer(fx),
                rules=filing.modelo721.to_rules(),
                custodian=Custodian(name=filing.custodian.name, country=filing.custodian.country),
                previous=history.latest_before(parsed.year),
                overrides=overrides,
                today=today,
                shadow=not parsed.live,
            )
    except FileNotFoundError:
        stream.write(f"ERROR: no ledger at {parsed.db}\n")
        return 1
    except LedgerError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 1
    finally:
        close = getattr(fx, "close", None)
        if callable(close):
            close()
    if isinstance(built, Failure):
        stream.write(f"ERROR: ledger trades are inconsistent: {built.failure()}\n")
        return 1
    return built.unwrap()


def _write_outputs(out: Path, report: str, csv_files: Mapping[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report, encoding="utf-8")
    for name, content in csv_files.items():
        (out / name).write_text(content, encoding="utf-8")


def _parse(argv: list[str], stream: TextIO) -> argparse.Namespace | int:
    """Parse arguments; return an exit code when there is nothing to run."""
    if not argv:
        stream.write(_USAGE)
        return 2
    parser = argparse.ArgumentParser(prog="hermes tax-filing", add_help=False)
    parser.add_argument("--year", type=int)
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--price", action="append", default=[])
    parser.add_argument("--history", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        stream.write(f"ERROR: could not parse `hermes tax-filing` arguments.\n{_USAGE}")
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    if parsed.year is None:
        stream.write(f"ERROR: --year is required.\n{_USAGE}")
        return 2
    return parsed


def _parse_prices(raw: list[str], stream: TextIO) -> dict[Currency, Decimal] | None:
    """Parse ``COIN=EUR`` pairs; ``None`` (after an error line) on a bad one."""
    prices: dict[Currency, Decimal] = {}
    for item in raw:
        coin, sep, amount = item.partition("=")
        try:
            asset = Currency(coin.strip().upper())
            value = Decimal(amount.strip())
        except ValueError, InvalidOperation:
            value = None
        if not sep or value is None or not value.is_finite() or value < 0:
            stream.write(f"ERROR: --price expects COIN=EUR with a known coin, got {item!r}\n")
            return None
        prices[asset] = value
    return prices
