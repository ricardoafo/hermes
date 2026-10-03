"""Tests for the EUR FIFO replay (change trade-eur-values R-4, D-5, D-6).

Reference: ADR-016 item 3 (fees in acquisition and transmission value),
IRPF art. 35.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from hypothesis import given, settings
from hypothesis import strategies as st
from returns.result import Failure

from hermes.domain.lots.eur_fifo import (
    ConsumptionEurValue,
    EurTrade,
    FifoEurProjection,
    acquisition_value,
    exact_sum,
    replay_fifo_eur,
    transmission_value,
)
from hermes.domain.lots.selection import InsufficientInventory
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency

_T0 = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)


def _trade(
    ref: int,
    side: OrderSide,
    qty: str,
    price: str | None,
    fee: str | None,
) -> EurTrade:
    return EurTrade(
        ref=str(ref),
        side=side,
        qty=Decimal(qty),
        executed_at=_T0 + timedelta(minutes=ref),
        price_eur=None if price is None else Decimal(price),
        fee_eur=None if fee is None else Decimal(fee),
    )


def _replay(*trades: EurTrade) -> FifoEurProjection:
    return replay_fifo_eur(Currency.BTC, trades).unwrap()


class TestValueFormulas:
    """ADR-016 item 3 formulas."""

    def test_acquisition_value_adds_the_buy_fee(self) -> None:
        """Invariant: acquisition = price x qty + fee."""
        assert acquisition_value(
            price=Decimal("100"), qty=Decimal("2"), fee=Decimal("1.5")
        ) == Decimal("201.5")

    def test_transmission_value_subtracts_the_sell_fee(self) -> None:
        """Invariant: transmission = price x qty - fee."""
        assert transmission_value(
            price=Decimal("110"), qty=Decimal("2"), fee=Decimal("1.5")
        ) == Decimal("218.5")


class TestWorkedExamples:
    """Hand-computed FIFO cases."""

    def test_round_trip_gain_is_net_of_both_fees(self) -> None:
        """Invariant: buy 1 @ 100 fee 1, sell 1 @ 110 fee 1 -> gain 8 (A-4.2)."""
        projection = _replay(
            _trade(1, OrderSide.BUY, "1", "100", "1"),
            _trade(2, OrderSide.SELL, "1", "110", "1"),
        )
        assert projection.consumptions == (
            ConsumptionEurValue(
                sell_ref="2",
                buy_ref="1",
                qty=Decimal("1"),
                consumed_at=_T0 + timedelta(minutes=2),
                proceeds_eur=Decimal("110"),
                cost_eur=Decimal("101"),
                fee_eur=Decimal("1"),
                gain_eur=Decimal("8"),
            ),
        )

    def test_lot_cost_basis_includes_the_buy_fee(self) -> None:
        """Invariant: a lot's EUR basis per unit is (price x qty + fee) / qty."""
        projection = _replay(_trade(1, OrderSide.BUY, "2", "100", "3"))
        assert (projection.lots[0].acquisition_eur, projection.lots[0].cost_basis_per_unit_eur) == (
            Decimal("203"),
            Decimal("101.5"),
        )

    def test_sell_spanning_two_lots_consumes_the_oldest_first(self) -> None:
        """Invariant: FIFO — the first lot is drained before the second is touched."""
        projection = _replay(
            _trade(1, OrderSide.BUY, "1", "100", "0"),
            _trade(2, OrderSide.BUY, "1", "200", "0"),
            _trade(3, OrderSide.SELL, "1.5", "300", "3"),
        )
        assert [(c.buy_ref, c.qty, c.cost_eur, c.fee_eur) for c in projection.consumptions] == [
            ("1", Decimal("1"), Decimal("100"), Decimal("2")),
            ("2", Decimal("0.5"), Decimal("100"), Decimal("1")),
        ]
        assert [lot.remaining_qty for lot in projection.lots] == [Decimal("0"), Decimal("0.5")]

    def test_three_way_split_adds_up_to_the_lot_value_exactly(self) -> None:
        """Invariant: thirds of a lot that do not divide evenly still sum to its value."""
        projection = _replay(
            _trade(1, OrderSide.BUY, "3", "100", "1"),
            _trade(2, OrderSide.SELL, "1", "100", "0"),
            _trade(3, OrderSide.SELL, "1", "100", "0"),
            _trade(4, OrderSide.SELL, "1", "100", "0"),
        )
        costs = [c.cost_eur for c in projection.consumptions]
        assert exact_sum(c for c in costs if c is not None) == Decimal("301")

    def test_sell_beyond_inventory_fails(self) -> None:
        """Invariant: a SELL larger than the open quantity is an InsufficientInventory failure."""
        result = replay_fifo_eur(
            Currency.BTC,
            (
                _trade(1, OrderSide.BUY, "1", "100", "0"),
                _trade(2, OrderSide.SELL, "2", "100", "0"),
            ),
        )
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), InsufficientInventory)


class TestPending:
    """A trade without EUR values leaves only what it feeds pending."""

    def test_pending_buy_leaves_cost_and_gain_unknown(self) -> None:
        """Invariant: the lot of a pending BUY has no EUR basis and its consumption no gain."""
        projection = _replay(
            _trade(1, OrderSide.BUY, "1", None, None),
            _trade(2, OrderSide.SELL, "1", "110", "1"),
        )
        piece = projection.consumptions[0]
        assert (piece.proceeds_eur, piece.fee_eur, piece.cost_eur, piece.gain_eur) == (
            Decimal("110"),
            Decimal("1"),
            None,
            None,
        )
        assert projection.lots[0].cost_basis_per_unit_eur is None
        assert projection.lots[0].remaining_qty == Decimal("0")

    def test_pending_sell_does_not_touch_other_sells(self) -> None:
        """Invariant: one pending SELL leaves an earlier priced SELL's gain intact."""
        projection = _replay(
            _trade(1, OrderSide.BUY, "2", "100", "0"),
            _trade(2, OrderSide.SELL, "1", "120", "0"),
            _trade(3, OrderSide.SELL, "1", None, None),
        )
        assert [c.gain_eur for c in projection.consumptions] == [Decimal("20"), None]


