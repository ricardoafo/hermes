"""``hermes tax-pack``: keep each tax year's filing pack up to date.

Writes ``<out>/<year>/`` with the Renta Web sheet and dossier as PDF, the
Modelo 100 and 721 report and CSV files, the trail from each sale to its
lots, the readiness checklist, the changes since the previous refresh and a
checksum manifest. Adds the ``hermes tax``
estimate only where the taxpayer profile file exists (change 568-tax-pack
D-4). A closed year whose pack is ready is frozen and never rewritten (D-2).
Nothing is ever submitted.

Without ``--year`` it refreshes the current Madrid tax year and the previous
one unless that is frozen (D-3); the daily timer runs exactly that.

Exit codes: 0 written, 1 ledger failure, 2 usage, 3 config error,
5 the requested year is frozen (nothing written). ERROR and WARNING lines go
to ``err`` (stderr), the report to ``stream``.

Reference: openspec/archive/568-tax-pack/change.md.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, TextIO

from returns.result import Failure

from hermes.adapters.documents.pdf_writer import render_pdf
from hermes.adapters.ledger.filing_reader import LedgerFilingReader
from hermes.adapters.ledger.trade_rates_reader import LedgerTradeRates
from hermes.application.filing import Custodian, FilingPack, build_filing_pack
from hermes.application.filing.pack import (
    CHANGES_FILE,
    ESTIMATE_FILE,
    FIGURES_FILE,
    FROZEN_FILE,
    MANIFEST_FILE,
    PackOutput,
    build_pack_output,
    collect_trail,
    render_frozen_marker,
    render_manifest,
)
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.errors import LedgerError
from hermes.domain.filing.renta_campaign import campaign_for
from hermes.domain.filing.tax_year import tax_year_of
from hermes.domain.filing.trail import TrailLine
from hermes.domain.fx.eur_pricing import fill_date_madrid
from hermes.domain.value_objects.currency import Currency
from hermes.entrypoints import fx as fx_module
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints import tax as tax_module
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.filing import (
    FilingConfig,
    Modelo721History,
    load_filing_config,
    load_modelo721_history,
)
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.config.renta import RentaCampaignsConfig, load_renta_campaigns
from hermes.infrastructure.config.root import HermesConfig
from hermes.infrastructure.config.tax import resolve_taxpayer_profile_path
from hermes.infrastructure.time import now_utc

__all__ = [
    "DEFAULT_PACK_DIR",
    "EXIT_FROZEN",
    "PACK_DIR_ENV",
    "EstimateRunner",
    "main",
    "out_root",
    "run_estimate",
]

EXIT_FROZEN: Final[int] = 5
PACK_DIR_ENV: Final[str] = "HERMES_TAX_PACK_DIR"
DEFAULT_PACK_DIR: Final[Path] = Path("~/.local/share/hermes/tax-packs")

EstimateRunner = Callable[[list[str], Mapping[str, str], Callable[[], datetime]], str | None]
"""Runs ``hermes tax`` with the given arguments; its text, or ``None`` on a non-zero exit."""

_USAGE: Final[str] = (
    "Usage: hermes tax-pack fetch --year YYYY [--out DIR]   (see below)\n"
    "       hermes tax-pack [--year YYYY] [--out DIR] [--db PATH] [--config-dir DIR]\n"
    "                       [--price COIN=EUR ...] [--history PATH] [--live]\n"
    "\n"
    "Refreshes the tax filing pack of a tax year in DIR/<year>/: the Modelo 100\n"
    "and 721 figures, the trail from each sale to its lots, a readiness\n"
    "checklist and what changed since the last refresh. Without --year it\n"
    "refreshes this year and last year (unless frozen). A closed year that is\n"
    "ready is frozen. Hermes submits nothing.\n"
    "\n"
    "  --out DIR       pack folder (default $HERMES_TAX_PACK_DIR or\n"
    "                  ~/.local/share/hermes/tax-packs)\n"
    "  --price COIN=EUR  31 December value of one unit; wins over the ledger\n"
    "  --history PATH  your earlier 721 declarations\n"
    "  --live          the ledger holds real fills (drops the shadow label)\n"
    "\n"
    "`fetch` copies the server's pack for a year to DIR/server/YEAR/ over ssh\n"
    "and checks it against its manifest (exit 4 on a mismatch).\n"
    "\n"
    "Exit codes: 0 written, 1 ledger failure, 2 usage, 3 config, 5 frozen.\n"
)


def run_estimate(
    argv: list[str], env: Mapping[str, str], now: Callable[[], datetime]
) -> str | None:
    """Run ``hermes tax`` in-process and return its text, or ``None`` when it failed.

    Args:
        argv: Arguments after ``tax``.
        env: Environment snapshot, for the profile path.
        now: Clock.

    Returns:
        The estimate text, or ``None`` on a non-zero exit.
    """
    buffer = io.StringIO()
    code = tax_module.main(argv, stream=buffer, now=now, env=env)
    return buffer.getvalue() if code == 0 else None


def main(
    argv: list[str],
    *,
    stream: TextIO,
    fx_factory: fx_module.FxFactory = fx_module.build_fx,
    env: Mapping[str, str] | None = None,
    now: Callable[[], datetime] = now_utc,
    estimate_runner: EstimateRunner = run_estimate,
    err: TextIO | None = None,
) -> int:
    """Parse ``argv`` (after the ``tax-pack`` token) and refresh the packs.

    Args:
        argv: Arguments after ``tax-pack``.
        stream: Output stream.
        err: Stream for ERROR and WARNING lines; defaults to ``sys.stderr``.
        fx_factory: Builds the rate provider from config; tests inject a fake.
        env: Environment snapshot (defaults to ``os.environ``).
        now: Clock; tests inject a fixed instant.
        estimate_runner: Runs ``hermes tax``; tests inject a fake.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parsed = _parse(argv, stream, err)
    if isinstance(parsed, int):
        return parsed
    overrides = _parse_prices(parsed.price, err)
    if overrides is None:
        return 2
    snapshot = dict(os.environ if env is None else env)
    try:
        config = load_config(parsed.config_dir)
        filing = load_filing_config(parsed.config_dir, env=snapshot)
        history = load_modelo721_history(parsed.history, env=snapshot)
        campaigns = load_renta_campaigns(parsed.config_dir, env=snapshot)
    except ConfigError as exc:
        err.write(f"ERROR: config load failed: {exc}\n")
        return 3

    refreshed_at = now()
    run = _Run(
        parsed=parsed,
        config=config,
        filing=filing,
        history=history,
        campaigns=campaigns,
        overrides=overrides,
        out_root=out_root(parsed.out, snapshot),
        fx_factory=fx_factory,
        env=snapshot,
        now=now,
        refreshed_at=refreshed_at,
        estimate_runner=estimate_runner,
        err=err,
    )
    current = tax_year_of(refreshed_at)
    explicit = parsed.year is not None
    years = [parsed.year] if explicit else [current - 1, current]
    for year in years:
        if (run.out_root / str(year) / FROZEN_FILE).is_file():
            stream.write(f"Tax pack {year} is frozen; nothing written.\n")
            if explicit:
                return EXIT_FROZEN
            continue
        code = _refresh(year, run, stream)
        if code != 0:
            return code
    return 0


