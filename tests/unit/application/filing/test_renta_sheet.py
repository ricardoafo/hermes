"""Tests for the printable Renta Web sheet and dossier (change 568-tax-pack D-6, D-7).

Uses the scripted reader; the same seam against a real ledger file is covered
in ``tests/unit/entrypoints/test_tax_pack.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from hermes.application.filing.renta_sheet import render_dossier, render_renta_sheet
from hermes.application.filing.service import Custodian, FilingPack, build_filing_pack
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.filing.modelo100 import DisposalPiece
from hermes.domain.filing.modelo721 import Modelo721Rules, UnitTrade
from hermes.domain.filing.readiness import PackStatus
from hermes.domain.filing.renta_campaign import CampaignLayout, RentaCampaign
from hermes.domain.filing.trail import TradeRate, build_trail
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.unit.application.filing.fake_reader import FakeFilingReader
from tests.unit.application.ledger.fake_fx import FakeFx

_DEC31 = date(2026, 12, 31)
_NOW = datetime(2027, 1, 5, 6, 30, tzinfo=UTC)
_LAYOUT = CampaignLayout(
    tax_year=2025, transmissions_box="1804", source="s", verified_on=date(2026, 10, 4)
)


def _piece(received: Currency = Currency.USDT, proceeds: str = "1234.5") -> DisposalPiece:
    return DisposalPiece(
        sell_ref="3",
        asset=Currency.BTC,
        received=received,
        disposed_at=datetime(2026, 6, 1, 10, tzinfo=UTC),
        buy_ref="1",
        acquired_at=datetime(2026, 3, 2, 10, tzinfo=UTC),
        qty=Decimal("0.5"),
        proceeds_eur=Decimal(proceeds),
        cost_eur=Decimal("1000"),
        fee_eur=Decimal("4.5"),
    )


def _pack(reader: FakeFilingReader, threshold: str = "50000") -> FilingPack:
    return build_filing_pack(
        2026,
        reader,
        EurPricer(FakeFx(rates={_DEC31: (Decimal("1.25"), _DEC31)})),
        rules=Modelo721Rules(threshold_eur=Decimal(threshold), increase_eur=Decimal("20000")),
        custodian=Custodian(name="Binance", country=None),
        previous=None,
        overrides={},
        today=date(2027, 1, 5),
        shadow=True,
    ).unwrap()


def _sheet(
    pack: FilingPack,
    *,
    campaign: RentaCampaign | None = None,
    status: PackStatus = PackStatus.READY,
) -> str:
    chosen = campaign or RentaCampaign(tax_year=2026, layout=_LAYOUT, provisional=False)
    return render_renta_sheet(pack, chosen, status=status, refreshed_at=_NOW)


def test_entry_uses_renta_web_keys_and_spanish_amounts() -> None:
    """Invariant: one entry per coin and consideration, key N for a sale into USDT, 1.230,00."""
    sheet = _sheet(_pack(FakeFilingReader(pieces=[_piece()])))
    assert (
        "<td>Bitcoin (BTC)</td><td>N: Otra moneda virtual</td>"
        '<td class="n">1.230,00</td><td class="n">1.000,00</td><td class="n">230,00</td>'
    ) in sheet


def test_a_sale_for_euros_is_key_f() -> None:
    """Invariant: a sale for legal tender is entered with key F."""
    sheet = _sheet(_pack(FakeFilingReader(pieces=[_piece(received=Currency.EUR)])))
    assert "<td>F: Moneda de curso legal</td>" in sheet


def test_provisional_box_says_to_check_the_new_campaign() -> None:
    """Invariant: a borrowed layout is flagged on the sheet itself."""
    campaign = RentaCampaign(tax_year=2026, layout=_LAYOUT, provisional=True)
    sheet = _sheet(_pack(FakeFilingReader(pieces=[_piece()])), campaign=campaign)
    assert "compruébela cuando la AEAT publique la del ejercicio 2026" in sheet


def test_unknown_box_never_prints_a_number() -> None:
    """Invariant: with no layout the sheet says so instead of guessing a box."""
    pack = _pack(FakeFilingReader(pieces=[_piece()]))
    sheet = render_renta_sheet(pack, None, status=PackStatus.READY, refreshed_at=_NOW)
    assert "casilla sin casilla conocida" in sheet
    assert "1804" not in sheet


def test_open_year_is_marked_as_still_changing() -> None:
    """Invariant: a sheet built before 31 December warns that figures still change."""
    sheet = _sheet(_pack(FakeFilingReader(pieces=[_piece()])), status=PackStatus.OPEN)
    assert "sigue abierto" in sheet


def test_shadow_sheet_says_no_tax_is_due() -> None:
    """Invariant: shadow figures carry the no-real-tax warning."""
    assert "no hay impuesto que pagar" in _sheet(_pack(FakeFilingReader(pieces=[_piece()])))


def test_721_fields_appear_only_when_due() -> None:
    """Invariant: below the threshold only the 'not required' line shows; above it, the fields."""
    holding = UnitTrade(
        ref="1",
        asset=Currency.BTC,
        side=OrderSide.BUY,
        qty=Decimal("2"),
        executed_at=datetime(2026, 3, 2, 10, tzinfo=UTC),
    )
    closes = {Pair(base=Currency.BTC, quote=Currency.USDT): Decimal("40000")}
    below = _sheet(_pack(FakeFilingReader(trades=[holding], closes=closes), threshold="100000"))
    above = _sheet(_pack(FakeFilingReader(trades=[holding], closes=closes), threshold="50000"))
    assert "No obligatorio para el ejercicio 2026" in below
    assert "Condición del declarante" not in below
    assert (
        '<td>Bitcoin</td><td>BTC</td><td class="n">2,000000</td><td class="n">32.000,000000</td>'
    ) in above
    assert "<td>A: declarada por primera vez</td>" in above


def test_dossier_lists_each_lot_with_its_rates() -> None:
    """Invariant: the dossier shows every lot consumed with both ECB rates."""
    piece = _piece()
    rates = {
        "3": TradeRate(rate=Decimal("1.25"), rate_date=date(2026, 6, 1)),
        "1": TradeRate(rate=Decimal("1.2"), rate_date=date(2026, 3, 2)),
    }
    dossier = render_dossier(
        _pack(FakeFilingReader(pieces=[piece])),
        build_trail([piece], rates),
        refreshed_at=_NOW,
    )
    assert "<td>1,25 (2026-06-01)</td>" not in dossier
    assert '<td class="n">1,25 (2026-06-01)</td><td class="n">1,2 (2026-03-02)</td>' in dossier


def test_text_from_config_is_escaped() -> None:
    """Invariant: config text cannot inject markup into the printed page."""
    pack = build_filing_pack(
        2026,
        FakeFilingReader(pieces=[_piece()]),
        EurPricer(FakeFx(rates={_DEC31: (Decimal("1.25"), _DEC31)})),
        rules=Modelo721Rules(threshold_eur=Decimal("0"), increase_eur=Decimal("0")),
        custodian=Custodian(name="<b>X</b>", country=None),
        previous=None,
        overrides={},
        today=date(2027, 1, 5),
        shadow=True,
    ).unwrap()
    assert "<b>X</b>" not in _sheet(pack)
