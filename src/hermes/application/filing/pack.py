"""Build the year's tax filing pack: files, headline figures, checklist, changes.

Composes what already exists (change 568-tax-pack D-1): the Modelo 100 and
721 report of :func:`~hermes.application.filing.service.build_filing_pack`,
the ``hermes tax`` estimate when the caller has one, and adds the trail, the
readiness checklist and the changes since the previous refresh. Nothing here
touches the disk; the ``tax-pack`` entrypoint writes what this returns.

Reference: openspec/archive/568-tax-pack/change.md.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from hermes.application.filing.document import Document
from hermes.application.filing.render import csv_text as _csv
from hermes.application.filing.render import madrid_day as _day
from hermes.application.filing.render import render_csv_files, render_markdown
from hermes.application.filing.renta_sheet import (
    DOSSIER_FILE,
    RENTA_SHEET_FILE,
    build_dossier,
    build_renta_sheet,
)
from hermes.application.filing.service import FilingPack
from hermes.domain.filing.pack_changes import figure_changes
from hermes.domain.filing.readiness import PackStatus, Readiness, assess_readiness
from hermes.domain.filing.renta_campaign import RentaCampaign
from hermes.domain.filing.tax_year import tax_year_bounds
from hermes.domain.filing.trail import TradeRate, TrailLine, build_trail
from hermes.domain.value_objects.cents import cents_text as _eur
from hermes.domain.value_objects.time import MADRID
from hermes.ports.filing import FilingLedgerReaderPort
from hermes.ports.tax_pack import TradeRatesPort

__all__ = [
    "CHANGES_FILE",
    "ESTIMATE_FILE",
    "FIGURES_FILE",
    "FROZEN_FILE",
    "MANIFEST_FILE",
    "PackOutput",
    "build_pack_output",
    "collect_trail",
    "render_frozen_marker",
    "render_manifest",
]

CHANGES_FILE: Final[str] = "changes.md"
ESTIMATE_FILE: Final[str] = "tax-estimate.txt"
FIGURES_FILE: Final[str] = "figures.json"
FROZEN_FILE: Final[str] = "FROZEN"
MANIFEST_FILE: Final[str] = "manifest.json"
_README_FILE: Final[str] = "README.md"
_REPORT_FILE: Final[str] = "report.md"
_TRAIL_FILE: Final[str] = "trail.csv"


_FILE_NOTES: Final[tuple[tuple[str, str], ...]] = (
    (RENTA_SHEET_FILE, "the Renta Web entries to copy, in Spanish, and the 721 fields when due"),
    (DOSSIER_FILE, "every sale and the lots behind it, to keep in case Hacienda asks"),
    (_REPORT_FILE, "Modelo 100 and 721 figures per form field, with every sale"),
    ("modelo100-summary.csv", "one Renta Web line per coin and kind of consideration"),
    ("modelo100-sales.csv", "every sale of the year"),
    ("modelo721.csv", "coins held on 31 December"),
    (_TRAIL_FILE, "each sale back to the lots, fees and ECB rates behind it"),
    (ESTIMATE_FILE, "the `hermes tax` estimate from your local profile (keep it local)"),
    (FIGURES_FILE, "the headline figures the next refresh compares against"),
    (CHANGES_FILE, "every refresh that changed a figure, newest last"),
    (MANIFEST_FILE, "sha256 of every other file"),
    (FROZEN_FILE, "present once the year is closed and ready; delete it to reopen"),
)


@dataclass(frozen=True, slots=True)
class PackOutput:
    """What one refresh writes.

    Attributes:
        files: File name to text content, rewritten on every refresh.
        documents: File name to printable sheet, rewritten on every refresh;
            the entrypoint renders each one (as PDF).
        readiness: The checklist and status.
        changes: Figure changes since the previous refresh; empty when none.
        changes_entry: Markdown to append to ``changes.md``, or ``None`` when
            nothing changed.
    """

    files: dict[str, str]
    documents: dict[str, Document]
    readiness: Readiness
    changes: tuple[str, ...]
    changes_entry: str | None

    @property
    def freeze(self) -> bool:
        """Return whether this refresh closes the pack."""
        return self.readiness.status is PackStatus.READY


def collect_trail(
    year: int, reader: FilingLedgerReaderPort, rates: TradeRatesPort
) -> tuple[TrailLine, ...]:
    """Return the trail of every sale filled in ``year``.

    Args:
        year: Tax year.
        reader: Ledger queries, the same ones the Modelo 100 report reads.
        rates: Stored ECB rate per trade.

    Returns:
        One line per stored FIFO consumption, in ledger order.
    """
    start, end = tax_year_bounds(year)
    return build_trail(reader.disposal_pieces(start, end), rates.eur_rates())


def build_pack_output(
    pack: FilingPack,
    trail: Sequence[TrailLine],
    *,
    campaign: RentaCampaign | None,
    estimate: str | None,
    previous_figures: Mapping[str, str] | None,
    today: date,
    refreshed_at: datetime,
) -> PackOutput:
    """Return every file of the pack and what changed.

    Args:
        pack: The year's Modelo 100 and 721 figures.
        trail: The year's trail.
        campaign: The Renta Web layout for the year, or ``None`` when none is known.
        estimate: ``hermes tax`` output, or ``None`` without a local profile.
        previous_figures: ``figures.json`` of the previous refresh, or ``None``.
        today: Madrid date of the run.
        refreshed_at: Instant of the run.

    Returns:
        The pack's files and sheets, checklist and changes.
    """
    readiness = assess_readiness(
        pack.year,
        today,
        pending_sales=len(pack.modelo100.pending),
        missing_prices=pack.modelo721.missing_prices,
        obligation=pack.modelo721.obligation,
        estimate_included=estimate is not None,
        shadow=pack.shadow,
        campaign_confirmed=campaign is not None and not campaign.provisional,
    )
    figures = _figures(pack, readiness)
    changes = figure_changes(previous_figures, figures)
    stamp = _stamp(refreshed_at)
    files = {
        _README_FILE: _readme(pack, readiness, changes, stamp, has_estimate=estimate is not None),
        _REPORT_FILE: render_markdown(pack),
        **render_csv_files(pack),
        _TRAIL_FILE: _trail_csv(trail),
        FIGURES_FILE: json.dumps(figures, indent=2, ensure_ascii=False) + "\n",
    }
    if estimate is not None:
        files[ESTIMATE_FILE] = estimate
    documents = {
        RENTA_SHEET_FILE: build_renta_sheet(
            pack, campaign, status=readiness.status, refreshed_at=refreshed_at
        ),
        DOSSIER_FILE: build_dossier(pack, trail, refreshed_at=refreshed_at),
    }
    entry = None
    if changes:
        entry = "\n".join([f"## {stamp}", "", *(f"- {line}" for line in changes), "", ""])
    return PackOutput(
        files=files,
        documents=documents,
        readiness=readiness,
        changes=changes,
        changes_entry=entry,
    )


def render_manifest(year: int, refreshed_at: datetime, contents: Mapping[str, bytes]) -> str:
    """Return ``manifest.json``: the sha256 of every file, by name.

    Args:
        year: Tax year.
        refreshed_at: Instant of the run.
        contents: File name to bytes, as written.

    Returns:
        The manifest as JSON text.
    """
    manifest = {
        "tax_year": year,
        "refreshed_at": refreshed_at.isoformat(),
        "sha256": {name: hashlib.sha256(contents[name]).hexdigest() for name in sorted(contents)},
    }
    return json.dumps(manifest, indent=2) + "\n"


def render_frozen_marker(year: int, refreshed_at: datetime) -> str:
    """Return the text of the ``FROZEN`` marker.

    Args:
        year: Tax year.
        refreshed_at: Instant of the run that froze the pack.

    Returns:
        The marker text.
    """
    return (
        f"Tax year {year} pack frozen on {_stamp(refreshed_at)}.\n"
        "Refreshes leave this folder untouched. To reopen it after a correction,\n"
        "delete this file and run `hermes tax-pack --year "
        f"{year}`; changes.md records what moved.\n"
    )


def _figures(pack: FilingPack, readiness: Readiness) -> dict[str, str]:
    m100 = pack.modelo100
    figures = {
        "status": readiness.status.value,
        "sales": str(len(m100.transmissions)),
        "sales waiting for EUR values": str(len(m100.pending)),
        "Modelo 100 gains (EUR)": _eur(m100.gains_eur),
        "Modelo 100 losses (EUR)": _eur(m100.losses_eur),
        "Modelo 100 net (EUR)": _eur(m100.net_eur),
        "Modelo 721 obligation": pack.modelo721.obligation.value,
        "Modelo 721 total (EUR)": _eur(pack.modelo721.total_eur),
    }
    for line in m100.summaries:
        figures[f"Modelo 100 {line.asset.value}, {line.kind.value} (EUR)"] = _eur(line.gain_eur)
    return figures


def _readme(
    pack: FilingPack,
    readiness: Readiness,
    changes: Sequence[str],
    stamp: str,
    *,
    has_estimate: bool,
) -> str:
    m100 = pack.modelo100
    out = [
        f"# Tax filing pack, tax year {pack.year}",
        "",
        "> **Estimate, not a filing.** Hermes submits nothing to AEAT. "
        "Check every figure with your gestor before you file.",
    ]
    if pack.shadow:
        out += [">", "> **Shadow (simulated fills, no real tax due).**"]
    out += [
        "",
        f"**Status: {readiness.status.value}.** Refreshed {stamp}.",
        "",
        "## Headline figures",
        "",
        f"- Sales: {len(m100.transmissions)} priced, {len(m100.pending)} waiting for EUR values",
        f"- Modelo 100: gains {_eur(m100.gains_eur)} EUR, losses {_eur(m100.losses_eur)} EUR, "
        f"net {_eur(m100.net_eur)} EUR",
        f"- Modelo 721: {pack.modelo721.obligation.value}, "
        f"holdings {_eur(pack.modelo721.total_eur)} EUR",
        "",
        "## Checklist",
        "",
        *(f"- [{'x' if item.done else ' '}] {item.text}" for item in readiness.items),
        "",
        "## Changes in this refresh",
        "",
        *([f"- {line}" for line in changes] or ["- None."]),
        "",
        "## Files",
        "",
        "| File | What it holds |",
        "|---|---|",
    ]
    skipped = set() if has_estimate else {ESTIMATE_FILE}
    out += [f"| `{name}` | {note} |" for name, note in _FILE_NOTES if name not in skipped]
    out += [
        "",
        "Trail amounts are rounded to cents per line, so a sum of lines can differ "
        "from report.md by a cent per line.",
        "",
    ]
    return "\n".join(out)


def _trail_csv(trail: Sequence[TrailLine]) -> str:
    return _csv(
        ["sale_id", "sale_date", "coin", "received", "lot_buy_id", "lot_date", "units",
         "proceeds_eur", "sell_fee_eur", "acquisition_eur", "gain_eur",
         "sell_rate_usd_per_eur", "sell_rate_date", "buy_rate_usd_per_eur", "buy_rate_date"],
        [
            [t.sell_ref, _day(t.disposed_at), t.asset.value, t.received.value, t.buy_ref,
             _day(t.acquired_at), format(t.qty.normalize(), "f"), _pending(t.proceeds_eur),
             _pending(t.sell_fee_eur), _pending(t.acquisition_eur), _pending(t.gain_eur),
             *_rate(t.sell_rate), *_rate(t.buy_rate)]
            for t in trail
        ],
    )  # fmt: skip


def _rate(rate: TradeRate | None) -> tuple[str, str]:
    if rate is None:
        return ("pending", "")
    return (str(rate.rate), rate.rate_date.isoformat())


def _pending(amount: Decimal | None) -> str:
    return "pending" if amount is None else _eur(amount)


def _stamp(at: datetime) -> str:
    return at.astimezone(MADRID).strftime("%Y-%m-%d %H:%M") + " Madrid"
