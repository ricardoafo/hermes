"""Spanish sheets for filing: the Renta Web entries, the 721 fields, the dossier.

Renta Web has no file import for virtual currencies, and the 721 is filed
through the AEAT web form, so what the taxpayer needs is a sheet to copy from
(change 568-tax-pack D-6, D-7):

- ``renta-web.pdf``: one entry per coin and kind of consideration for the
  campaign's virtual-currency box, with Renta Web's own labels and keys, then
  the Modelo 721 fields in the order of Orden HFP/886/2023 when it is due.
- ``dossier.pdf``: every sale and every lot it consumed, with fees and ECB
  rates, to keep in case Hacienda asks.

This module builds the content as a :class:`Document`; the ``tax-pack``
entrypoint writes it as PDF (D-8). Amounts use the Spanish format Renta Web
takes (``1.234,56``).

Reference: openspec/changes/568-tax-pack/change.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from zoneinfo import ZoneInfo

from hermes.application.filing.document import Block, Document, Heading, Paragraph, Table, Tone
from hermes.application.filing.service import FilingPack
from hermes.domain.filing.modelo100 import ConsiderationKind
from hermes.domain.filing.modelo721 import Modelo721Origin, Obligation
from hermes.domain.filing.readiness import PackStatus
from hermes.domain.filing.renta_campaign import RentaCampaign
from hermes.domain.filing.trail import TradeRate, TrailLine
from hermes.domain.value_objects.currency import Currency

__all__ = ["DOSSIER_FILE", "RENTA_SHEET_FILE", "build_dossier", "build_renta_sheet"]

RENTA_SHEET_FILE: Final[str] = "renta-web.pdf"
DOSSIER_FILE: Final[str] = "dossier.pdf"

_MADRID: Final = ZoneInfo("Europe/Madrid")
_CENT: Final[Decimal] = Decimal("0.01")
_SIX: Final[Decimal] = Decimal("0.000001")

# Renta Web's "tipo de contraprestación" keys.
_CONSIDERATION: Final[dict[ConsiderationKind, tuple[str, str]]] = {
    ConsiderationKind.FIAT: ("F", "Moneda de curso legal"),
    ConsiderationKind.VIRTUAL_CURRENCY: ("N", "Otra moneda virtual"),
}

_NAMES: Final[dict[Currency, str]] = {
    Currency.BTC: "Bitcoin",
    Currency.ETH: "Ethereum",
    Currency.SOL: "Solana",
    Currency.USDT: "Tether",
    Currency.USDC: "USD Coin",
}

# Orden HFP/886/2023, list L5.
_ORIGIN: Final[dict[Modelo721Origin, str]] = {
    Modelo721Origin.FIRST: "A: declarada por primera vez",
    Modelo721Origin.MODIFIED: "M: ya declarada; el saldo conjunto subió más de 20.000 €",
    Modelo721Origin.EXTINGUISHED: "C: posición cancelada en el ejercicio",
}


def build_renta_sheet(
    pack: FilingPack,
    campaign: RentaCampaign | None,
    *,
    status: PackStatus,
    refreshed_at: datetime,
) -> Document:
    """Return the Renta Web sheet.

    Args:
        pack: The year's figures.
        campaign: The Renta Web layout, or ``None`` when no layout is known.
        status: The pack's readiness status.
        refreshed_at: Instant of the run.

    Returns:
        The sheet.
    """
    year = pack.year
    blocks: list[Block] = [
        Paragraph(f"Generada por hermes el {_stamp(refreshed_at)}.", Tone.NOTE),
        _warnings(pack, status),
        *_modelo100(pack, campaign),
        *_modelo721(pack),
    ]
    return Document(
        title=f"Renta {year + 1} (ejercicio {year}): hoja para Renta Web", blocks=tuple(blocks)
    )


def build_dossier(
    pack: FilingPack, trail: Sequence[TrailLine], *, refreshed_at: datetime
) -> Document:
    """Return the dossier: every sale and the lots behind it.

    Args:
        pack: The year's figures.
        trail: The year's trail.
        refreshed_at: Instant of the run.

    Returns:
        The dossier.
    """
    m100 = pack.modelo100
    blocks: list[Block] = [
        Paragraph(
            f"Generado por hermes el {_stamp(refreshed_at)}. Método FIFO por moneda, valores en "
            "euros al tipo del BCE del día de cada operación (USDT tomado como USD), comisiones "
            "incluidas (art. 35 LIRPF).",
            Tone.NOTE,
        ),
        Heading("Transmisiones"),
        Table.of(
            ["Venta", "Fecha", "Moneda", "Contraprestación", "Unidades",
             "Valor de transmisión (€)", "Valor de adquisición (€)", "Ganancia o pérdida (€)"],
            [
                [t.sell_ref, _day(t.disposed_at), t.asset.value, t.received.value, _units(t.qty),
                 _eur(t.transmission_eur), _eur(t.acquisition_eur), _eur(t.gain_eur)]
                for t in m100.transmissions
            ],
            numeric=range(4, 8),
        ),
        Heading("Lotes consumidos por cada venta"),
        Table.of(
            ["Venta", "Fecha", "Compra (lote)", "Fecha de compra", "Unidades", "Importe bruto (€)",
             "Comisión de venta (€)", "Valor de adquisición (€)", "Ganancia o pérdida (€)",
             "Tipo BCE venta (USD/EUR)", "Tipo BCE compra (USD/EUR)"],
            [
                [line.sell_ref, _day(line.disposed_at), line.buy_ref, _day(line.acquired_at),
                 _units(line.qty), _optional_eur(line.proceeds_eur),
                 _optional_eur(line.sell_fee_eur), _optional_eur(line.acquisition_eur),
                 _optional_eur(line.gain_eur), _rate(line.sell_rate), _rate(line.buy_rate)]
                for line in trail
            ],
            numeric=range(4, 11),
        ),
    ]  # fmt: skip
    if m100.flags:
        blocks.append(Heading("Ventas con pérdida y recompra en dos meses (para el gestor)"))
        blocks.extend(
            Paragraph(
                f"Venta {f.sell_ref} del {_day(f.disposed_at)}: pérdida de {_eur(f.loss_eur)} € "
                f"en {f.asset.value}; compras {', '.join(f.purchase_refs)}. "
                "No se ha aplicado el art. 33.5.f."
            )
            for f in m100.flags
        )
    return Document(
        title=f"Expediente de operaciones con monedas virtuales, ejercicio {pack.year}",
        blocks=tuple(blocks),
        landscape=True,
    )


def _warnings(pack: FilingPack, status: PackStatus) -> Paragraph:
    lines = [
        "Borrador para revisar con su gestor. hermes no presenta nada a la AEAT; "
        "copie estos datos en Renta Web."
    ]
    if status is PackStatus.OPEN:
        lines.append(
            f"El ejercicio {pack.year} sigue abierto: las cifras cambian con cada operación "
            "hasta el 31 de diciembre."
        )
    elif status is PackStatus.INCOMPLETE:
        lines.append("Faltan datos (vea README.md): no lo use para presentar todavía.")
    if pack.shadow:
        lines.append("Simulación (shadow): operaciones no reales, no hay impuesto que pagar.")
    return Paragraph("\n".join(lines), Tone.WARNING)


def _modelo100(pack: FilingPack, campaign: RentaCampaign | None) -> list[Block]:
    m100 = pack.modelo100
    box = "sin casilla conocida" if campaign is None else campaign.layout.transmissions_box
    out: list[Block] = [
        Heading(f"Ganancias y pérdidas patrimoniales · Monedas virtuales · casilla {box}")
    ]
    if campaign is None:
        out.append(
            Paragraph(
                "No hay casillas configuradas para este ejercicio (config/renta_campaigns.yaml).",
                Tone.NOTE,
            )
        )
    elif campaign.provisional:
        out.append(
            Paragraph(
                f"Casilla de la campaña del ejercicio {campaign.layout.tax_year}; compruébela "
                f"cuando la AEAT publique la del ejercicio {pack.year}.",
                Tone.NOTE,
            )
        )
    out.append(
        Paragraph(
            "En Renta Web: Ganancias y pérdidas patrimoniales > Monedas virtuales, "
            f"casilla {box}. Pulse el lápiz y añada una entrada por fila."
        )
    )
    if not m100.summaries:
        out.append(Paragraph("No hay transmisiones con valores completos en este ejercicio."))
    else:
        out.append(
            Table.of(
                [
                    "Entrada",
                    "Denominación de la moneda virtual",
                    "Tipo de contraprestación",
                    "Valor de transmisión (€)",
                    "Valor de adquisición (€)",
                    "Ganancia o pérdida (€)",
                ],
                [
                    [
                        str(i),
                        _name(s.asset),
                        _consideration(s.kind),
                        _eur(s.transmission_eur),
                        _eur(s.acquisition_eur),
                        _eur(s.gain_eur),
                    ]
                    for i, s in enumerate(m100.summaries, start=1)
                ],
                numeric=range(3, 6),
            )
        )
    out.append(
        Paragraph(
            f"Ganancias: {_eur(m100.gains_eur)} € · Pérdidas: {_eur(m100.losses_eur)} € · "
            f"Saldo neto: {_eur(m100.net_eur)} €"
        )
    )
    if m100.pending:
        out.append(
            Paragraph(
                f"{len(m100.pending)} venta(s) sin valores en euros todavía no están incluidas. "
                "Ejecute hermes fx backfill.",
                Tone.WARNING,
            )
        )
    return out


def _modelo721(pack: FilingPack) -> list[Block]:
    m721 = pack.modelo721
    out: list[Block] = [Heading("Modelo 721: monedas virtuales situadas en el extranjero")]
    if m721.obligation is not Obligation.REQUIRED:
        state = "No obligatorio" if m721.obligation is Obligation.NOT_REQUIRED else "Sin determinar"
        out.append(
            Paragraph(
                f"{state} para el ejercicio {pack.year} (saldo conjunto "
                f"{_eur(m721.total_eur)} €; umbral {_eur(m721.rules.threshold_eur)} €)."
            )
        )
        return out
    custodian = pack.custodian
    out += [
        Paragraph(
            f"Se presenta del 1 de enero al 31 de marzo de {pack.year + 1} en la Sede "
            "electrónica (formulario del modelo 721), con certificado, DNIe o Cl@ve."
        ),
        Table.of(
            ["Campo", "Valor"],
            [
                ["Ejercicio", str(pack.year)],
                ["NIF y nombre del declarante", "rellenar (hermes no los guarda)"],
                ["Condición del declarante", "T: Titular"],
                ["Entidad que custodia (nombre)", custodian.name],
                [
                    "País de residencia fiscal de la entidad",
                    custodian.country or "confirmar con el gestor",
                ],
            ],
        ),
        Table.of(
            [
                "Moneda (denominación)",
                "Siglas",
                "Número de unidades",
                "Valor por unidad (€)",
                "Origen del valor",
                "Saldo a 31 de diciembre (€)",
                "Origen",
            ],
            [
                [
                    _NAMES.get(line.asset, line.asset.value),
                    line.asset.value,
                    _units6(line.units),
                    _optional_six(line.price_eur),
                    _source(pack, line.asset),
                    _optional_eur(line.value_eur),
                    _ORIGIN[line.origin],
                ]
                for line in m721.lines
            ],
            numeric=(2, 3, 5),
        ),
    ]
    if any(line.origin is Modelo721Origin.EXTINGUISHED for line in m721.lines):
        out.append(
            Paragraph(
                "Origen C: indique como fecha de fin de la condición el día de la última venta "
                "de esa moneda (vea dossier.pdf).",
                Tone.NOTE,
            )
        )
    return out


def _source(pack: FilingPack, asset: Currency) -> str:
    for price in pack.prices:
        if price.asset == asset:
            return price.source
    return ""


def _name(asset: Currency) -> str:
    name = _NAMES.get(asset)
    return asset.value if name is None else f"{name} ({asset.value})"


def _consideration(kind: ConsiderationKind) -> str:
    key, label = _CONSIDERATION[kind]
    return f"{key}: {label}"


def _spanish(amount: Decimal, quantum: Decimal) -> str:
    text = f"{amount.quantize(quantum, rounding=ROUND_HALF_UP):,}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _eur(amount: Decimal) -> str:
    return _spanish(amount, _CENT)


def _optional_eur(amount: Decimal | None) -> str:
    return "pendiente" if amount is None else _eur(amount)


def _optional_six(amount: Decimal | None) -> str:
    return "falta" if amount is None else _spanish(amount, _SIX)


def _units(qty: Decimal) -> str:
    return format(qty.normalize(), "f").replace(".", ",")


def _units6(qty: Decimal) -> str:
    return _spanish(qty, _SIX)


def _rate(rate: TradeRate | None) -> str:
    if rate is None:
        return "pendiente"
    return f"{format(rate.rate, 'f').replace('.', ',')} ({rate.rate_date.isoformat()})"


def _day(at: datetime) -> str:
    return at.astimezone(_MADRID).strftime("%d/%m/%Y")


def _stamp(at: datetime) -> str:
    return at.astimezone(_MADRID).strftime("%d/%m/%Y %H:%M") + " (hora de Madrid)"