@dataclass(frozen=True, slots=True)
class _Run:
    """Everything one invocation shares across the years it refreshes."""

    parsed: argparse.Namespace
    config: HermesConfig
    filing: FilingConfig
    history: Modelo721History
    campaigns: RentaCampaignsConfig
    overrides: Mapping[Currency, Decimal]
    out_root: Path
    fx_factory: fx_module.FxFactory
    env: Mapping[str, str]
    now: Callable[[], datetime]
    refreshed_at: datetime
    estimate_runner: EstimateRunner
    err: TextIO


def _refresh(year: int, run: _Run, stream: TextIO) -> int:
    """Rebuild and write one year's pack; an exit code."""
    today = fill_date_madrid(run.refreshed_at)
    built = _read_ledger(year, run, today)
    if isinstance(built, int):
        return built
    pack, trail = built
    parsed = run.parsed
    estimate = None
    if resolve_taxpayer_profile_path(dict(run.env)).is_file():
        estimate = run.estimate_runner(
            ["--year", str(year), "--db", str(parsed.db), "--config-dir", str(parsed.config_dir)],
            run.env,
            run.now,
        )
        if estimate is None:
            run.err.write(
                f"WARNING: `hermes tax` failed; pack {year} written without the estimate.\n"
            )
    directory = run.out_root / str(year)
    output = build_pack_output(
        pack,
        trail,
        campaign=campaign_for(year, run.campaigns.layouts()),
        estimate=estimate,
        previous_figures=_previous_figures(directory / FIGURES_FILE),
        today=today,
        refreshed_at=run.refreshed_at,
    )
    _write(directory, output, year=year, refreshed_at=run.refreshed_at)
    _report(stream, year, directory, output)
    return 0


