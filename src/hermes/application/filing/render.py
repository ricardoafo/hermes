"""Render a :class:`FilingPack` as a Markdown report and CSV files.

EUR amounts are rounded to cents (half up) for display only; the CSV files
carry the same rounded amounts the operator types into the forms, and the
per-sale annex lets anyone re-add them. Dates are Madrid calendar dates,
the tax calendar (ADR-016 item 2).

The report names form fields; it prints no box numbers or letter keys,
which change between AEAT campaigns (change D-4).

Reference: openspec/archive/36-modelo-100-721/change.md.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from zoneinfo import ZoneInfo

from hermes.application.filing.service import FilingPack
from hermes.domain.filing.modelo721 import Obligation

__all__ = ["CSV_FILES", "render_csv_files", "render_markdown"]

_CENT: Final[Decimal] = Decimal("0.01")
_MADRID: Final = ZoneInfo("Europe/Madrid")

CSV_FILES: Final[tuple[str, ...]] = (
    "modelo100-summary.csv",
    "modelo100-sales.csv",
    "modelo721.csv",
)

_GESTOR_DEFAULTS: Final[tuple[str, ...]] = (
    "Renta Web lines aggregate every sale of one coin for one kind of consideration; "
    "the annex lists each sale.",
    "A sale for USDT or another coin is a swap into another virtual currency, not a sale for fiat.",
    "Values follow the FIFO lots in EUR at the ECB rate of the fill date "
    "(USDT read as USD), fees included (ADR-016).",
    "Loss sales with a same-coin purchase within two months are flagged, "
    "not deferred (ADR-016 item 8).",
    "Modelo 721 values each coin at the last stored bar close in USDT on 31 December "
    "at the ECB rate of that day, unless an override is given.",
    "The 721 obligation is tested on the total of all coins: over the threshold the "
    "first time, then on an increase over the last declared total or a coin no longer held.",
    "The custodian's country for the 721 is left to the gestor.",
)

_KNOWN_GAPS: Final[tuple[str, ...]] = (
    "USDT is not yet its own FIFO asset (#405): USDT disposals and USDT balances "
    "are missing from both reports until it lands.",
)


def render_markdown(pack: FilingPack) -> str:
    """Return the full report as Markdown.

    Args:
        pack: The figures.

    Returns:
        The report text.
    """
    out: list[str] = [f"# Tax filing figures, tax year {pack.year}", ""]
    out += _banner(pack)
    out += _modelo100(pack)
    out += _modelo721(pack)
    out += ["## Defaults to confirm with the gestor", ""]
    out += [f"- {line}" for line in _GESTOR_DEFAULTS]
    out += ["", "## Known gaps", ""]
    out += [f"- {line}" for line in _KNOWN_GAPS]
    out.append("")
    return "\n".join(out)


def render_csv_files(pack: FilingPack) -> dict[str, str]:
    """Return each CSV file's name and content.

    Args:
        pack: The figures.

    Returns:
        A mapping of the names in :data:`CSV_FILES` to their CSV text.
    """
    m100 = pack.modelo100
    m721 = pack.modelo721
    prices = {p.asset: p for p in pack.prices}
    return {
        "modelo100-summary.csv": _csv(
            ["coin", "consideration", "sales", "units", "transmission_eur",
             "acquisition_eur", "gain_eur"],
            [
                [s.asset.value, s.kind.value, str(s.sales), _units(s.qty),
                 _eur(s.transmission_eur), _eur(s.acquisition_eur), _eur(s.gain_eur)]
                for s in m100.summaries
            ],
        ),
        "modelo100-sales.csv": _csv(
            ["sale_id", "date", "coin", "received", "units", "transmission_eur",
             "acquisition_eur", "gain_eur", "first_acquired"],
            [
                [t.sell_ref, _day(t.disposed_at), t.asset.value, t.received.value,
                 _units(t.qty), _eur(t.transmission_eur), _eur(t.acquisition_eur),
                 _eur(t.gain_eur), _day(t.first_acquired_at)]
                for t in m100.transmissions
            ],
        ),
        "modelo721.csv": _csv(
            ["coin", "origin", "units", "price_eur", "value_eur", "oldest_open_lot",
             "price_source"],
            [
                [line.asset.value, line.origin.value, _units(line.units),
                 _optional_eur(line.price_eur), _optional_eur(line.value_eur),
                 "" if line.oldest_lot_opened_at is None else _day(line.oldest_lot_opened_at),
                 prices[line.asset].source if line.asset in prices else ""]
                for line in m721.lines
            ],
        ),
    }  # fmt: skip


def _banner(pack: FilingPack) -> list[str]:
    lines = [
        "> **Estimate, not a filing.** Hermes submits nothing to AEAT. "
        "Check every figure with your gestor before you file.",
    ]
    if pack.shadow:
        lines.append(">")
        lines.append(
            "> **Shadow (simulated fills, no real tax due).** These figures come "
            "from paper trading and are not real transactions."
        )
    lines.append("")
    if pack.complete:
        lines.append("**Status: complete.**")
    else:
        reasons = []
        if pack.modelo100.pending:
            reasons.append(f"{len(pack.modelo100.pending)} sale(s) with pending EUR values")
        if pack.modelo721.missing_prices:
            missing = ", ".join(a.value for a in pack.modelo721.missing_prices)
            reasons.append(f"no year-end price for {missing}")
        lines.append(f"**Status: incomplete:** {'; '.join(reasons)}.")
    lines.append("")
    return lines


def _modelo100(pack: FilingPack) -> list[str]:
    m100 = pack.modelo100
    out = [
        "## Modelo 100: gains and losses from transmissions of virtual currencies",
        "",
    ]
    if not m100.summaries:
        out += ["No priced sales in this tax year.", ""]
    else:
        out += _table(
            ["Coin", "Consideration", "Sales", "Units", "Transmission value (EUR)",
             "Acquisition value (EUR)", "Gain or loss (EUR)"],
            [
                [s.asset.value, s.kind.value, str(s.sales), _units(s.qty),
                 _eur(s.transmission_eur), _eur(s.acquisition_eur), _eur(s.gain_eur)]
                for s in m100.summaries
            ],
        )  # fmt: skip
    out += [
        f"- Gains: {_eur(m100.gains_eur)} EUR",
        f"- Losses: {_eur(m100.losses_eur)} EUR",
        f"- Net: {_eur(m100.net_eur)} EUR",
        "",
    ]
    if m100.pending:
        out += ["### Sales left out (EUR values pending)", ""]
        out += ["Run `hermes fx backfill`, then this report again.", ""]
        out += [
            f"- Sale {p.sell_ref} on {_day(p.disposed_at)}: {_units(p.qty)} {p.asset.value}"
            for p in m100.pending
        ]
        out.append("")
    if m100.flags:
        out += ["### Two-month repurchase flags (for the gestor, not applied)", ""]
        out += [
            f"- Sale {f.sell_ref} on {_day(f.disposed_at)}: loss of {_eur(f.loss_eur)} EUR on "
            f"{f.asset.value}; purchases {', '.join(f.purchase_refs)} within two months"
            for f in m100.flags
        ]
        out.append("")
    if m100.transmissions:
        out += ["### Annex: every sale", ""]
        out += _table(
            ["Sale", "Date", "Coin", "Received", "Units", "Transmission (EUR)",
             "Acquisition (EUR)", "Gain or loss (EUR)", "First acquired"],
            [
                [t.sell_ref, _day(t.disposed_at), t.asset.value, t.received.value,
                 _units(t.qty), _eur(t.transmission_eur), _eur(t.acquisition_eur),
                 _eur(t.gain_eur), _day(t.first_acquired_at)]
                for t in m100.transmissions
            ],
        )  # fmt: skip
    return out


def _modelo721(pack: FilingPack) -> list[str]:
    m721 = pack.modelo721
    country = pack.custodian.country or "to confirm with the gestor"
    out = [
        "## Modelo 721: virtual currencies held abroad on 31 December",
        "",
        f"**Obligation: {m721.obligation.value}.** {m721.reason[0].upper()}{m721.reason[1:]}.",
        "",
        "- Declarant: name and NIF are not stored by hermes; fill them in on the form.",
        f"- Custodian: {pack.custodian.name}, country {country}.",
        f"- Threshold: {m721.rules.threshold_eur} EUR the first time; "
        f"then an increase over {m721.rules.increase_eur} EUR.",
    ]
    if m721.previous is not None:
        out.append(
            f"- Last declaration on file: {m721.previous.year}, "
            f"total {_eur(m721.previous.total_eur)} EUR."
        )
    else:
        out.append("- Last declaration on file: none.")
    out.append("")
    if not m721.lines:
        out += ["No coins held on 31 December.", ""]
        return out
    prices = {p.asset: p for p in pack.prices}
    out += _table(
        ["Coin", "Origin", "Units", "Price (EUR)", "Value (EUR)", "Oldest open lot",
         "Price source"],
        [
            [line.asset.value, line.origin.value, _units(line.units),
             _optional_eur(line.price_eur), _optional_eur(line.value_eur),
             "" if line.oldest_lot_opened_at is None else _day(line.oldest_lot_opened_at),
             prices[line.asset].source if line.asset in prices else "no longer held"]
            for line in m721.lines
        ],
    )  # fmt: skip
    out += [f"- Total: {_eur(m721.total_eur)} EUR", ""]
    if m721.obligation is Obligation.REQUIRED:
        out += [
            "After you file, add the declared values to your local 721 history so next "
            "year's increase test uses them.",
            "",
        ]
    return out


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    out.append("")
    return out


def _csv(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _eur(amount: Decimal) -> str:
    return str(amount.quantize(_CENT, rounding=ROUND_HALF_UP))


def _optional_eur(amount: Decimal | None) -> str:
    return "missing" if amount is None else _eur(amount)


def _units(qty: Decimal) -> str:
    return format(qty.normalize(), "f")


def _day(at: datetime) -> str:
    return at.astimezone(_MADRID).date().isoformat()
