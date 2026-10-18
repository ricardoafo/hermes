"""Unit tests for SimulatedExchangeAdapter.

Testing pyramid — unit layer:

- Port conformance: ``isinstance(adapter, ExchangePort)`` → ``True``.
- ``place_market_order`` delegates to FillModelPort and returns ``Fill``.
- ``place_market_order`` raises ``ValueError`` when ``limit_price`` is set.
- ``place_market_order`` raises ``RuntimeError`` when fill model rejects.
- ``place_limit_order`` returns ``Fill`` on success.
- ``place_limit_order`` returns ``Rejection`` when limit unreachable.
- ``place_limit_order`` raises ``ValueError`` when ``limit_price`` is None.
- ``cancel_order`` removes from open-order queue (idempotent — no-op for missing).
- ``get_orderbook`` delegates to injected callable.
- ``get_balance`` reads ledger; returns zero for unknown currency.
- ``get_fees`` delegates to injected callable.
- Balance deduction on BUY: quote debited, base credited.
- Balance deduction on SELL: base debited, quote credited.
- Lot opened on BUY fill; NOT on SELL fill.
- Lot has correct asset, qty, strategy_id, opened_at.
- ``lot_ledger`` grows with each BUY fill.
- Multiple BUY fills produce multiple lots.
- Deterministic replay under seeded RNG (same inputs → same Fill).
- ``place_market_order`` side parameter is used correctly (BUY vs SELL).
- Zero-balance initial state — balance returns Quantity with amount=0.
- Large order fill updates balance correctly.
- Fee subtracted from BUY balance (cost = qty x price + fee).
- Fee subtracted from SELL balance (proceeds = qty x price - fee).
- Consecutive BUY + SELL balance round-trip (approximate equality).
- ``get_orderbook`` passes pair to callable.
- ``get_fees`` passes pair to callable.
- Multi-pair adapter — get_balance for untraded pair returns zero.

Reference: spec-2c-simulated-exchange-adapter/change.md.
"""

from __future__ import annotations

import inspect
import random
from datetime import UTC, datetime
from decimal import Decimal
from typing import get_type_hints

import pytest

from hermes.adapters.clock.simulated import SimulatedClock
from hermes.adapters.exchanges.simulated import SimulatedExchangeAdapter
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.exchange import ExchangePort

_STRATEGY_ID = StrategyId("test-strategy")

_PORT_METHODS = (
    "place_market_order",
    "place_limit_order",
    "cancel_order",
    "get_orderbook",
    "get_balance",
    "get_fees",
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

_STANDARD_FEES = FeeSchedule.from_tiers(
    pair=_BTC_USDT,
    tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26),),
)

_ETH_FEES = FeeSchedule.from_tiers(
    pair=_ETH_USDT,
    tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26),),
)

# A simple single-level book: ask 60_001, bid 60_000.
_SIMPLE_BOOK = Orderbook(
    pair=_BTC_USDT,
    bids=(OrderbookLevel(price=Decimal("60000"), quantity=Decimal("10")),),
    asks=(OrderbookLevel(price=Decimal("60001"), quantity=Decimal("10")),),
    snapshot_time=_T0,
)

_ETH_BOOK = Orderbook(
    pair=_ETH_USDT,
    bids=(OrderbookLevel(price=Decimal("3000"), quantity=Decimal("20")),),
    asks=(OrderbookLevel(price=Decimal("3001"), quantity=Decimal("20")),),
    snapshot_time=_T0,
)


def _make_clock(start: datetime = _T0) -> SimulatedClock:
    return SimulatedClock(start=start)