def _read_ledger(
    year: int, run: _Run, today: date
) -> tuple[FilingPack, tuple[TrailLine, ...]] | int:
    """Build the year's figures and trail; an exit code on failure."""
    parsed, filing = run.parsed, run.filing
    fx = run.fx_factory(run.config.frankfurter)
    try:
        with (
            LedgerFilingReader.open(parsed.db) as reader,
            LedgerTradeRates.open(parsed.db) as rates,
        ):
            built = build_filing_pack(
                year,
                reader,
                EurPricer(fx),
                rules=filing.modelo721.to_rules(),
                custodian=Custodian(name=filing.custodian.name, country=filing.custodian.country),
                previous=run.history.latest_before(year),
                overrides=run.overrides,
                today=today,
                shadow=not parsed.live,
            )
            trail = collect_trail(year, reader, rates)
    except FileNotFoundError:
        run.err.write(f"ERROR: no ledger at {parsed.db}\n")
        return 1
    except LedgerError as exc:
        run.err.write(f"ERROR: {exc}\n")
        return 1
    finally:
        close = getattr(fx, "close", None)
        if callable(close):
            close()
    if isinstance(built, Failure):
        run.err.write(f"ERROR: ledger trades are inconsistent: {built.failure()}\n")
        return 1
    return built.unwrap(), trail


def _write(directory: Path, output: PackOutput, *, year: int, refreshed_at: datetime) -> None:
    """Write the pack; every file is replaced atomically, the manifest last."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in output.files.items():
        _replace(directory / name, content)
    for name, document in output.documents.items():
        _replace(directory / name, render_pdf(document, created_at=refreshed_at))
    if ESTIMATE_FILE not in output.files:
        (directory / ESTIMATE_FILE).unlink(missing_ok=True)
    if output.changes_entry is not None:
        changes = directory / CHANGES_FILE
        previous = (
            changes.read_text(encoding="utf-8")
            if changes.is_file()
            else (f"# Changes to the tax year {year} pack\n\n")
        )
        _replace(changes, previous + output.changes_entry)
    if output.freeze:
        _replace(directory / FROZEN_FILE, render_frozen_marker(year, refreshed_at))
    contents = {
        path.name: path.read_bytes()
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name != MANIFEST_FILE and not path.name.endswith(".tmp")
    }
    _replace(directory / MANIFEST_FILE, render_manifest(year, refreshed_at, contents))


def _replace(path: Path, content: str | bytes) -> None:
    """Write ``content`` next to ``path`` and rename it in place; text as UTF-8."""
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    temporary.replace(path)


def _previous_figures(path: Path) -> dict[str, str] | None:
    """Return the last refresh's figures, or ``None`` when absent or unreadable."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    # Python 3.14 form (PEP 758, no parentheses); ruff format writes it this way.
    except OSError, ValueError:
        return None
    if not isinstance(loaded, dict):
        return None
    return {str(key): str(value) for key, value in loaded.items()}


def _report(stream: TextIO, year: int, directory: Path, output: PackOutput) -> None:
    lines = [f"Tax pack {year}: {output.readiness.status.value}. Written to {directory}"]
    lines += [f"  changed: {line}" for line in output.changes]
    lines += [
        f"  [{'x' if item.done else ' '}] {item.text}"
        for item in output.readiness.items
        if item.blocking
    ]
    if output.freeze:
        lines.append(f"  Frozen: later refreshes leave {directory} untouched.")
    stream.write("\n".join(lines) + "\n")


def out_root(out: Path | None, env: Mapping[str, str]) -> Path:
    if out is not None:
        return out
    configured = env.get(PACK_DIR_ENV)
    return (Path(configured) if configured else DEFAULT_PACK_DIR).expanduser()


def _parse(argv: list[str], stream: TextIO, err: TextIO) -> argparse.Namespace | int:
    """Parse arguments; return an exit code when there is nothing to run."""
    parser = argparse.ArgumentParser(prog="hermes tax-pack", add_help=False)
    parser.add_argument("--year", type=int)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--price", action="append", default=[])
    parser.add_argument("--history", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write(f"ERROR: could not parse `hermes tax-pack` arguments.\n{_USAGE}")
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    return parsed


def _parse_prices(raw: list[str], err: TextIO) -> dict[Currency, Decimal] | None:
    """Parse ``COIN=EUR`` pairs; ``None`` (after an error line) on a bad one."""
    prices: dict[Currency, Decimal] = {}
    for item in raw:
        coin, sep, amount = item.partition("=")
        try:
            asset = Currency(coin.strip().upper())
            value = Decimal(amount.strip())
        # Python 3.14 form (PEP 758, no parentheses); ruff format writes it this way.
        except ValueError, InvalidOperation:
            value = None
        if not sep or value is None or not value.is_finite() or value < 0:
            err.write(f"ERROR: --price expects COIN=EUR with a known coin, got {item!r}\n")
            return None
        prices[asset] = value
    return prices
