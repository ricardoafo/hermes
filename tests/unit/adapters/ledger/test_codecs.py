"""Ledger codecs: what the writer stores, the readers read back unchanged.

The codecs are shared by :class:`SqliteLedger` and the read-only readers
(issue #1185), so a codec that loses information corrupts every reader at
once. The ledger round-trip tests in ``test_sqlite_ledger.py`` cover the
codecs through the adapter; these pin the pure functions on their own.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.ledger.codecs import decimal_text, event_to_json, iso_utc, trade_from_payload
from hermes.domain.errors import LedgerError
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId


@given(st.datetimes(timezones=st.just(UTC)))
def test_iso_utc_parses_back_to_the_same_instant(moment: datetime) -> None:
    """Any UTC instant survives ``iso_utc`` and ``fromisoformat`` unchanged."""
    assert datetime.fromisoformat(iso_utc(moment)) == moment


def test_iso_utc_refuses_a_naive_datetime() -> None:
    """A naive datetime is rejected instead of being stored in an unknown zone."""
    with pytest.raises(LedgerError):
        iso_utc(datetime(2026, 11, 5, 14, 22, 3))


@given(st.decimals(allow_nan=False, allow_infinity=False))
def test_decimal_text_parses_back_to_the_same_value(value: Decimal) -> None:
    """Any finite Decimal survives ``decimal_text`` and ``Decimal`` unchanged."""
    assert Decimal(decimal_text(value)) == value


@given(st.decimals(allow_nan=False, allow_infinity=False))
def test_decimal_text_never_writes_an_exponent(value: Decimal) -> None:
    """Stored decimals are positional text, so SQL text comparisons stay meaningful."""
    assert "E" not in decimal_text(value).upper()


def test_decimal_text_writes_a_large_exponent_value_in_full() -> None:
    """``1E+5`` is stored as ``100000``, the hand-computed positional form."""
    assert decimal_text(Decimal("1E+5")) == "100000"


def test_trade_event_survives_the_stored_payload() -> None:
    """A TradeExecuted event read back from its stored JSON equals the original."""
    pair = Pair(base=Currency.BTC, quote=Currency.USDT)
    event = TradeExecuted(
        occurred_at=datetime(2026, 11, 5, 14, 22, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        pair=pair,
        side=TradeSide.SELL,
        qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        price=Price(amount=Decimal("65000.12"), pair=pair),
        fee=Money(amount=Decimal("0.06"), currency=Currency.USDT),
        strategy_id=StrategyId("swing"),
    )

    assert trade_from_payload(json.loads(event_to_json(event))) == event