def _make_adapter(
    *,
    pair: Pair = _BTC_USDT,
    book: Orderbook | None = None,
    fees: FeeSchedule | None = None,
    initial_balances: dict[Currency, Decimal] | None = None,
    clock: SimulatedClock | None = None,
    rng: random.Random | None = None,
) -> SimulatedExchangeAdapter:
    """Build a minimal valid SimulatedExchangeAdapter for unit tests."""
    resolved_book = book or _SIMPLE_BOOK
    resolved_fees = fees or _STANDARD_FEES
    balances = initial_balances or {Currency.USDT: Decimal("100000"), Currency.BTC: Decimal("5")}
    clk = clock or _make_clock()
    rng_ = rng or random.Random(42)  # noqa: S311

    def _book_fn(p: Pair) -> Orderbook:
        if p == pair:
            return resolved_book
        if p == _ETH_USDT:
            return _ETH_BOOK
        return resolved_book

    def _fee_fn(p: Pair) -> FeeSchedule:
        if p == _ETH_USDT:
            return _ETH_FEES
        return resolved_fees

    return SimulatedExchangeAdapter(
        fill_model=OrderbookWalkFillModel(apply_slippage=False),
        orderbook_fn=_book_fn,
        clock=clk,
        fee_schedule_fn=_fee_fn,
        initial_balances=balances,
        rng=rng_,
    )


def _buy_order(
    qty: Decimal = Decimal("0.1"),
    pair: Pair = _BTC_USDT,
    limit_price: Decimal | None = None,
) -> Order:
    return Order(
        side=OrderSide.BUY,
        pair=pair,
        qty=Quantity(amount=qty, asset=pair.base),
        limit_price=limit_price,
        strategy_id=_STRATEGY_ID,
    )


def _sell_order(
    qty: Decimal = Decimal("0.1"),
    pair: Pair = _BTC_USDT,
    limit_price: Decimal | None = None,
) -> Order:
    return Order(
        side=OrderSide.SELL,
        pair=pair,
        qty=Quantity(amount=qty, asset=pair.base),
        limit_price=limit_price,
        strategy_id=_STRATEGY_ID,
    )


# ---------------------------------------------------------------------------
# Port conformance
# ---------------------------------------------------------------------------


class TestPortConformance:
    """SimulatedExchangeAdapter must match every ExchangePort method signature."""

    @pytest.mark.parametrize("method_name", sorted(_PORT_METHODS))
    def test_method_signature_matches_the_port(self, method_name: str) -> None:
        """Parameter names, kinds and annotations equal the port's, so no adapter drift.

        Methods are keyword-only on both sides; a renamed or retyped parameter
        would break the composition root that swaps this adapter for another.
        """
        port_sig = inspect.signature(getattr(ExchangePort, method_name))
        adapter_sig = inspect.signature(getattr(SimulatedExchangeAdapter, method_name))
        assert [(p.name, p.kind) for p in adapter_sig.parameters.values()] == [
            (p.name, p.kind) for p in port_sig.parameters.values()
        ]
        assert get_type_hints(getattr(SimulatedExchangeAdapter, method_name)) == get_type_hints(
            getattr(ExchangePort, method_name)
        )


# ---------------------------------------------------------------------------
# place_market_order
# ---------------------------------------------------------------------------


