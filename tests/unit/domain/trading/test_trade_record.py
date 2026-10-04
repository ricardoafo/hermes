"""Tests for TradeRecord and TradeEur (change trade-eur-values R-2).

Reference: ADR-002 §3, ADR-016 item 3.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hermes.domain.fx.eur_pricing import EurPricing, EurStatus
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.trading.trade_record import TradeEur, TradeRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_AT = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)
_PRICING = EurPricing(
    quote=Currency.USDT, rate=Decimal("1.25"), rate_date=date(2026, 1, 2), source="ecb:frankfurter"
)


def _fill(side: OrderSide) -> Fill:
    order = Order(
        side=side,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
        strategy_id=StrategyId("swing"),
    )
    return Fill(
        order=order,
        executed_qty=Decimal("0.5"),
        avg_price=Decimal("50000"),
        fee=Money(amount=Decimal("25"), currency=Currency.USDT),
        timestamp=_AT,
    )


class TestTradeEurPrice:
    """Native price and fee convert at the same rate."""

    def test_price_and_fee_in_quote_both_convert(self) -> None:
        """Invariant: 50000 USDT and a 25 USDT fee at 1.25 are 40000 EUR and 20 EUR."""
        eur = TradeEur.price(
            _PRICING,
            price=Money(amount=Decimal("50000"), currency=Currency.USDT),
            fee=Money(amount=Decimal("25"), currency=Currency.USDT),
        )
        assert eur == TradeEur(pricing=_PRICING, price_eur=Decimal("40000"), fee_eur=Decimal("20"))

    def test_fee_already_in_eur_is_kept(self) -> None:
        """Invariant: an EUR fee is not converted a second time."""
        eur = TradeEur.price(
            _PRICING,
            price=Money(amount=Decimal("50000"), currency=Currency.USDT),
            fee=Money(amount=Decimal("3"), currency=Currency.EUR),
        )
        assert eur == TradeEur(pricing=_PRICING, price_eur=Decimal("40000"), fee_eur=Decimal("3"))

    def test_fee_in_a_third_currency_cannot_be_priced(self) -> None:
        """Invariant: a fee the rate does not cover leaves the trade pending."""
        eur = TradeEur.price(
            _PRICING,
            price=Money(amount=Decimal("50000"), currency=Currency.USDT),
            fee=Money(amount=Decimal("0.001"), currency=Currency.BTC),
        )
        record = TradeRecord.from_fill(_fill(OrderSide.SELL), correlation_id="c-0", eur=eur)
        assert record.eur_status == EurStatus.PENDING

    def test_fee_in_the_base_asset_is_valued_at_the_fill_price(self) -> None:
        """Invariant (405 D-2): a 0.001 BTC fee at 50000 USDT and 1.25 is 40 EUR."""
        eur = TradeEur.price(
            _PRICING,
            price=Money(amount=Decimal("50000"), currency=Currency.USDT),
            fee=Money(amount=Decimal("0.001"), currency=Currency.BTC),
            base=Currency.BTC,
        )
        assert eur == TradeEur(pricing=_PRICING, price_eur=Decimal("40000"), fee_eur=Decimal("40"))


class TestTradeRecord:
    """The record mirrors the fill and states its EUR status."""

    def test_buy_fill_record_mints_a_lot_and_keeps_native_values(self) -> None:
        """Invariant: native qty/price/fee are copied verbatim; a BUY carries a lot id."""
        record = TradeRecord.from_fill(_fill(OrderSide.BUY), correlation_id="c-1", eur=None)
        assert (record.qty, record.price, record.fee, record.side, record.lot_id is not None) == (
            Decimal("0.5"),
            Decimal("50000"),
            Money(amount=Decimal("25"), currency=Currency.USDT),
            OrderSide.BUY,
            True,
        )

    def test_sell_fill_record_has_no_lot(self) -> None:
        """Invariant: a SELL opens no lot."""
        record = TradeRecord.from_fill(_fill(OrderSide.SELL), correlation_id="c-2", eur=None)
        assert record.lot_id is None
        assert record.strategy_id == StrategyId("swing")

    def test_status_is_pending_without_eur_values(self) -> None:
        """Invariant: a record with no EUR values is stored pending."""
        record = TradeRecord.from_fill(_fill(OrderSide.SELL), correlation_id="c-3", eur=None)
        assert record.eur_status == EurStatus.PENDING

    def test_status_is_ok_with_eur_values(self) -> None:
        """Invariant: a record with EUR values is stored ok."""
        eur = TradeEur(pricing=_PRICING, price_eur=Decimal("40000"), fee_eur=Decimal("20"))
        record = TradeRecord.from_fill(_fill(OrderSide.SELL), correlation_id="c-4", eur=eur)
        assert record.eur_status == EurStatus.OK

    def test_eur_values_for_another_quote_are_rejected(self) -> None:
        """Invariant: a trade is never priced with a rate for a different currency."""
        usdc = EurPricing(
            quote=Currency.USDC, rate=Decimal("1.2"), rate_date=date(2026, 1, 2), source="x"
        )
        eur = TradeEur(pricing=usdc, price_eur=Decimal("1"), fee_eur=Decimal("0"))
        with pytest.raises(ValueError, match="pair quote is USDT"):
            TradeRecord.from_fill(_fill(OrderSide.SELL), correlation_id="c-5", eur=eur)

    def test_sell_with_a_lot_id_is_rejected(self) -> None:
        """Invariant: only a BUY opens a lot."""
        with pytest.raises(ValueError, match="lot_id is required for a BUY"):
            TradeRecord(
                correlation_id="c-6",
                pair=_BTC_USDT,
                side=OrderSide.SELL,
                qty=Decimal("1"),
                price=Decimal("1"),
                fee=Money(amount=Decimal("0"), currency=Currency.USDT),
                executed_at=_AT,
                strategy_id=StrategyId("swing"),
                lot_id=LotId.new(),
                eur=None,
            )
