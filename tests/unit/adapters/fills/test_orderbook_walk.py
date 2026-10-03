"""Unit tests for OrderbookWalkFillModel.

Every test protects one invariant of the orderbook-walk fill model:

- Port conformance: ``isinstance(impl, FillModelPort)`` → ``True``.
- **Anti-Zipline** (SA-2 AP-1): market order at bar T fills at bar T+1
  open — the fill price is the first level in the book, never the
  previous bar's close.
- Fill quantity ≤ order quantity (never over-fill).
- Fill price is computed from the walked book levels (VWAP).
- Deterministic replay: same ``random.Random(seed)`` + same inputs →
  identical ``Fill`` results.
- Rejection matrix: zero qty, insufficient depth, PRICE_FILTER.
- Slippage is applied only when ``apply_slippage=True``.
- Fee is always taker-rate x executed_qty x avg_price.
- ``fill_order`` raises ``ValueError`` on naive clock.

Reference: spec-2b-orderbook-walk-fill-model/change.md,
ADR-011 D-10/D-11/D-02, pre-reg §6.2.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.adapters.fills import OrderbookWalkFillModel
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.fill_model import FillModelPort

_STRATEGY_ID = StrategyId("test-strategy")

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)

_CLOCK = datetime(2026, 8, 25, 10, 0, 0, tzinfo=UTC)

_STANDARD_SCHEDULE = FeeSchedule.from_tiers(
    pair=_BTC_USDT,
    tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26),),
)

# A single-level book: best ask 60 001 USDT, best bid 60 000 USDT.
_SINGLE_LEVEL_BOOK = Orderbook(
    pair=_BTC_USDT,
    bids=(OrderbookLevel(price=Decimal("60000"), quantity=Decimal("1")),),
    asks=(OrderbookLevel(price=Decimal("60001"), quantity=Decimal("1")),),
    snapshot_time=_CLOCK,
)

# A deep multi-level book for large order tests.
_DEEP_BOOK = Orderbook(
    pair=_BTC_USDT,
    bids=(
        OrderbookLevel(price=Decimal("60000"), quantity=Decimal("2")),
        OrderbookLevel(price=Decimal("59999"), quantity=Decimal("3")),
        OrderbookLevel(price=Decimal("59998"), quantity=Decimal("5")),
    ),
    asks=(
        OrderbookLevel(price=Decimal("60001"), quantity=Decimal("2")),
        OrderbookLevel(price=Decimal("60002"), quantity=Decimal("3")),
        OrderbookLevel(price=Decimal("60003"), quantity=Decimal("5")),
    ),
    snapshot_time=_CLOCK,
)


def _make_order(
    *,
    side: OrderSide = OrderSide.BUY,
    qty_amount: Decimal = Decimal("0.5"),
    limit_price: Decimal | None = None,
    pair: Pair | None = None,
) -> Order:
    """Build a minimal valid Order."""
    p = pair or _BTC_USDT
    return Order(
        side=side,
        pair=p,
        qty=Quantity(amount=qty_amount, asset=p.base),
        limit_price=limit_price,
        strategy_id=_STRATEGY_ID,
    )


def _make_model(
    *,
    apply_slippage: bool = False,
    entry_slippage_bps: int = 5,
    stop_slippage_bps: int = 15,
    seed: int | None = None,
) -> OrderbookWalkFillModel:
    """Build a model, optionally with a seeded RNG for determinism tests."""
    rng = random.Random(seed) if seed is not None else random.Random()  # noqa: S311
    return OrderbookWalkFillModel(
        apply_slippage=apply_slippage,
        entry_slippage_bps=entry_slippage_bps,
        stop_slippage_bps=stop_slippage_bps,
        rng=rng,
    )


# ---------------------------------------------------------------------------
# Port conformance
# ---------------------------------------------------------------------------


class TestPortConformance:
    """OrderbookWalkFillModel must satisfy FillModelPort at the structural level."""

    def test_isinstance_fill_model_port(self) -> None:
        """``isinstance(OrderbookWalkFillModel(), FillModelPort)`` must be True.

        FillModelPort is @runtime_checkable — this test enforces structural
        conformance at the Protocol level.
        """
        model = OrderbookWalkFillModel()
        assert isinstance(model, FillModelPort), (
            "OrderbookWalkFillModel must satisfy FillModelPort via structural subtyping. "
            "Check that fill_order signature matches the Protocol exactly."
        )

    def test_fill_order_returns_fill_or_rejection(self) -> None:
        """fill_order return value must be Fill or Rejection (discriminated union)."""
        model = _make_model()
        order = _make_order(qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, (Fill, Rejection)), (
            f"fill_order must return Fill or Rejection, got {type(result).__name__!r}. "
            "The discriminated union contract is defined in spec-2a."
        )


# ---------------------------------------------------------------------------
# Anti-Zipline test (SA-2 AP-1)
# ---------------------------------------------------------------------------


class TestAntiZipline:
    """Market order fills at the orderbook's first level — NOT the previous close.

    ADR-011 D-10: the caller is responsible for passing the NEXT bar's open
    as the first level in the book. The fill model is agnostic to what the
    synthetic book represents — it walks what it receives. The test constructs
    a book whose first ask level IS bar T+1 open and verifies the fill price
    matches exactly.
    """

    def test_market_buy_fills_at_best_ask_not_prior_close(self) -> None:
        """Market BUY fills at the best ask (= next bar's open), not the prior close.

        Anti-Zipline invariant (SA-2 AP-1): fill_order must NOT apply the
        prior-bar-close logic that produces look-ahead bias. The composition
        root provides a synthetic book where asks[0].price == next_bar_open.
        """
        next_bar_open = Decimal("61000")
        prior_bar_close = Decimal("60800")  # must NOT appear in fill

        # Synthetic book: asks[0] = next bar open (composition root wires this).
        book = Orderbook(
            pair=_BTC_USDT,
            bids=(OrderbookLevel(price=prior_bar_close - Decimal("1"), quantity=Decimal("1")),),
            asks=(OrderbookLevel(price=next_bar_open, quantity=Decimal("1")),),
            snapshot_time=_CLOCK,
        )

        model = _make_model(apply_slippage=False)
        order = _make_order(side=OrderSide.BUY, qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=book, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )

        assert isinstance(result, Fill), (
            f"Expected Fill (market order against liquid book), got {type(result).__name__!r}."
        )
        assert result.avg_price == next_bar_open, (
            f"Market BUY must fill at best ask (next bar open) {next_bar_open}, "
            f"got {result.avg_price!r}. Fill model must NOT use prior_bar_close={prior_bar_close}. "
            "Anti-Zipline invariant violated (SA-2 AP-1)."
        )
        assert result.avg_price != prior_bar_close, (
            f"Fill price {result.avg_price!r} equals prior_bar_close={prior_bar_close} — "
            "this is the Zipline look-ahead bias bug. Market orders fill at T+1 open, "
            "never at T close."
        )

    def test_market_sell_fills_at_best_bid_not_prior_close(self) -> None:
        """Market SELL fills at the best bid (= next bar's open), not the prior close.

        Symmetric anti-Zipline check for the SELL side.
        """
        next_bar_open_bid = Decimal("60900")
        prior_bar_close = Decimal("61000")  # must NOT appear in fill

        book = Orderbook(
            pair=_BTC_USDT,
            bids=(OrderbookLevel(price=next_bar_open_bid, quantity=Decimal("1")),),
            asks=(OrderbookLevel(price=next_bar_open_bid + Decimal("1"), quantity=Decimal("1")),),
            snapshot_time=_CLOCK,
        )

        model = _make_model(apply_slippage=False)
        order = _make_order(side=OrderSide.SELL, qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=book, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )

        assert isinstance(result, Fill), (
            f"Expected Fill (market SELL against liquid book), got {type(result).__name__!r}."
        )
        assert result.avg_price == next_bar_open_bid, (
            f"Market SELL must fill at best bid (next bar open) {next_bar_open_bid}, "
            f"got {result.avg_price!r}. Anti-Zipline invariant violated (SA-2 AP-1, SELL side)."
        )
        assert result.avg_price != prior_bar_close, (
            f"Fill price {result.avg_price!r} equals prior_bar_close={prior_bar_close} — "
            "Zipline bias. Market SELL fills at next bar bid, never at prior close."
        )


# ---------------------------------------------------------------------------
# Quantity bound invariant
# ---------------------------------------------------------------------------


class TestQuantityBound:
    """Fill quantity must never exceed order quantity."""

    def test_full_fill_executed_qty_equals_order_qty(self) -> None:
        """When the book has enough depth, executed_qty == order.qty.amount."""
        model = _make_model()
        order = _make_order(qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill with sufficient depth, got {type(result).__name__!r}."
        )
        assert result.executed_qty == order.qty.amount, (
            f"executed_qty {result.executed_qty!r} != order.qty.amount {order.qty.amount!r}. "
            "Full fill must consume exactly the order quantity."
        )

    @given(
        qty=st.decimals(min_value="0.0001", max_value="0.9", allow_nan=False, allow_infinity=False)
    )
    @settings(max_examples=50)
    def test_executed_qty_never_exceeds_order_qty(self, qty: Decimal) -> None:
        """Property: executed_qty ≤ order.qty.amount for any valid fill.

        This is the no-overfill invariant — the fill model must NEVER
        allocate more base asset than was requested.
        """
        model = _make_model()
        order = _make_order(qty_amount=qty)
        result = model.fill_order(
            order=order, orderbook=_DEEP_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        if isinstance(result, Fill):
            assert result.executed_qty <= order.qty.amount, (
                f"No-overfill invariant violated: executed_qty {result.executed_qty!r} "
                f"> order.qty.amount {order.qty.amount!r} (qty={qty!r})."
            )

    def test_fill_qty_preserves_fill_domain_invariant(self) -> None:
        """Fill.executed_qty must pass Fill's own domain validation (≤ order.qty).

        This is a belt-and-suspenders check: Fill.__post_init__ already
        enforces this, but we verify the fill model produces a value that
        does not cause the Fill constructor to raise.
        """
        model = _make_model()
        order = _make_order(qty_amount=Decimal("0.1"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        # If Fill constructor raises, the model produced an invalid executed_qty.
        assert isinstance(result, (Fill, Rejection)), (
            "fill_order must return Fill or Rejection, never raise on valid input."
        )


# ---------------------------------------------------------------------------
# Price-in-book-range invariant
# ---------------------------------------------------------------------------


class TestFillPrice:
    """Fill price must derive from the walked book levels."""

    def test_buy_fill_price_equals_best_ask_when_single_level(self) -> None:
        """BUY fill price == best ask when order fits in a single level."""
        model = _make_model(apply_slippage=False)
        order = _make_order(side=OrderSide.BUY, qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill for liquid market BUY, got {type(result).__name__!r}."
        )
        expected_price = Decimal("60001")  # best ask
        assert result.avg_price == expected_price, (
            f"BUY avg_price {result.avg_price!r} != best ask {expected_price!r}. "
            "Single-level BUY must fill at the best ask exactly."
        )

    def test_sell_fill_price_equals_best_bid_when_single_level(self) -> None:
        """SELL fill price == best bid when order fits in a single level."""
        model = _make_model(apply_slippage=False)
        order = _make_order(side=OrderSide.SELL, qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill for liquid market SELL, got {type(result).__name__!r}."
        )
        expected_price = Decimal("60000")  # best bid
        assert result.avg_price == expected_price, (
            f"SELL avg_price {result.avg_price!r} != best bid {expected_price!r}. "
            "Single-level SELL must fill at the best bid exactly."
        )

    def test_buy_vwap_multi_level(self) -> None:
        """BUY VWAP is correctly computed across multiple ask levels."""
        model = _make_model(apply_slippage=False)
        # Order needs 4 BTC; deep book has asks: 2@60001, 3@60002, 5@60003.
        # Consumes 2@60001 + 2@60002 = 4 BTC total.
        # VWAP = (2*60001 + 2*60002) / 4 = (120002 + 120004) / 4 = 240006 / 4 = 60001.5
        order = _make_order(side=OrderSide.BUY, qty_amount=Decimal("4"))
        result = model.fill_order(
            order=order, orderbook=_DEEP_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill for 4 BTC BUY against deep book, got {type(result).__name__!r}."
        )
        expected_vwap = Decimal("60001.5")
        assert result.avg_price == expected_vwap, (
            f"VWAP {result.avg_price!r} != expected {expected_vwap!r} for multi-level BUY. "
            "VWAP must be computed as sum(price*qty) / total_qty across walked levels."
        )

    def test_sell_vwap_multi_level(self) -> None:
        """SELL VWAP is correctly computed across multiple bid levels."""
        model = _make_model(apply_slippage=False)
        # Order needs 4 BTC SELL; deep book bids: 2@60000, 3@59999, 5@59998.
        # Consumes 2@60000 + 2@59999 = 4 BTC.
        # VWAP = (2*60000 + 2*59999) / 4 = (120000 + 119998) / 4 = 239998 / 4 = 59999.5
        order = _make_order(side=OrderSide.SELL, qty_amount=Decimal("4"))
        result = model.fill_order(
            order=order, orderbook=_DEEP_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill for 4 BTC SELL against deep book, got {type(result).__name__!r}."
        )
        expected_vwap = Decimal("59999.5")
        assert result.avg_price == expected_vwap, (
            f"VWAP {result.avg_price!r} != expected {expected_vwap!r} for multi-level SELL. "
            "VWAP computation must weight each level by consumed quantity."
        )

    def test_fill_avg_price_is_positive(self) -> None:
        """avg_price must always be > 0 (enforced by Fill domain invariant)."""
        model = _make_model()
        order = _make_order(qty_amount=Decimal("0.1"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        if isinstance(result, Fill):
            assert result.avg_price > Decimal("0"), (
                f"avg_price {result.avg_price!r} must be > 0. "
                "A non-positive fill price is non-physical."
            )


# ---------------------------------------------------------------------------
# Slippage tests
# ---------------------------------------------------------------------------


class TestSlippage:
    """Slippage is applied only when apply_slippage=True."""

    def test_no_slippage_by_default(self) -> None:
        """Default model (apply_slippage=False) does not add slippage."""
        model_no_slip = _make_model(apply_slippage=False)
        model_with_slip = _make_model(apply_slippage=True, entry_slippage_bps=5)
        order = _make_order(side=OrderSide.BUY, qty_amount=Decimal("0.5"))

        result_no = model_no_slip.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        result_with = model_with_slip.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )

        assert isinstance(result_no, Fill), (
            f"Expected Fill (no slippage), got {type(result_no).__name__!r}."
        )
        assert isinstance(result_with, Fill), (
            f"Expected Fill (with slippage), got {type(result_with).__name__!r}."
        )
        assert result_with.avg_price > result_no.avg_price, (
            f"BUY with slippage ({result_with.avg_price!r}) must have higher price than "
            f"without slippage ({result_no.avg_price!r}). Slippage marks BUY price up."
        )

    def test_entry_slippage_buy_marks_price_up(self) -> None:
        """BUY with apply_slippage=True: avg_price = best_ask * (1 + bps/10000)."""
        bps = 5
        model = _make_model(apply_slippage=True, entry_slippage_bps=bps)
        order = _make_order(side=OrderSide.BUY, qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill for BUY with slippage, got {type(result).__name__!r}."
        )
        best_ask = Decimal("60001")
        expected = best_ask * (Decimal("1") + Decimal(bps) / Decimal("10000"))
        assert result.avg_price == expected, (
            f"BUY slippage: avg_price {result.avg_price!r} != expected {expected!r}. "
            f"Entry slippage for BUY = best_ask * (1 + {bps}/10000)."
        )

    def test_entry_slippage_sell_marks_price_down(self) -> None:
        """SELL with apply_slippage=True: avg_price = best_bid * (1 - bps/10000)."""
        bps = 5
        model = _make_model(apply_slippage=True, entry_slippage_bps=bps)
        order = _make_order(side=OrderSide.SELL, qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill for SELL with slippage, got {type(result).__name__!r}."
        )
        best_bid = Decimal("60000")
        expected = best_bid * (Decimal("1") - Decimal(bps) / Decimal("10000"))
        assert result.avg_price == expected, (
            f"SELL slippage: avg_price {result.avg_price!r} != expected {expected!r}. "
            f"Entry slippage for SELL = best_bid * (1 - {bps}/10000)."
        )

    def test_shadow_mode_no_slippage_on_real_book(self) -> None:
        """Shadow mode (apply_slippage=False) does not add bps on top of real spread.

        In shadow mode the real spread is the only cost. Adding slippage on
        top of a live book would double-count the execution cost.
        """
        model = _make_model(apply_slippage=False)
        order = _make_order(side=OrderSide.BUY, qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill (shadow mode, no slippage), got {type(result).__name__!r}."
        )
        # Price must equal the raw walked price — no extra bps.
        raw_ask = Decimal("60001")
        assert result.avg_price == raw_ask, (
            f"Shadow mode avg_price {result.avg_price!r} != raw ask {raw_ask!r}. "
            "Shadow mode must NOT add slippage on top of the real book spread."
        )


# ---------------------------------------------------------------------------
# Fee tests
# ---------------------------------------------------------------------------


class TestFee:
    """Fee is always taker_rate x executed_qty x avg_price."""

    def test_fee_uses_taker_rate(self) -> None:
        """Fee = executed_qty x avg_price x taker_rate (is_maker=False)."""
        taker_bps = 26
        schedule = FeeSchedule.from_tiers(
            pair=_BTC_USDT,
            tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=taker_bps),),
        )
        model = _make_model(apply_slippage=False)
        order = _make_order(side=OrderSide.BUY, qty_amount=Decimal("1"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=schedule
        )
        assert isinstance(result, Fill), f"Expected Fill, got {type(result).__name__!r}."
        taker_rate = Decimal(taker_bps) / Decimal("10000")
        expected_fee = result.executed_qty * result.avg_price * taker_rate
        assert result.fee.amount == expected_fee, (
            f"Fee amount {result.fee.amount!r} != expected taker fee {expected_fee!r}. "
            f"Fee = executed_qty x avg_price x taker_rate ({taker_bps} bps)."
        )

    def test_fee_currency_is_quote_currency(self) -> None:
        """Fee must be denominated in the pair's quote currency (USDT for BTC/USDT)."""
        model = _make_model()
        order = _make_order(side=OrderSide.BUY, qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), f"Expected Fill, got {type(result).__name__!r}."
        assert result.fee.currency == Currency.USDT, (
            f"Fee currency {result.fee.currency!r} != USDT. "
            "Fees must be denominated in the pair's quote currency at the port boundary."
        )

    def test_fee_is_non_negative(self) -> None:
        """Fee must always be non-negative."""
        model = _make_model()
        order = _make_order(qty_amount=Decimal("0.1"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        if isinstance(result, Fill):
            assert result.fee.amount >= Decimal("0"), (
                f"Fee amount {result.fee.amount!r} is negative — fees cannot be rebates "
                "at this port level."
            )


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed + same inputs → identical Fill results."""

    def test_same_seed_produces_identical_fills(self) -> None:
        """Two models with the same seed produce identical Fill objects."""
        seed = 42
        order = _make_order(qty_amount=Decimal("0.5"))
        model_a = _make_model(seed=seed)
        model_b = _make_model(seed=seed)

        result_a = model_a.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        result_b = model_b.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )

        assert type(result_a) == type(result_b), (  # noqa: E721
            f"Determinism violation: result_a={type(result_a).__name__!r} "
            f"but result_b={type(result_b).__name__!r} with seed={seed!r}."
        )
        if isinstance(result_a, Fill) and isinstance(result_b, Fill):
            assert result_a.executed_qty == result_b.executed_qty, (
                f"Determinism violation: executed_qty differs: "
                f"{result_a.executed_qty!r} vs {result_b.executed_qty!r} (seed={seed!r})."
            )
            assert result_a.avg_price == result_b.avg_price, (
                f"Determinism violation: avg_price differs: "
                f"{result_a.avg_price!r} vs {result_b.avg_price!r} (seed={seed!r})."
            )
            assert result_a.fee.amount == result_b.fee.amount, (
                f"Determinism violation: fee amount differs: "
                f"{result_a.fee.amount!r} vs {result_b.fee.amount!r} (seed={seed!r})."
            )

    def test_different_seeds_do_not_guarantee_different_fills(self) -> None:
        """Different seeds MAY produce different results — documented boundary.

        For a purely deterministic model (VWAP without random tie-breaking at
        current scope), fills are identical regardless of seed. We verify the
        model at least runs without error with different seeds — the important
        invariant is that same seed → same result (tested above).
        """
        order = _make_order(qty_amount=Decimal("0.5"))
        for seed in (1, 2, 99, 12345):
            model = _make_model(seed=seed)
            result = model.fill_order(
                order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
            )
            assert isinstance(result, (Fill, Rejection)), (
                f"fill_order must return Fill or Rejection for seed={seed!r}, "
                f"got {type(result).__name__!r}."
            )

    def test_repeated_calls_with_same_inputs_are_consistent(self) -> None:
        """Multiple calls with identical inputs produce consistent results."""
        seed = 7
        order = _make_order(qty_amount=Decimal("0.3"))

        # Run model twice with a fresh same-seed RNG each time (reset by rebuilding).
        model_1 = _make_model(seed=seed)
        model_2 = _make_model(seed=seed)

        r1 = model_1.fill_order(
            order=order, orderbook=_DEEP_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        r2 = model_2.fill_order(
            order=order, orderbook=_DEEP_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )

        assert type(r1) == type(r2), (  # noqa: E721
            f"Repeated calls: result types differ ({type(r1).__name__!r} vs "
            f"{type(r2).__name__!r}) with seed={seed!r}."
        )
        if isinstance(r1, Fill) and isinstance(r2, Fill):
            assert r1.avg_price == r2.avg_price, (
                f"Repeated calls: avg_price differs {r1.avg_price!r} vs {r2.avg_price!r}. "
                f"Same seed + same inputs must produce identical fills."
            )


# ---------------------------------------------------------------------------
# Rejection matrix
# ---------------------------------------------------------------------------


class TestRejectionMatrix:
    """Verify all expected rejection paths return Rejection with the correct prefix."""

    def test_insufficient_depth_buy_no_asks(self) -> None:
        """BUY against a book with thin ask depth → INSUFFICIENT_DEPTH rejection."""
        model = _make_model()
        # Request more than available depth.
        order = _make_order(side=OrderSide.BUY, qty_amount=Decimal("1"))
        # A book with very thin asks (0.001 BTC available, 1 BTC requested).
        thin_ask_book = Orderbook(
            pair=_BTC_USDT,
            bids=(OrderbookLevel(price=Decimal("59999"), quantity=Decimal("5")),),
            asks=(OrderbookLevel(price=Decimal("60001"), quantity=Decimal("0.001")),),
            snapshot_time=_CLOCK,
        )
        result = model.fill_order(
            order=order, orderbook=thin_ask_book, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Rejection), (
            f"Expected Rejection (insufficient depth), got {type(result).__name__!r}."
        )
        assert "INSUFFICIENT_DEPTH" in result.reason, (
            f"Rejection reason must contain 'INSUFFICIENT_DEPTH', got {result.reason!r}. "
            "Thin book rejection must use the standard prefix."
        )

    def test_insufficient_depth_sell_thin_bids(self) -> None:
        """SELL against book with insufficient bid depth → INSUFFICIENT_DEPTH rejection."""
        thin_bid_book = Orderbook(
            pair=_BTC_USDT,
            bids=(OrderbookLevel(price=Decimal("59999"), quantity=Decimal("0.001")),),
            asks=(OrderbookLevel(price=Decimal("60001"), quantity=Decimal("5")),),
            snapshot_time=_CLOCK,
        )
        model = _make_model()
        order = _make_order(side=OrderSide.SELL, qty_amount=Decimal("1"))
        result = model.fill_order(
            order=order, orderbook=thin_bid_book, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Rejection), (
            f"Expected Rejection (insufficient SELL depth), got {type(result).__name__!r}."
        )
        assert "INSUFFICIENT_DEPTH" in result.reason, (
            f"SELL thin-book rejection must include 'INSUFFICIENT_DEPTH', got {result.reason!r}."
        )

    def test_price_filter_limit_buy_above_best_ask(self) -> None:
        """Limit BUY with limit_price < best_ask → PRICE_FILTER rejection.

        A limit buy at 59000 cannot fill when the best ask is 60001.
        """
        model = _make_model()
        order = _make_order(
            side=OrderSide.BUY,
            qty_amount=Decimal("0.5"),
            limit_price=Decimal("59000"),  # below best ask 60001
        )
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Rejection), (
            f"Expected Rejection (PRICE_FILTER: limit below ask), got {type(result).__name__!r}."
        )
        assert "PRICE_FILTER" in result.reason, (
            f"Limit-buy rejection must contain 'PRICE_FILTER', got {result.reason!r}."
        )

    def test_price_filter_limit_sell_below_best_bid(self) -> None:
        """Limit SELL with limit_price > best_bid → PRICE_FILTER rejection.

        A limit sell at 61000 cannot fill when the best bid is 60000.
        """
        model = _make_model()
        order = _make_order(
            side=OrderSide.SELL,
            qty_amount=Decimal("0.5"),
            limit_price=Decimal("61000"),  # above best bid 60000
        )
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Rejection), (
            f"Expected Rejection (PRICE_FILTER: limit above bid), got {type(result).__name__!r}."
        )
        assert "PRICE_FILTER" in result.reason, (
            f"Limit-sell rejection must contain 'PRICE_FILTER', got {result.reason!r}."
        )

    def test_limit_buy_at_exactly_best_ask_fills(self) -> None:
        """Limit BUY with limit_price == best_ask fills successfully."""
        model = _make_model(apply_slippage=False)
        best_ask = Decimal("60001")
        order = _make_order(
            side=OrderSide.BUY,
            qty_amount=Decimal("0.5"),
            limit_price=best_ask,
        )
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill when limit_price == best_ask {best_ask!r}, "
            f"got {type(result).__name__!r}. At-limit orders must fill."
        )

    def test_limit_sell_at_exactly_best_bid_fills(self) -> None:
        """Limit SELL with limit_price == best_bid fills successfully."""
        model = _make_model(apply_slippage=False)
        best_bid = Decimal("60000")
        order = _make_order(
            side=OrderSide.SELL,
            qty_amount=Decimal("0.5"),
            limit_price=best_bid,
        )
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill when limit_price == best_bid {best_bid!r}, "
            f"got {type(result).__name__!r}. At-limit SELL orders must fill."
        )

    def test_rejection_preserves_original_order(self) -> None:
        """Rejection.order must be the same Order object that was submitted."""
        model = _make_model()
        order = _make_order(
            side=OrderSide.BUY,
            qty_amount=Decimal("0.5"),
            limit_price=Decimal("1"),  # far below market — will reject
        )
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Rejection), (
            f"Expected Rejection for far-below-market limit, got {type(result).__name__!r}."
        )
        assert result.order is order, (
            "Rejection.order must reference the exact Order submitted. "
            "Callers use it to decide whether to retry or alert."
        )

    def test_rejection_reason_is_non_empty(self) -> None:
        """Every Rejection returned by the fill model must have a non-empty reason."""
        model = _make_model()
        order = _make_order(
            side=OrderSide.BUY,
            qty_amount=Decimal("100"),  # more than book depth
        )
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Rejection), (
            f"Expected Rejection (book too thin for 100 BTC), got {type(result).__name__!r}."
        )
        assert result.reason.strip(), (
            "Rejection.reason must be non-empty — an unexplained rejection is not actionable."
        )


# ---------------------------------------------------------------------------
# Clock validation
# ---------------------------------------------------------------------------


class TestClockValidation:
    """fill_order must raise ValueError on naive clock."""

    def test_naive_clock_raises_value_error(self) -> None:
        """Passing a naive datetime as clock must raise ValueError.

        The no-naive-datetime invariant is enforced throughout hermes.
        A naive clock is a misconfiguration at the composition root.
        """
        model = _make_model()
        order = _make_order()
        naive_clock = datetime(2026, 8, 25, 10, 0, 0)  # no tzinfo
        with pytest.raises(ValueError, match="UTC-aware"):
            model.fill_order(
                order=order,
                orderbook=_SINGLE_LEVEL_BOOK,
                clock=naive_clock,
                fees=_STANDARD_SCHEDULE,
            )

    def test_utc_aware_clock_is_accepted(self) -> None:
        """UTC-aware clock is accepted and recorded on the Fill."""
        model = _make_model()
        order = _make_order(qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order, orderbook=_SINGLE_LEVEL_BOOK, clock=_CLOCK, fees=_STANDARD_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill with valid UTC clock, got {type(result).__name__!r}."
        )
        assert result.timestamp == _CLOCK, (
            f"Fill.timestamp {result.timestamp!r} != clock {_CLOCK!r}. "
            "The fill timestamp must match the provided clock."
        )


# ---------------------------------------------------------------------------
# Fill timestamp test
# ---------------------------------------------------------------------------


class TestFillTimestamp:
    """Fill.timestamp must equal the clock passed to fill_order."""

    def test_fill_timestamp_matches_clock(self) -> None:
        """The Fill records the exact clock provided by the caller."""
        custom_clock = datetime(2026, 1, 15, 9, 30, 0, tzinfo=UTC)
        model = _make_model()
        order = _make_order(qty_amount=Decimal("0.5"))
        result = model.fill_order(
            order=order,
            orderbook=_SINGLE_LEVEL_BOOK,
            clock=custom_clock,
            fees=_STANDARD_SCHEDULE,
        )
        assert isinstance(result, Fill), f"Expected Fill, got {type(result).__name__!r}."
        assert result.timestamp == custom_clock, (
            f"Fill.timestamp {result.timestamp!r} != provided clock {custom_clock!r}. "
            "The composition root's clock controls the fill timestamp."
        )


class TestStrategyAttribution:
    """Fills produced by the model inherit the order's strategy."""

    def test_fill_carries_the_strategy_id_of_the_order(self) -> None:
        """OrderbookWalkFillModel returns a Fill whose strategy_id is the order's."""
        order = _make_order()
        result = _make_model().fill_order(order, _DEEP_BOOK, _CLOCK, _STANDARD_SCHEDULE)
        assert isinstance(result, Fill)
        assert result.strategy_id == order.strategy_id