class TestPlaceMarketOrder:
    """Behaviour of place_market_order."""

    def test_buy_returns_fill(self) -> None:
        """Market BUY returns a Fill with executed_qty > 0."""
        adapter = _make_adapter()
        order = _buy_order()
        result = adapter.place_market_order(side=OrderSide.BUY, order=order)
        assert isinstance(result, Fill), (
            f"place_market_order(BUY) must return Fill, got {type(result).__name__!r}"
        )
        assert result.executed_qty > Decimal("0"), (
            f"Fill.executed_qty must be > 0, got {result.executed_qty!r}"
        )

    def test_sell_returns_fill(self) -> None:
        """Market SELL returns a Fill with executed_qty > 0."""
        adapter = _make_adapter()
        order = _sell_order()
        result = adapter.place_market_order(side=OrderSide.SELL, order=order)
        assert isinstance(result, Fill), (
            f"place_market_order(SELL) must return Fill, got {type(result).__name__!r}"
        )
        assert result.executed_qty > Decimal("0"), (
            f"Fill.executed_qty must be > 0, got {result.executed_qty!r}"
        )

    def test_raises_on_limit_price_set(self) -> None:
        """Raises ValueError when order.limit_price is set."""
        adapter = _make_adapter()
        order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.1"), asset=Currency.BTC),
            limit_price=Decimal("60000"),
            strategy_id=_STRATEGY_ID,
        )
        with pytest.raises(ValueError, match="limit_price must be None"):
            adapter.place_market_order(side=OrderSide.BUY, order=order)

    def test_raises_runtime_error_on_rejection(self) -> None:
        """Raises RuntimeError when fill model returns Rejection (empty book)."""
        empty_book = Orderbook(
            pair=_BTC_USDT,
            bids=(OrderbookLevel(price=Decimal("59999"), quantity=Decimal("10")),),
            asks=(),
            snapshot_time=_T0,
        )
        # The OrderbookWalkFillModel will reject a BUY with no asks.
        adapter = _make_adapter(book=empty_book)
        order = _buy_order()
        with pytest.raises(RuntimeError, match="rejected a market order"):
            adapter.place_market_order(side=OrderSide.BUY, order=order)

    def test_fill_timestamp_matches_clock(self) -> None:
        """Fill.timestamp equals the clock's current time."""
        clock = _make_clock(start=_T0)
        adapter = _make_adapter(clock=clock)
        order = _buy_order()
        fill = adapter.place_market_order(side=OrderSide.BUY, order=order)
        assert fill.timestamp == _T0, (
            f"Fill.timestamp must equal clock.now()={_T0!r}, got {fill.timestamp!r}"
        )

    def test_fill_avg_price_matches_ask(self) -> None:
        """Fill avg_price is the best ask for a single-level BUY."""
        adapter = _make_adapter()
        order = _buy_order(qty=Decimal("0.1"))
        fill = adapter.place_market_order(side=OrderSide.BUY, order=order)
        assert fill.avg_price == Decimal("60001"), (
            f"Fill avg_price must be best ask 60001, got {fill.avg_price!r}"
        )

    def test_fill_avg_price_matches_bid_for_sell(self) -> None:
        """Fill avg_price is the best bid for a single-level SELL."""
        adapter = _make_adapter()
        order = _sell_order(qty=Decimal("0.1"))
        fill = adapter.place_market_order(side=OrderSide.SELL, order=order)
        assert fill.avg_price == Decimal("60000"), (
            f"Fill avg_price must be best bid 60000, got {fill.avg_price!r}"
        )

    def test_fill_order_uses_passed_side(self) -> None:
        """The side kwarg overrides whatever order.side would be (adapter builds new Order)."""
        # Pass a BUY order but side=SELL — adapter should create SELL Order.
        adapter = _make_adapter()
        buy_order = _buy_order(qty=Decimal("0.1"))
        fill = adapter.place_market_order(side=OrderSide.SELL, order=buy_order)
        assert isinstance(fill, Fill), "place_market_order must return Fill when fill succeeds"
        assert fill.order.side == OrderSide.SELL, (
            f"Fill.order.side must be SELL (the passed side), got {fill.order.side!r}"
        )


# ---------------------------------------------------------------------------
# place_limit_order
# ---------------------------------------------------------------------------


