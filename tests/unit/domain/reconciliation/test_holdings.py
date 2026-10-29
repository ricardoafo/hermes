"""Tests for the holdings comparison (change 35-monthly-reconciliation D-1, D-2).

Invariants protected here:

- the book folds every fill: BUY adds base and spends quote, SELL the reverse,
  every fee leaves the currency it was charged in;
- a difference within the tolerance is a match, anything larger is drift;
- assets zero on both sides are left out, missing assets read zero.
"""

from datetime import UTC, datetime
from decimal import Decimal, localcontext

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from hermes.domain.reconciliation.holdings import (
    CheckStatus,
    LedgerTrade,
    book_balances,
    compare_holdings,
)
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.precision import EXACT_CONTEXT

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_T0 = datetime(2026, 9, 3, 10, tzinfo=UTC)
_TOL = Decimal("1e-8")


def _trade(
    side: OrderSide, qty: str, price: str, fee: str, fee_ccy: Currency = Currency.USDT
) -> LedgerTrade:
    return LedgerTrade(
        trade_id=1,
        pair=_BTC,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Money(amount=Decimal(fee), currency=fee_ccy),
        executed_at=_T0,
        price_eur=None,
        fee_eur=None,
    )


def test_a_buy_adds_base_and_spends_notional_plus_fee() -> None:
    """5000 USDT, BUY 0.01 at 60000 with a 0.6 fee: 0.01 BTC and 4399.4 USDT."""
    balances = book_balances(
        [_trade(OrderSide.BUY, "0.01", "60000", "0.6")], {Currency.USDT: Decimal("5000")}
    )

    assert balances == {Currency.USDT: Decimal("4399.4"), Currency.BTC: Decimal("0.01")}


def test_a_sell_returns_proceeds_net_of_fee() -> None:
    """SELL 0.004 at 65000 with a 0.26 fee adds 259.74 USDT and removes 0.004 BTC."""
    balances = book_balances(
        [_trade(OrderSide.SELL, "0.004", "65000", "0.26")],
        {Currency.USDT: Decimal("0"), Currency.BTC: Decimal("0.004")},
    )

    assert balances == {Currency.USDT: Decimal("259.74"), Currency.BTC: Decimal("0")}


def test_a_fee_in_the_base_asset_leaves_the_base_balance() -> None:
    """A fee charged in BTC reduces BTC, not USDT."""
    balances = book_balances([_trade(OrderSide.BUY, "1", "100", "0.001", Currency.BTC)], {})

    assert balances == {Currency.BTC: Decimal("0.999"), Currency.USDT: Decimal("-100")}


def test_ledger_trade_rejects_half_priced_eur_values() -> None:
    """A row with a EUR price but no EUR fee is corrupt, not pending."""
    with pytest.raises(ValueError, match="both set or both pending"):
        LedgerTrade(
            trade_id=1,
            pair=_BTC,
            side=OrderSide.BUY,
            qty=Decimal(1),
            price=Decimal(1),
            fee=Money(amount=Decimal(0), currency=Currency.USDT),
            executed_at=_T0,
            price_eur=Decimal(1),
            fee_eur=None,
        )


def test_difference_at_the_tolerance_is_a_match() -> None:
    """|drift| == tolerance still matches; the bound is inclusive."""
    (check,) = compare_holdings(
        {Currency.BTC: Decimal("1")},
        {Currency.BTC: Decimal("1.00000001")},
        source="events",
        tolerance=_TOL,
    )

    assert (check.status, check.drift) == (CheckStatus.MATCH, Decimal("0.00000001"))


def test_difference_above_the_tolerance_is_drift() -> None:
    """A view holding 0.1 BTC less than the book drifts by -0.1."""
    (check,) = compare_holdings(
        {Currency.BTC: Decimal("0.5")},
        {Currency.BTC: Decimal("0.4")},
        source="lots",
        tolerance=_TOL,
    )

    assert (check.source, check.status, check.drift) == ("lots", CheckStatus.DRIFT, Decimal("-0.1"))


