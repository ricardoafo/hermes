"""Tests for the tax-filing service (change 36-modelo-100-721 D-8).

Year-end valuation order: operator override, then last bar close in USDT at
the ECB rate of 31 December, else missing.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.application.filing.service import Custodian, FilingPack, YearEndPrice, build_filing_pack
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.filing.modelo100 import DisposalPiece, PurchaseRecord
from hermes.domain.filing.modelo721 import (
    Modelo721Rules,
    Obligation,
    PreviousDeclaration,
    UnitTrade,
)
from hermes.domain.lots.selection import InsufficientInventory
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.unit.application.filing.fake_reader import FakeFilingReader
from tests.unit.application.ledger.fake_fx import FakeFx

_RULES = Modelo721Rules(threshold_eur=Decimal("50000"), increase_eur=Decimal("20000"))
_DEC31 = date(2026, 12, 31)
_AFTER = date(2027, 1, 5)
_MAY = datetime(2026, 5, 1, 12, tzinfo=UTC)
_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)


def _fx(rate: str = "1.25") -> FakeFx:
    return FakeFx(rates={_DEC31: (Decimal(rate), _DEC31)})


def _buy(ref: str, asset: Currency, qty: str, at: datetime = _MAY) -> UnitTrade:
    return UnitTrade(ref=ref, asset=asset, side=OrderSide.BUY, qty=Decimal(qty), executed_at=at)


def _pack(
    reader: FakeFilingReader,
    *,
    fx: FakeFx | None = None,
    overrides: dict[Currency, Decimal] | None = None,
    previous: PreviousDeclaration | None = None,
    today: date = _AFTER,
    year: int = 2026,
) -> FilingPack:
    return build_filing_pack(
        year,
        reader,
        EurPricer(fx if fx is not None else _fx()),
        rules=_RULES,
        custodian=Custodian(name="Exchange", country=None),
        previous=previous,
        overrides=overrides or {},
        today=today,
        shadow=True,
    ).unwrap()


def test_coin_is_valued_at_the_last_close_over_the_ecb_rate() -> None:
    """Invariant: 0.5 BTC with a 80,000 USDT close at 1.25 USD/EUR is worth 32,000 EUR."""
    reader = FakeFilingReader(
        trades=[_buy("1", Currency.BTC, "0.5")], closes={_BTC_USDT: Decimal("80000")}
    )
    pack = _pack(reader)
    assert (pack.prices[0].price_eur, pack.modelo721.total_eur) == (
        Decimal("64000"),
        Decimal("32000"),
    )


def test_close_is_read_at_the_madrid_year_end() -> None:
    """Invariant: the bar lookup uses 1 January 00:00 Madrid, i.e. 23:00 UTC on 31 December."""
    reader = FakeFilingReader(
        trades=[_buy("1", Currency.BTC, "1")], closes={_BTC_USDT: Decimal("1")}
    )
    _pack(reader)
    assert reader.close_queries == [(_BTC_USDT, datetime(2026, 12, 31, 23, tzinfo=UTC))]


def test_override_wins_over_the_ledger_and_needs_no_rate() -> None:
    """Invariant: an operator price is used as is, even with the FX source down."""
    reader = FakeFilingReader(
        trades=[_buy("1", Currency.BTC, "2")], closes={_BTC_USDT: Decimal("80000")}
    )
    pack = _pack(reader, fx=FakeFx(down=True), overrides={Currency.BTC: Decimal("30000")})
    assert pack.prices == (
        YearEndPrice(asset=Currency.BTC, price_eur=Decimal("30000"), source="operator override"),
    )
    assert pack.modelo721.obligation is Obligation.REQUIRED


def test_usdt_holding_is_valued_at_the_rate_alone() -> None:
    """Invariant: one USDT is worth 1 / rate EUR (USDT read as USD)."""
    reader = FakeFilingReader(trades=[_buy("1", Currency.USDT, "1000")])
    pack = _pack(reader)
    assert (pack.prices[0].price_eur, pack.modelo721.total_eur) == (
        Decimal("0.8"),
        Decimal("800.0"),
    )


def test_no_bar_means_a_missing_price_and_an_incomplete_pack() -> None:
    """Invariant: a coin with no stored close has no value and the pack is incomplete."""
    reader = FakeFilingReader(trades=[_buy("1", Currency.ETH, "1")])
    pack = _pack(reader)
    assert (pack.prices[0].price_eur, pack.prices[0].source, pack.complete) == (
        None,
        "no stored bar before year end",
        False,
    )


def test_rate_not_yet_final_means_a_missing_price() -> None:
    """Invariant: on 31 December itself the day's rate is not final, so no value."""
    reader = FakeFilingReader(
        trades=[_buy("1", Currency.BTC, "1")], closes={_BTC_USDT: Decimal("1")}
    )
    fx = FakeFx(rates={_DEC31: (Decimal("1.25"), _DEC31 - timedelta(days=1))})
    price = _pack(reader, fx=fx, today=_DEC31).prices[0]
    assert (price.price_eur, price.source) == (None, "no final ECB rate for 31 December")