class TestPlaceLimitOrder:
    """Behaviour of place_limit_order."""

    def test_buy_limit_at_ask_returns_fill(self) -> None:
        """BUY limit at the best ask fills 0.1 BTC at 60001 with the 26 bps taker fee."""
        adapter = _make_adapter()
        order = _buy_order(qty=Decimal("0.1"), limit_price=Decimal("60001"))
        assert adapter.place_limit_order(order=order) == Fill(
            order=order,
            executed_qty=Decimal("0.1"),
            avg_price=Decimal("60001"),
            # 0.1 * 60001 * 26 / 10_000
            fee=Money(amount=Decimal("15.60026"), currency=Currency.USDT),
            timestamp=_T0,
        )

    def test_buy_limit_below_ask_returns_rejection(self) -> None:
        """BUY limit below the best ask is rejected with the price-filter reason."""
        adapter = _make_adapter()
        order = _buy_order(qty=Decimal("0.1"), limit_price=Decimal("59000"))
        assert adapter.place_limit_order(order=order) == Rejection(
            order=order,
            reason="PRICE_FILTER: best ask 60001 > limit_price 59000 for BUY order on BTC/USDT",
        )

    def test_sell_limit_at_bid_returns_fill(self) -> None:
        """SELL limit at the best bid fills 0.1 BTC at 60000 with the 26 bps taker fee."""
        adapter = _make_adapter()
        order = _sell_order(qty=Decimal("0.1"), limit_price=Decimal("60000"))
        assert adapter.place_limit_order(order=order) == Fill(
            order=order,
            executed_qty=Decimal("0.1"),
            avg_price=Decimal("60000"),
            # 0.1 * 60000 * 26 / 10_000
            fee=Money(amount=Decimal("15.6"), currency=Currency.USDT),
            timestamp=_T0,
        )

    def test_sell_limit_above_bid_returns_rejection(self) -> None:
        """SELL limit above the best bid is rejected with the price-filter reason."""
        adapter = _make_adapter()
        order = _sell_order(qty=Decimal("0.1"), limit_price=Decimal("70000"))
        assert adapter.place_limit_order(order=order) == Rejection(
            order=order,
            reason="PRICE_FILTER: best bid 60000 < limit_price 70000 for SELL order on BTC/USDT",
        )

    def test_raises_on_missing_limit_price(self) -> None:
        """Raises ValueError when limit_price is None."""
        adapter = _make_adapter()
        order = _buy_order()  # no limit_price
        with pytest.raises(ValueError, match="limit_price must not be None"):
            adapter.place_limit_order(order=order)

    def test_rejection_does_not_modify_balance(self) -> None:
        """Rejected limit order leaves balances unchanged."""
        balances = {Currency.USDT: Decimal("100000"), Currency.BTC: Decimal("5")}
        adapter = _make_adapter(initial_balances=balances)
        order = _buy_order(qty=Decimal("0.1"), limit_price=Decimal("1"))
        adapter.place_limit_order(order=order)
        assert adapter.get_balance(currency=Currency.USDT).amount == Decimal("100000"), (
            "USDT balance must not change after a rejected limit order"
        )
        assert adapter.get_balance(currency=Currency.BTC).amount == Decimal("5"), (
            "BTC balance must not change after a rejected limit order"
        )


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------


class TestCancelOrder:
    """Behaviour of cancel_order."""

    def test_cancel_unknown_order_is_noop(self) -> None:
        """Cancelling a non-existent order_id is a silent no-op (idempotent)."""
        adapter = _make_adapter()
        # Must not raise
        adapter.cancel_order(order_id="non-existent-id-12345")


# ---------------------------------------------------------------------------
# get_orderbook
# ---------------------------------------------------------------------------


class TestGetOrderbook:
    """Behaviour of get_orderbook."""

    def test_delegates_to_orderbook_fn(self) -> None:
        """get_orderbook(pair=X) returns exactly what orderbook_fn(X) returns."""
        adapter = _make_adapter()
        result = adapter.get_orderbook(pair=_BTC_USDT)
        assert result is _SIMPLE_BOOK, (
            "get_orderbook must return exactly what orderbook_fn returns — no wrapping or copying."
        )

    def test_passes_correct_pair(self) -> None:
        """get_orderbook passes the requested pair to the callable."""
        received: list[Pair] = []

        def _capture_fn(p: Pair) -> Orderbook:
            received.append(p)
            return _SIMPLE_BOOK

        adapter = SimulatedExchangeAdapter(
            fill_model=OrderbookWalkFillModel(),
            orderbook_fn=_capture_fn,
            clock=_make_clock(),
            fee_schedule_fn=lambda p: _STANDARD_FEES,
            initial_balances={},
        )
        adapter.get_orderbook(pair=_ETH_USDT)
        assert received == [_ETH_USDT], (
            f"orderbook_fn must receive the requested pair, got {received!r}"
        )


# ---------------------------------------------------------------------------
# get_balance
# ---------------------------------------------------------------------------