def test_an_asset_missing_from_one_side_reads_zero() -> None:
    """A coin only the other view holds is drift of its full amount."""
    (check,) = compare_holdings({}, {Currency.ETH: Decimal("2")}, source="x", tolerance=_TOL)

    assert (check.asset, check.book, check.drift) == (Currency.ETH, Decimal(0), Decimal(2))


def test_assets_zero_on_both_sides_are_left_out() -> None:
    """A coin fully sold long ago does not pad the report."""
    checks = compare_holdings(
        {Currency.SOL: Decimal(0), Currency.BTC: Decimal(1)},
        {Currency.BTC: Decimal(1)},
        source="x",
        tolerance=_TOL,
    )

    assert [c.asset for c in checks] == [Currency.BTC]


def test_assets_restricts_the_comparison() -> None:
    """With ``assets`` only those assets are compared (the lot table holds no cash)."""
    checks = compare_holdings(
        {Currency.BTC: Decimal(1), Currency.USDT: Decimal(100)},
        {Currency.BTC: Decimal(1)},
        source="lots",
        tolerance=_TOL,
        assets={Currency.BTC},
    )

    assert [c.asset for c in checks] == [Currency.BTC]


def test_negative_tolerance_is_refused() -> None:
    """A negative tolerance would flag every exact match as drift."""
    with pytest.raises(ValueError, match="tolerance"):
        compare_holdings({}, {}, source="x", tolerance=Decimal("-1"))


_amounts = st.decimals(min_value=0, max_value=10**6, places=8, allow_nan=False)


@given(
    trades=st.lists(
        st.tuples(st.sampled_from(list(OrderSide)), _amounts.filter(bool), _amounts, _amounts),
        max_size=12,
    )
)
# #585: 1E12 + 1E-16 needs 29 digits; the default 28-digit context rounds it.
@example(
    trades=[
        (OrderSide.BUY, Decimal("1E-8"), Decimal("1E-8"), Decimal(0)),
        (OrderSide.BUY, Decimal("1000000"), Decimal("1000000"), Decimal(0)),
    ]
)
def test_book_conserves_value_at_trade_prices(
    trades: list[tuple[OrderSide, Decimal, Decimal, Decimal]],
) -> None:
    """Property: quote + base * price changes by exactly minus the fees, per fill.

    Valuing each fill's base flow at its own price, the only value that leaves
    the book is the fee. The check runs in the book's own exact context: the
    default 28-digit context would round the expected side, not the book (#585).
    """
    rows = [_trade(side, str(qty), str(price), str(fee)) for side, qty, price, fee in trades]
    balances = book_balances(rows, {})
    with localcontext(EXACT_CONTEXT):
        quote_flow = balances.get(Currency.USDT, Decimal(0))
        base_value = sum(
            ((1 if r.side == OrderSide.BUY else -1) * r.qty * r.price for r in rows), Decimal(0)
        )
        fees = sum((r.fee.amount for r in rows), Decimal(0))

        assert quote_flow + base_value == -fees


def test_book_keeps_a_tiny_flow_next_to_a_huge_one() -> None:
    """A 1E-16 USDT flow beside a 1E12 one survives: the book does not round (#585)."""
    balances = book_balances(
        [
            _trade(OrderSide.BUY, "1E-8", "1E-8", "0"),
            _trade(OrderSide.BUY, "1000000", "1000000", "0"),
        ],
        {},
    )

    assert balances[Currency.USDT] == Decimal("-1000000000000.0000000000000001")


@given(book=_amounts, observed=_amounts)
def test_drift_is_observed_minus_book(book: Decimal, observed: Decimal) -> None:
    """Property: drift sign follows the view, and status agrees with the tolerance."""
    checks = compare_holdings(
        {Currency.BTC: book}, {Currency.BTC: observed}, source="x", tolerance=_TOL
    )
    if book == 0 and observed == 0:
        assert checks == ()
        return
    (check,) = checks
    assert check.drift == observed - book
    assert (check.status is CheckStatus.MATCH) == (abs(observed - book) <= _TOL)