# ---------------------------------------------------------------------------
# Property tests (A-4.1)
# ---------------------------------------------------------------------------

_RATES = (Decimal("1"), Decimal("1.1721"), Decimal("1.0834"), Decimal("0.9973"))


@st.composite
def _priced_value(draw: st.DrawFn, *, max_cents: int) -> Decimal:
    """Draw an EUR value as the ledger stores it: a native amount divided by an ECB rate."""
    cents = Decimal(draw(st.integers(min_value=0, max_value=max_cents))) / Decimal(100)
    return cents / draw(st.sampled_from(_RATES))


@st.composite
def _trade_sequences(draw: st.DrawFn, *, close_out: bool) -> list[EurTrade]:
    """Draw a valid single-asset sequence; ``close_out`` appends a SELL of what is left."""
    trades: list[EurTrade] = []
    open_units = 0
    for ref in range(draw(st.integers(min_value=1, max_value=12))):
        buy = open_units == 0 or draw(st.booleans())
        if buy:
            units = draw(st.integers(min_value=1, max_value=10**9))
            open_units += units
        else:
            units = draw(st.integers(min_value=1, max_value=open_units))
            open_units -= units
        price = draw(_priced_value(max_cents=10**8)) + Decimal("0.01")
        trades.append(
            EurTrade(
                ref=str(ref),
                side=OrderSide.BUY if buy else OrderSide.SELL,
                qty=Decimal(units) / Decimal(10**8),
                executed_at=_T0 + timedelta(minutes=ref),
                price_eur=price,
                fee_eur=draw(_priced_value(max_cents=10**4)),
            )
        )
    if close_out and open_units > 0:
        trades.append(
            EurTrade(
                ref=str(len(trades)),
                side=OrderSide.SELL,
                qty=Decimal(open_units) / Decimal(10**8),
                executed_at=_T0 + timedelta(minutes=len(trades)),
                price_eur=draw(_priced_value(max_cents=10**8)) + Decimal("0.01"),
                fee_eur=draw(_priced_value(max_cents=10**4)),
            )
        )
    return trades


# Sums in these tests run at the replay's precision so the check itself never rounds.
_PREC = 100


def _total(values: list[Decimal | None]) -> Decimal:
    known = [v for v in values if v is not None]
    assert len(known) == len(values), "every value is priced in these sequences"
    with localcontext() as ctx:
        ctx.prec = _PREC
        return sum(known, start=Decimal("0"))


@settings(max_examples=300)
@given(trades=_trade_sequences(close_out=False))
def test_gains_equal_proceeds_minus_costs_minus_fees(trades: list[EurTrade]) -> None:
    """Invariant (A-4.1): sum(gain) == sum(proceeds) - sum(cost) - sum(fee), exactly."""
    projection = replay_fifo_eur(Currency.BTC, trades).unwrap()
    pieces = projection.consumptions
    with localcontext() as ctx:
        ctx.prec = _PREC
        gains = _total([c.gain_eur for c in pieces])
        expected = (
            _total([c.proceeds_eur for c in pieces])
            - _total([c.cost_eur for c in pieces])
            - _total([c.fee_eur for c in pieces])
        )
        assert gains - expected == Decimal("0")


@settings(max_examples=300)
@given(trades=_trade_sequences(close_out=True))
def test_closed_out_gains_equal_eur_proceeds_minus_eur_costs_minus_fees(
    trades: list[EurTrade],
) -> None:
    """Invariant (A-4.1): all lots closed -> sum(gain) == sells net of fees - buys plus fees.

    Sells contribute ``price x qty - fee`` and buys ``price x qty + fee``, all in EUR.
    """
    projection = replay_fifo_eur(Currency.BTC, trades).unwrap()
    with localcontext() as ctx:
        ctx.prec = _PREC
        sells = sum(
            (
                transmission_value(price=t.price_eur, qty=t.qty, fee=t.fee_eur)
                for t in trades
                if t.side == OrderSide.SELL and t.price_eur is not None and t.fee_eur is not None
            ),
            start=Decimal("0"),
        )
        buys = sum(
            (
                acquisition_value(price=t.price_eur, qty=t.qty, fee=t.fee_eur)
                for t in trades
                if t.side == OrderSide.BUY and t.price_eur is not None and t.fee_eur is not None
            ),
            start=Decimal("0"),
        )
        gains = _total([c.gain_eur for c in projection.consumptions])
        assert gains - (sells - buys) == Decimal("0")
        assert [lot.remaining_qty for lot in projection.lots] == [Decimal("0")] * len(
            projection.lots
        )


@settings(max_examples=200)
@given(trades=_trade_sequences(close_out=True))
def test_each_closed_lot_charges_exactly_its_acquisition_value(trades: list[EurTrade]) -> None:
    """Invariant (D-5): the cost shares of a closed lot add up to its acquisition value, exactly."""
    projection = replay_fifo_eur(Currency.BTC, trades).unwrap()
    for lot in projection.lots:
        charged = _total([c.cost_eur for c in projection.consumptions if c.buy_ref == lot.buy_ref])
        assert lot.acquisition_eur is not None
        with localcontext() as ctx:
            ctx.prec = _PREC
            assert charged - lot.acquisition_eur == Decimal("0")