class TestGetBalance:
    """Behaviour of get_balance."""

    def test_returns_initial_balance(self) -> None:
        """get_balance returns the initial balance set at construction."""
        adapter = _make_adapter(
            initial_balances={Currency.USDT: Decimal("50000"), Currency.BTC: Decimal("2")}
        )
        qty = adapter.get_balance(currency=Currency.USDT)
        assert qty.amount == Decimal("50000"), f"USDT balance must be 50000, got {qty.amount!r}"
        assert qty.asset == Currency.USDT, f"Balance asset must be USDT, got {qty.asset!r}"

    def test_returns_zero_for_unknown_currency(self) -> None:
        """get_balance returns zero Quantity for a currency not in the ledger."""
        adapter = _make_adapter(initial_balances={})
        qty = adapter.get_balance(currency=Currency.ETH)
        assert qty.amount == Decimal("0"), (
            f"Balance for unlisted currency must be 0, got {qty.amount!r}"
        )
        assert qty.asset == Currency.ETH, (
            f"Balance asset must match queried currency, got {qty.asset!r}"
        )


# ---------------------------------------------------------------------------
# get_fees
# ---------------------------------------------------------------------------


class TestGetFees:
    """Behaviour of get_fees."""

    def test_delegates_to_fee_schedule_fn(self) -> None:
        """get_fees(pair=X) returns what fee_schedule_fn(X) returns."""
        adapter = _make_adapter()
        result = adapter.get_fees(pair=_BTC_USDT)
        assert result is _STANDARD_FEES, (
            "get_fees must return exactly what fee_schedule_fn returns."
        )

    def test_passes_correct_pair(self) -> None:
        """get_fees passes the requested pair to the callable."""
        received: list[Pair] = []

        def _capture_fn(p: Pair) -> FeeSchedule:
            received.append(p)
            return _STANDARD_FEES

        adapter = SimulatedExchangeAdapter(
            fill_model=OrderbookWalkFillModel(),
            orderbook_fn=lambda p: _SIMPLE_BOOK,
            clock=_make_clock(),
            fee_schedule_fn=_capture_fn,
            initial_balances={},
        )
        adapter.get_fees(pair=_ETH_USDT)
        assert received == [_ETH_USDT], (
            f"fee_schedule_fn must receive the requested pair, got {received!r}"
        )


# ---------------------------------------------------------------------------
# Balance ledger — BUY deduction
# ---------------------------------------------------------------------------


class TestBuyBalanceDeduction:
    """BUY fills must debit quote and credit base correctly."""

    def test_quote_debited_on_buy(self) -> None:
        """BUY fill debits quote balance by at least qty x price."""
        initial_usdt = Decimal("100000")
        adapter = _make_adapter(
            initial_balances={Currency.USDT: initial_usdt, Currency.BTC: Decimal("0")}
        )
        qty = Decimal("0.5")
        fill = adapter.place_market_order(side=OrderSide.BUY, order=_buy_order(qty=qty))
        # cost = qty x avg_price + fee
        expected_cost = qty * fill.avg_price + fill.fee.amount
        expected_usdt = initial_usdt - expected_cost
        actual_usdt = adapter.get_balance(currency=Currency.USDT).amount
        # Balance must have decreased (strictly less than initial).
        assert actual_usdt < initial_usdt, (
            f"USDT balance must decrease after BUY: initial={initial_usdt!r}, "
            f"actual={actual_usdt!r}"
        )
        assert actual_usdt == expected_usdt, (
            f"USDT balance after BUY: expected {expected_usdt!r}, got {actual_usdt!r}"
        )

    def test_base_credited_on_buy(self) -> None:
        """BUY fill credits base balance by executed_qty."""
        adapter = _make_adapter(
            initial_balances={Currency.USDT: Decimal("100000"), Currency.BTC: Decimal("0")}
        )
        qty = Decimal("0.5")
        fill = adapter.place_market_order(side=OrderSide.BUY, order=_buy_order(qty=qty))
        actual_btc = adapter.get_balance(currency=Currency.BTC).amount
        assert actual_btc == fill.executed_qty, (
            f"BTC balance after BUY: expected {fill.executed_qty!r}, got {actual_btc!r}"
        )


# ---------------------------------------------------------------------------
# Balance ledger — SELL deduction
# ---------------------------------------------------------------------------