def test_modelo100_reads_the_tax_year_and_repurchase_margin() -> None:
    """Invariant: the sale in the year is reported; a buy two months into next year flags it."""
    loss = DisposalPiece(
        sell_ref="9",
        asset=Currency.BTC,
        received=Currency.USDT,
        disposed_at=datetime(2026, 12, 20, 12, tzinfo=UTC),
        buy_ref="1",
        acquired_at=_MAY,
        qty=Decimal("1"),
        proceeds_eur=Decimal("100"),
        cost_eur=Decimal("200"),
        fee_eur=Decimal("1"),
    )
    rebuy = PurchaseRecord(
        ref="10", asset=Currency.BTC, acquired_at=datetime(2027, 2, 1, tzinfo=UTC)
    )
    pack = _pack(FakeFilingReader(pieces=[loss], buys=[rebuy]))
    assert [(f.sell_ref, f.purchase_refs) for f in pack.modelo100.flags] == [("9", ("10",))]


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (
            2026,
            (datetime(2025, 10, 31, 23, 0, tzinfo=UTC), datetime(2027, 2, 28, 23, 0, tzinfo=UTC)),
        ),
        (
            2025,
            (datetime(2024, 10, 31, 23, 0, tzinfo=UTC), datetime(2026, 2, 28, 23, 0, tzinfo=UTC)),
        ),
    ],
)
def test_purchases_are_read_from_madrid_midnight_two_months_either_side_of_the_year(
    year: int, expected: tuple[datetime, datetime]
) -> None:
    """Invariant: the purchase read covers every repurchase window of the year, no more (#1171).

    Worked by hand for 2026: the earliest window opens on 1 Nov 2025 (Madrid,
    CET), 23:00 UTC on 31 Oct; the latest closes at the end of 28 Feb 2027, so
    the read runs to Madrid midnight on 1 Mar 2027, 23:00 UTC on 28 Feb. 2025
    is the same a year earlier; an odd year catches ``year + 1`` arithmetic
    that only works for even years.
    """
    reader = FakeFilingReader()
    _pack(reader, year=year)
    assert reader.purchase_queries == [expected]


def test_a_buy_late_on_the_last_window_day_after_new_years_eve_flags_the_sale() -> None:
    """Invariant: a loss sale at 23:30 Madrid on 31 Dec flags a buy at 23:59 Madrid on 28 Feb.

    The window of a 31 Dec sale ends on 28 Feb (clamped), whatever the hour.
    """
    loss = DisposalPiece(
        sell_ref="9",
        asset=Currency.BTC,
        received=Currency.USDT,
        disposed_at=datetime(2026, 12, 31, 22, 30, tzinfo=UTC),
        buy_ref="1",
        acquired_at=_MAY,
        qty=Decimal("1"),
        proceeds_eur=Decimal("100"),
        cost_eur=Decimal("200"),
        fee_eur=Decimal("1"),
    )
    rebuy = PurchaseRecord(
        ref="10", asset=Currency.BTC, acquired_at=datetime(2027, 2, 28, 22, 59, tzinfo=UTC)
    )
    pack = _pack(FakeFilingReader(pieces=[loss], buys=[rebuy]))
    assert [(f.sell_ref, f.purchase_refs) for f in pack.modelo100.flags] == [("9", ("10",))]


def test_previous_declaration_reaches_the_721_decision() -> None:
    """Invariant: a coin declared last year and sold since makes the 721 due."""
    previous = PreviousDeclaration(year=2025, values_eur={Currency.ETH: Decimal("60000")})
    pack = _pack(FakeFilingReader(), previous=previous)
    assert pack.modelo721.obligation is Obligation.REQUIRED


def test_inconsistent_trades_are_a_failure() -> None:
    """Invariant: a SELL beyond the units held fails rather than reporting negative units."""
    sell = UnitTrade(
        ref="1", asset=Currency.BTC, side=OrderSide.SELL, qty=Decimal("1"), executed_at=_MAY
    )
    result = build_filing_pack(
        2026,
        FakeFilingReader(trades=[sell]),
        EurPricer(_fx()),
        rules=_RULES,
        custodian=Custodian(name="Exchange", country=None),
        previous=None,
        overrides={},
        today=_AFTER,
        shadow=True,
    )
    assert isinstance(result.failure(), InsufficientInventory)
