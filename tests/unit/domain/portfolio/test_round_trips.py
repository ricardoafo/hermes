"""Unit and property tests for the one round-trip loop (issue #1179).

Invariants (openspec/changes/1179-one-pearson-round-trips/change.md):

- a round trip runs from a pair's first BUY while flat to the SELL that
  flattens it; gross P&L is proceeds minus the average cost of what was sold,
  fees are every fee on the way, net is gross minus fees;
- a position still open at the end is not a round trip, and pairs never mix;
- a SELL beyond the holding, or a fee not in the pair's quote, is refused;
- the arithmetic is exact at the precision fills carry: a full round trip's
  gross is exactly ``qty * (sell - buy)`` (property).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.portfolio.round_trips import (
    ClosedRoundTrip,
    RoundTripError,
    RoundTripFill,
    round_trips,
)
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.precision import EXACT_CONTEXT

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_T0 = datetime(2026, 10, 3, tzinfo=UTC)
_H4 = timedelta(hours=4)

_eighteen_places = st.decimals(
    min_value=Decimal("0.000000000000000001"),
    max_value=Decimal("1000000"),
    places=18,
    allow_nan=False,
    allow_infinity=False,
)


def _fill(
    step: int,
    side: OrderSide,
    qty: str | Decimal,
    price: str | Decimal,
    fee: str = "0",
    *,
    pair: Pair = _BTC,
    fee_currency: Currency | None = None,
) -> RoundTripFill:
    return RoundTripFill(
        pair=pair,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Money(Decimal(fee), fee_currency or pair.quote),
        at=_T0 + step * _H4,
    )


def test_one_round_trip_has_gross_fees_and_net() -> None:
    """Buy 2 at 100 (fee 0.2), sell 2 at 110 (fee 0.22): gross 20, fees 0.42, net 19.58."""
    trips = round_trips(
        [
            _fill(0, OrderSide.BUY, "2", "100", "0.2"),
            _fill(1, OrderSide.SELL, "2", "110", "0.22"),
        ]
    )

    assert trips == [ClosedRoundTrip(_BTC, _T0, Decimal(20), Decimal("0.42"))]
    assert trips[0].net_pnl == Decimal("19.58")


def test_a_round_trip_spans_partial_fills_from_the_first_buy() -> None:
    """Buys at 100 and 120, sells at 130 and 90: one trip, gross 0, entered at the first buy."""
    trips = round_trips(
        [
            _fill(0, OrderSide.BUY, "1", "100"),
            _fill(1, OrderSide.BUY, "1", "120"),
            _fill(2, OrderSide.SELL, "1", "130"),
            _fill(3, OrderSide.SELL, "1", "90"),
        ]
    )

    assert trips == [ClosedRoundTrip(_BTC, _T0, Decimal(0), Decimal(0))]


def test_an_open_position_is_not_a_round_trip_and_pairs_do_not_mix() -> None:
    """The ETH buy stays open, so only the BTC trip (gross 5) is returned."""
    trips = round_trips(
        [
            _fill(0, OrderSide.BUY, "1", "100"),
            _fill(1, OrderSide.BUY, "1", "50", pair=_ETH),
            _fill(2, OrderSide.SELL, "1", "105"),
        ]
    )

    assert trips == [ClosedRoundTrip(_BTC, _T0, Decimal(5), Decimal(0))]


def test_a_sell_beyond_the_holding_is_refused() -> None:
    """Selling 2 while holding 1 is not a round trip; it raises naming both amounts."""
    fills = [_fill(0, OrderSide.BUY, "1", "100"), _fill(1, OrderSide.SELL, "2", "100")]

    with pytest.raises(RoundTripError, match=r"sell of 2 at .* exceeds the 1 held"):
        round_trips(fills)


@pytest.mark.parametrize("side", list(OrderSide))
def test_a_fee_outside_the_quote_is_refused(side: OrderSide) -> None:
    """A BTC fee on a BTC/USDT fill would add BTC to USDT; it raises instead (#1179)."""
    fills = [_fill(0, side, "1", "100", "0.001", fee_currency=Currency.BTC)]

    with pytest.raises(RoundTripError, match=r"fee at .* is in BTC, not the quote USDT"):
        round_trips(fills)


@pytest.mark.parametrize("side", list(OrderSide))
def test_a_fee_currency_that_sorts_after_the_quote_is_refused_too(side: OrderSide) -> None:
    """A USDT fee on a BTC/EUR fill is refused: the check is identity, not an order (#1219)."""
    btc_eur = Pair(base=Currency.BTC, quote=Currency.EUR)
    fills = [_fill(0, side, "1", "100", "0.1", pair=btc_eur, fee_currency=Currency.USDT)]

    with pytest.raises(RoundTripError, match=r"fee at .* is in USDT, not the quote EUR"):
        round_trips(fills)


@given(qty=_eighteen_places, buy=_eighteen_places, sell=_eighteen_places)
def test_a_full_round_trip_is_exact_at_fill_precision(
    qty: Decimal, buy: Decimal, sell: Decimal
) -> None:
    """Gross is exactly qty * (sell - buy), with no 28-digit rounding of the products."""
    trips = round_trips([_fill(0, OrderSide.BUY, qty, buy), _fill(1, OrderSide.SELL, qty, sell)])

    with localcontext(EXACT_CONTEXT):
        expected = qty * (sell - buy)
    assert [t.gross_pnl for t in trips] == [expected]


def test_the_products_need_more_than_the_default_precision() -> None:
    """Two 18-digit values multiply to 36 digits; the default context would drop the last 7."""
    trips = round_trips(
        [
            _fill(0, OrderSide.BUY, "0.123456789012345678", "12345.6789012345678"),
            _fill(1, OrderSide.SELL, "0.123456789012345678", "98765.4321098765432"),
        ]
    )

    assert [t.gross_pnl for t in trips] == [Decimal("10669.1052383782957794543512455723212")]