class TestSellBalanceDeduction:
    """SELL fills must debit base and credit quote correctly."""

    def test_base_debited_on_sell(self) -> None:
        """SELL fill debits base balance by executed_qty."""
        initial_btc = Decimal("2")
        adapter = _make_adapter(
            initial_balances={Currency.USDT: Decimal("0"), Currency.BTC: initial_btc}
        )
        qty = Decimal("0.5")
        fill = adapter.place_market_order(side=OrderSide.SELL, order=_sell_order(qty=qty))
        actual_btc = adapter.get_balance(currency=Currency.BTC).amount
        expected_btc = initial_btc - fill.executed_qty
        # BTC must be strictly less than initial after SELL.
        assert actual_btc < initial_btc, (
            f"BTC balance must decrease after SELL: initial={initial_btc!r}, actual={actual_btc!r}"
        )
        assert actual_btc == expected_btc, (
            f"BTC balance after SELL: expected {expected_btc!r}, got {actual_btc!r}"
        )

    def test_quote_credited_on_sell(self) -> None:
        """SELL fill credits quote balance by qty x price - fee."""
        adapter = _make_adapter(
            initial_balances={Currency.USDT: Decimal("0"), Currency.BTC: Decimal("2")}
        )
        qty = Decimal("0.5")
        fill = adapter.place_market_order(side=OrderSide.SELL, order=_sell_order(qty=qty))
        expected_usdt = fill.executed_qty * fill.avg_price - fill.fee.amount
        actual_usdt = adapter.get_balance(currency=Currency.USDT).amount
        # Starting from 0, USDT must be positive after SELL.
        assert actual_usdt > Decimal("0"), (
            f"USDT balance must be > 0 after SELL (started at 0, gained proceeds), "
            f"got {actual_usdt!r}"
        )
        assert actual_usdt == expected_usdt, (
            f"USDT balance after SELL: expected {expected_usdt!r}, got {actual_usdt!r}"
        )


# ---------------------------------------------------------------------------
# Lot tracking
# ---------------------------------------------------------------------------


class TestLotTracking:
    """BUY fills open Lots; SELL fills do not."""

    def test_buy_fill_opens_lot(self) -> None:
        """A BUY fill opens one lot carrying the fill's quantity, USDT basis and fee."""
        adapter = _make_adapter()
        adapter.place_market_order(side=OrderSide.BUY, order=_buy_order(qty=Decimal("0.1")))
        (lot,) = adapter.lot_ledger
        assert lot.original_qty == Quantity(amount=Decimal("0.1"), asset=Currency.BTC)
        assert lot.remaining_qty == lot.original_qty
        # No FX rate here, so the EUR basis stays unset and the native basis is
        # (0.1 * 60001 + 0.1 * 60001 * 26 / 10_000) / 0.1.
        assert lot.cost_basis_per_unit is None
        assert lot.cost_basis_native_per_unit == Money(
            amount=Decimal("60157.0026"), currency=Currency.USDT
        )

    def test_sell_fill_does_not_open_lot(self) -> None:
        """A SELL fill does not create any Lot."""
        adapter = _make_adapter(
            initial_balances={Currency.USDT: Decimal("0"), Currency.BTC: Decimal("5")}
        )
        adapter.place_market_order(side=OrderSide.SELL, order=_sell_order(qty=Decimal("0.1")))
        assert adapter.lot_ledger == []

    def test_lot_asset_matches_fill(self) -> None:
        """Lot.asset matches the fill's base currency."""
        adapter = _make_adapter()
        adapter.place_market_order(side=OrderSide.BUY, order=_buy_order())
        lot = adapter.lot_ledger[0]
        assert lot.asset == Currency.BTC, (
            f"Lot.asset must be BTC (base of BTC_USDT), got {lot.asset!r}"
        )

    def test_lot_qty_matches_fill(self) -> None:
        """Lot.original_qty equals the fill's executed_qty."""
        adapter = _make_adapter()
        qty = Decimal("0.3")
        fill = adapter.place_market_order(side=OrderSide.BUY, order=_buy_order(qty=qty))
        lot = adapter.lot_ledger[0]
        assert lot.original_qty.amount == fill.executed_qty, (
            f"Lot.original_qty must equal fill.executed_qty={fill.executed_qty!r}, "
            f"got {lot.original_qty.amount!r}"
        )

    def test_lot_strategy_id_set(self) -> None:
        """Lot.strategy_id equals the strategy_id of the order that opened it."""
        sid = StrategyId("test-strat")
        adapter = _make_adapter()
        adapter.place_market_order(
            side=OrderSide.BUY,
            order=Order(
                side=OrderSide.BUY,
                pair=_BTC_USDT,
                qty=Quantity(amount=Decimal("0.1"), asset=Currency.BTC),
                strategy_id=sid,
            ),
        )
        lot = adapter.lot_ledger[0]
        assert lot.strategy_id == sid, f"Lot.strategy_id must be {sid!r}, got {lot.strategy_id!r}"

    def test_lot_opened_at_matches_clock(self) -> None:
        """Lot.opened_at equals clock.now() at fill time."""
        clock = _make_clock(start=_T0)
        adapter = _make_adapter(clock=clock)
        adapter.place_market_order(side=OrderSide.BUY, order=_buy_order())
        lot = adapter.lot_ledger[0]
        assert lot.opened_at == _T0, f"Lot.opened_at must be {_T0!r}, got {lot.opened_at!r}"

    def test_multiple_buys_grow_lot_ledger(self) -> None:
        """Each BUY fill appends its own lot, with a distinct id, in fill order."""
        adapter = _make_adapter()
        for qty in ("0.05", "0.06", "0.07"):
            adapter.place_market_order(side=OrderSide.BUY, order=_buy_order(qty=Decimal(qty)))
        assert [lot.original_qty.amount for lot in adapter.lot_ledger] == [
            Decimal("0.05"),
            Decimal("0.06"),
            Decimal("0.07"),
        ]
        assert len({lot.lot_id for lot in adapter.lot_ledger}) == 3


