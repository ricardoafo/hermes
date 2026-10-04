"""``hermes tax`` — print the yearly tax estimate of hermes gains.

Reads the ledger's EUR trades, the savings scale from ``config/tax_scales.yaml``
and the local taxpayer profile, and prints the estimate for one Madrid tax year
as text or JSON. Every output is labelled an estimate, and a shadow estimate
says no real tax is due (ADR-016 decision 10). Profile values are never logged
(decision 12); only figures derived from them are printed, to this terminal.

Exit codes: 0 printed (an estimate or a pending notice), 1 ledger failure,
2 usage, 3 config error.

Reference: change ``tax-estimates``, ADR-016.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Final, TextIO

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.console.views import Tax, TaxEstimate, TaxPending
from hermes.application.tax.estimate import (
    ESTIMATE_LABEL,
    SHADOW_LABEL,
    TaxService,
    TaxYearEstimate,
)
from hermes.domain.errors import LedgerError
from hermes.domain.tax.netting import CarriedLoss, TaxpayerYear
from hermes.domain.tax.scale import SavingsBand, SavingsScale
from hermes.domain.tax.year_position import tax_year_of
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR
from hermes.infrastructure.config.tax import SavingsScale as SavingsScaleConfig
from hermes.infrastructure.config.tax import (
    TaxpayerProfile,
    TaxScalesConfig,
    load_tax_scales,
    load_taxpayer_profile,
    resolve_taxpayer_profile_path,
)
from hermes.infrastructure.time import now_utc

__all__ = ["main", "to_domain_scales", "to_domain_taxpayer"]

_USAGE: Final[str] = (
    "Usage: hermes tax [--year YYYY] [--json] [--price ASSET=EUR]... "
    "[--db PATH] [--config-dir DIR]\n"
    "\n"
    "Prints the estimated tax on hermes gains for one tax year (Madrid calendar\n"
    "year, default: this year). --price gives the current EUR price of an open\n"
    "position for 'tax if sold now'. Figures are estimates, never a filing.\n"
    "Exit codes: 0 printed, 1 ledger failure, 2 usage, 3 config.\n"
)

# Hermes runs shadow only in MVP1: no live exchange is wired, so no real tax is due.
_IS_SHADOW: Final[bool] = True
_CENT: Final[Decimal] = Decimal("0.01")


def to_domain_scales(config: TaxScalesConfig) -> dict[int, SavingsScale]:
    """Map the configured savings scales onto domain scales.

    Args:
        config: Contents of ``tax_scales.yaml``.

    Returns:
        Savings scale per tax year.
    """
    return {year: _scale(scale) for year, scale in config.savings.items()}


def _scale(scale: SavingsScaleConfig) -> SavingsScale:
    return SavingsScale(
        bands=tuple(SavingsBand(up_to=band.up_to_eur, rate=band.rate) for band in scale.bands)
    )


def to_domain_taxpayer(
    profile: TaxpayerProfile, year: int, *, default_personal_minimum: Decimal
) -> TaxpayerYear | None:
    """Map the operator's profile for ``year`` onto the domain type.

    Args:
        profile: The loaded taxpayer profile.
        year: Tax year.
        default_personal_minimum: Used when the profile gives no minimum.

    Returns:
        The year's figures, or ``None`` when the profile has no entry for ``year``.
    """
    if not profile.has_year(year):
        return None
    figures = profile.for_year(year)
    minimum = figures.personal_minimum_eur
    return TaxpayerYear(
        general_income=figures.general_income_eur,
        personal_minimum=default_personal_minimum if minimum is None else minimum,
        other_savings_income=figures.other_savings_income_eur,
        other_capital_gains=figures.other_capital_gains_eur,
        carried_losses=tuple(
            CarriedLoss(origin_year=loss.origin_year, amount=loss.amount_eur)
            for loss in figures.losses_carried_forward
        ),
    )


def main(
    argv: list[str],
    *,
    stream: TextIO,
    now: Callable[[], datetime] = now_utc,
    env: Mapping[str, str] | None = None,
) -> int:
    """Parse ``argv`` (after the ``tax`` token), estimate and print.

    Args:
        argv: Arguments after ``tax``.
        stream: Output stream.
        now: Clock; tests inject a fixed instant.
        env: Env snapshot for the profile path (defaults to ``os.environ``).

    Returns:
        Process exit code.
    """
    parsed = _parse(argv, stream)
    if isinstance(parsed, int):
        return parsed
    args, prices = parsed

    as_of = now()
    year: int = args.year if args.year is not None else tax_year_of(as_of)
    try:
        scales_config = load_tax_scales(args.config_dir)
        profile = load_taxpayer_profile(
            resolve_taxpayer_profile_path(None if env is None else dict(env))
        )
    except ConfigError as exc:
        stream.write(f"ERROR: config load failed: {exc}\n")
        return 3

    db: Path = args.db
    if not db.is_file():
        stream.write(f"ERROR: ledger not found at {db}\n")
        return 1
    connection = sqlite3.connect(db)
    try:
        result = TaxService(SqliteLedger(connection)).estimate(
            year=year,
            scales=to_domain_scales(scales_config),
            taxpayer=to_domain_taxpayer(
                profile, year, default_personal_minimum=scales_config.personal_minimum_eur
            ),
            default_personal_minimum=scales_config.personal_minimum_eur,
            prices=prices,
            as_of=as_of,
            is_shadow=_IS_SHADOW,
        )
    except (LedgerError, sqlite3.Error) as exc:
        stream.write(f"ERROR: cannot read ledger {db}: {exc}\n")
        return 1
    finally:
        connection.close()

    if args.json:
        stream.write(json.dumps(_as_json(year, result), indent=2) + "\n")
    else:
        stream.write(_as_text(year, result))
    return 0


def _parse(argv: list[str], stream: TextIO) -> tuple[argparse.Namespace, dict[str, Decimal]] | int:
    """Parse arguments; return an exit code when there is nothing to run."""
    parser = argparse.ArgumentParser(prog="hermes tax", add_help=False)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--price", action="append", default=[])
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        stream.write(f"ERROR: could not parse `hermes tax` arguments.\n{_USAGE}")
        return 2
    if args.help:
        stream.write(_USAGE)
        return 0
    prices: dict[str, Decimal] = {}
    for raw in args.price:
        asset, sep, amount = str(raw).partition("=")
        try:
            value = Decimal(amount)
        except InvalidOperation:
            value = Decimal("NaN")
        if not sep or not asset.strip() or not value.is_finite() or value < 0:
            stream.write(
                f"ERROR: --price expects ASSET=EUR with a non-negative amount, got {raw!r}\n"
            )
            return 2
        prices[asset.strip().upper()] = value
    return args, prices


def _label(is_shadow: bool) -> str:
    return f"{ESTIMATE_LABEL} · {SHADOW_LABEL}" if is_shadow else ESTIMATE_LABEL


def _cents(amount: Decimal) -> Decimal:
    rounded = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    return rounded if rounded != 0 else Decimal("0.00")


def _money(amount: Decimal) -> str:
    return f"{_cents(amount)} EUR"


def _figure(tax: Tax) -> str:
    return _money(tax.amount) if isinstance(tax, TaxEstimate) else tax.label


def _as_text(year: int, result: TaxYearEstimate | TaxPending) -> str:
    if isinstance(result, TaxPending):
        return f"hermes tax {year} ({_label(_IS_SHADOW)})\n{result.label}\n"
    rows = [
        ("realised gain", _money(result.realised_gain)),
        ("hermes tax", _figure(result.hermes_tax)),
        ("today's tax", _figure(result.today_tax)),
        ("tax if sold now", _figure(result.tax_if_sold_now)),
        *((f"  {p.asset}", _figure(p.tax_if_sold_now)) for p in result.per_position_if_sold_now),
        ("carried losses used", _money(result.carry_forward_used)),
        (
            "carried losses left",
            ", ".join(f"{c.origin_year}: {_money(c.amount)}" for c in result.carry_forward_left)
            or "none",
        ),
        ("band", _band_text(result)),
    ]
    width = max(len(name) for name, _ in rows)
    lines = [f"hermes tax {year} ({result.label})"]
    lines.extend(f"{name.ljust(width)}  {value}" for name, value in rows)
    lines.extend(f"flag: {flag}" for flag in result.flags)
    lines.extend(f"notice: {notice}" for notice in result.notices)
    return "\n".join(lines) + "\n"


def _band_text(result: TaxYearEstimate) -> str:
    band = result.band
    if band.next_rate is None or band.room_to_next is None:
        return f"{band.rate}% ({band.method}, top band)"
    return f"{band.rate}% ({band.method}; {band.next_rate}% after {_money(band.room_to_next)} more)"


def _optional_money(tax: Tax) -> str | None:
    return str(_cents(tax.amount)) if isinstance(tax, TaxEstimate) else None


def _pending_reason(tax: Tax) -> str | None:
    return tax.label if isinstance(tax, TaxPending) else None


def _as_json(year: int, result: TaxYearEstimate | TaxPending) -> dict[str, object]:
    if isinstance(result, TaxPending):
        return {
            "year": year,
            "status": "pending",
            "reason": result.label,
            "label": _label(_IS_SHADOW),
            "is_estimate": True,
            "is_shadow": _IS_SHADOW,
        }
    band = result.band
    return {
        "year": year,
        "status": "estimate",
        "label": result.label,
        "is_estimate": result.is_estimate,
        "is_shadow": result.is_shadow,
        "realised_gain_eur": str(_cents(result.realised_gain)),
        "hermes_tax_eur": str(_cents(result.hermes_tax.amount)),
        "today_tax_eur": str(_cents(result.today_tax.amount)),
        "tax_if_sold_now_eur": _optional_money(result.tax_if_sold_now),
        "tax_if_sold_now_pending": _pending_reason(result.tax_if_sold_now),
        "positions": [
            {
                "asset": p.asset,
                "unrealised_gain_eur": None
                if p.unrealised_gain is None
                else str(_cents(p.unrealised_gain)),
                "tax_if_sold_now_eur": _optional_money(p.tax_if_sold_now),
                "pending": _pending_reason(p.tax_if_sold_now),
            }
            for p in result.per_position_if_sold_now
        ],
        "carry_forward_used_eur": str(_cents(result.carry_forward_used)),
        "carry_forward_left": [
            {"origin_year": c.origin_year, "amount_eur": str(_cents(c.amount))}
            for c in result.carry_forward_left
        ],
        "band": {
            "rate_pct": str(band.rate),
            "next_rate_pct": None if band.next_rate is None else str(band.next_rate),
            "room_to_next_eur": None
            if band.room_to_next is None
            else str(_cents(band.room_to_next)),
            "method": band.method,
        },
        "flags": list(result.flags),
        "notices": list(result.notices),
    }
