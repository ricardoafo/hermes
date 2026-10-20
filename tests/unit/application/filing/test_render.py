"""Tests for the tax-filing Markdown and CSV renderer (change 36-modelo-100-721).

Reference: ADR-016 item 10 (estimate and shadow labels), change D-4 and D-10.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from hermes.application.filing.render import CSV_FILES, render_csv_files, render_markdown
from hermes.application.filing.service import Custodian, FilingPack, build_filing_pack
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.filing.modelo100 import DisposalPiece
from hermes.domain.filing.modelo721 import Modelo721Rules, UnitTrade
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.unit.application.filing.fake_reader import FakeFilingReader
from tests.unit.application.ledger.fake_fx import FakeFx

_DEC31 = date(2026, 12, 31)
_SALE_AT = datetime(2026, 6, 1, 10, tzinfo=UTC)
_BUY_AT = datetime(2026, 3, 2, 10, tzinfo=UTC)


def _sale(pending: bool = False) -> DisposalPiece:
    return DisposalPiece(
        sell_ref="3",
        asset=Currency.BTC,
        received=Currency.USDT,
        disposed_at=_SALE_AT,
        buy_ref="1",
        acquired_at=_BUY_AT,
        qty=Decimal("1"),
        proceeds_eur=Decimal("56000.004"),
        cost_eur=Decimal("40040"),
        fee_eur=None if pending else Decimal("56"),
    )


def _pack(*, pending: bool = False, shadow: bool = True, close: str | None = "80000") -> FilingPack:
    pair = Pair(base=Currency.BTC, quote=Currency.USDT)
    reader = FakeFilingReader(
        pieces=[_sale(pending)],
        trades=[
            UnitTrade(
                ref="2",
                asset=Currency.BTC,
                side=OrderSide.BUY,
                qty=Decimal("0.5"),
                executed_at=_BUY_AT,
            )
        ],
        closes={} if close is None else {pair: Decimal(close)},
    )
    return build_filing_pack(
        2026,
        reader,
        EurPricer(FakeFx(rates={_DEC31: (Decimal("1.25"), _DEC31)})),
        rules=Modelo721Rules(threshold_eur=Decimal("50000"), increase_eur=Decimal("20000")),
        custodian=Custodian(name="Exchange", country=None),
        previous=None,
        overrides={},
        today=date(2027, 1, 5),
        shadow=shadow,
    ).unwrap()


def test_report_always_says_it_is_an_estimate_and_submits_nothing() -> None:
    """Invariant (ADR-016 item 10): the first lines label the figures an estimate."""
    text = render_markdown(_pack(shadow=False))
    assert text.splitlines()[2].startswith("> **Estimate, not a filing.** Hermes submits nothing")


def test_shadow_report_says_no_real_tax_is_due() -> None:
    """Invariant: shadow figures carry the shadow label; live ones do not."""
    assert (
        "no real tax due" in render_markdown(_pack()),
        "no real tax due" in render_markdown(_pack(shadow=False)),
    ) == (
        True,
        False,
    )


def test_modelo100_line_is_rounded_to_cents_half_up() -> None:
    """Invariant: 55,944.004 EUR transmission shows as 55944.00; the gain as 15904.00."""
    text = render_markdown(_pack())
    assert "| BTC | other virtual currency | 1 | 1 | 55944.00 | 40040.00 | 15904.00 |" in text


def test_report_prints_no_aeat_box_numbers() -> None:
    """Invariant (D-4): field names only; no 'casilla' references that drift per campaign."""
    assert "casilla" not in render_markdown(_pack()).lower()


def test_complete_pack_says_complete() -> None:
    """Invariant: no pending sale and every price known reads 'complete'."""
    assert "**Status: complete.**" in render_markdown(_pack())


def test_pending_sale_and_missing_price_are_both_named() -> None:
    """Invariant: the incomplete status lists every reason."""
    text = render_markdown(_pack(pending=True, close=None))
    assert (
        "**Status: incomplete:** 1 sale(s) with pending EUR values; no year-end price for BTC."
        in text
    )


def test_custodian_country_unset_defers_to_the_gestor() -> None:
    """Invariant (D-7): an unconfirmed country is printed as such, never guessed."""
    assert "- Custodian: Exchange, country to confirm with the gestor." in render_markdown(_pack())


def test_modelo721_obligation_and_line_are_shown() -> None:
    """Invariant: 0.5 BTC at 64,000 EUR is 32,000 EUR, below the first-time threshold."""
    text = render_markdown(_pack())
    assert "**Obligation: not required.** No earlier 721 on file" in text
    assert "| BTC | A | 0.5 | 64000.00 | 32000.00 | 2026-03-02 |" in text


def test_csv_files_hold_the_same_rounded_figures() -> None:
    """Invariant: the CSVs carry what the report shows, one row per line."""
    files = render_csv_files(_pack())
    assert tuple(files) == CSV_FILES
    assert files["modelo100-summary.csv"].splitlines() == [
        "coin,consideration,sales,units,transmission_eur,acquisition_eur,gain_eur",
        "BTC,other virtual currency,1,1,55944.00,40040.00,15904.00",
    ]
    assert (
        files["modelo721.csv"].splitlines()[1].startswith("BTC,A,0.5,64000.00,32000.00,2026-03-02,")
    )


def test_sales_csv_dates_are_madrid_dates() -> None:
    """Invariant: dates are tax-calendar (Madrid) dates."""
    rows = render_csv_files(_pack())["modelo100-sales.csv"].splitlines()
    assert rows[1] == "3,2026-06-01,BTC,USDT,1,55944.00,40040.00,15904.00,2026-03-02"