# ---------------------------------------------------------------------------
# Deterministic replay under seeded RNG
# ---------------------------------------------------------------------------


class TestDeterministicReplay:
    """Same seed + same inputs → identical Fill."""

    def test_same_seed_produces_identical_fill(self) -> None:
        """Two adapters built with the same seed return equal Fills."""
        rng_a = random.Random(1337)  # noqa: S311
        rng_b = random.Random(1337)  # noqa: S311
        adapter_a = _make_adapter(rng=rng_a)
        adapter_b = _make_adapter(rng=rng_b)
        order = _buy_order(qty=Decimal("0.2"))
        fill_a = adapter_a.place_market_order(side=OrderSide.BUY, order=order)
        fill_b = adapter_b.place_market_order(side=OrderSide.BUY, order=order)
        assert fill_a.executed_qty == fill_b.executed_qty, (
            f"Seeded replay: executed_qty must match: "
            f"{fill_a.executed_qty!r} vs {fill_b.executed_qty!r}"
        )
        assert fill_a.avg_price == fill_b.avg_price, (
            f"Seeded replay: avg_price must match: {fill_a.avg_price!r} vs {fill_b.avg_price!r}"
        )


class TestStrategyAttribution:
    """Fills and lots from the adapter keep the submitting order's strategy."""

    def test_market_fill_keeps_the_order_strategy_id(self) -> None:
        """place_market_order returns a Fill whose strategy_id is the order's, not a default."""
        sid = StrategyId("swing-donchian-4h")
        order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.1"), asset=Currency.BTC),
            strategy_id=sid,
        )
        fill = _make_adapter().place_market_order(side=OrderSide.BUY, order=order)
        assert fill.strategy_id == sid

    def test_limit_fill_keeps_the_order_strategy_id(self) -> None:
        """place_limit_order returns a Fill whose strategy_id is the order's."""
        sid = StrategyId("swing-donchian-4h")
        order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.1"), asset=Currency.BTC),
            strategy_id=sid,
            limit_price=Decimal("1000000"),
        )
        result = _make_adapter().place_limit_order(order=order)
        assert isinstance(result, Fill)
        assert result.strategy_id == sid
