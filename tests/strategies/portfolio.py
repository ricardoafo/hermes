"""Builders and Hypothesis strategies for portfolio accounting tests.

``make_fill`` and ``make_bar`` build the domain values the tracker consumes
without a venue or a clock. ``operations`` draws a list of fills and bars to
fold into a snapshot; amounts are bounded (quantity 4 places, price 2 places,
fee 8 places) so every sum stays exact and the accounting identities can be
asserted with ``==``.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_STRATEGY_ID = StrategyId("test-strategy")

BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)
BTC_USDC = Pair(base=Currency.BTC, quote=Currency.USDC)
T0 = datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)

_FEE_QUANTUM = Decimal("1e-8")


def at(minutes: int) -> datetime:
    """Return ``T0`` plus ``minutes`` — a UTC-aware time for fills and bars."""
    return T0 + timedelta(minutes=minutes)


def fee_for(notional: Decimal, bps: int) -> Decimal:
    """Return a quote-denominated fee of ``bps`` basis points, rounded to 8 places."""
    return (notional * Decimal(bps) / Decimal(10_000)).quantize(_FEE_QUANTUM)


def make_fill(
    side: OrderSide,
    qty: str | Decimal,
    price: str | Decimal,
    *,
    fee: str | Decimal = "0",
    pair: Pair = BTC_USDT,
    minutes: int = 0,
    fee_currency: Currency | None = None,
) -> Fill:
    """Build a fully executed market fill of ``qty`` at ``price``.

    Args:
        side: BUY or SELL.
        qty: Base quantity executed.
        price: Average execution price in the quote currency.
        fee: Fee amount, in ``fee_currency`` (default: the pair quote).
        pair: The pair traded.
        minutes: Fill time as minutes after ``T0``.
        fee_currency: Override the fee currency to build a mismatched fill.

    Returns:
        A :class:`Fill` whose executed quantity equals the order quantity.

    """
    quantity = Decimal(qty)
    order = Order(
        side=side,
        pair=pair,
        qty=Quantity(amount=quantity, asset=pair.base),
        strategy_id=_STRATEGY_ID,
    )
    return Fill(
        order=order,
        executed_qty=quantity,
        avg_price=Decimal(price),
        fee=Money(amount=Decimal(fee), currency=fee_currency or pair.quote),
        timestamp=at(minutes),
    )


def make_bar(pair: Pair, close: str | Decimal, *, minutes: int) -> Bar:
    """Build a closed 4h bar for ``pair`` closing at ``close``, ``minutes`` after ``T0``."""
    return make_bar_at(pair, close, at(minutes))


def make_bar_at(pair: Pair, close: str | Decimal, close_time: datetime) -> Bar:
    """Build a closed 4h bar for ``pair`` closing at ``close`` when the bar closes."""
    price = Decimal(close)
    return Bar(
        pair=pair,
        interval=KlineInterval.H4,
        open_time=close_time - timedelta(hours=4),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
        close_time=close_time,
    )


@dataclass(frozen=True, slots=True)
class FillOp:
    """A fill to apply to a snapshot."""

    fill: Fill


@dataclass(frozen=True, slots=True)
class BarOp:
    """A closed bar to mark a snapshot with."""

    bar: Bar


type Operation = FillOp | BarOp

_PAIRS = st.sampled_from([BTC_USDT, BTC_USDT, BTC_USDT, ETH_USDT])
_QUANTITIES = st.one_of(
    st.sampled_from([Decimal("0.001"), Decimal("0.01"), Decimal("0.5"), Decimal("1")]),
    st.decimals(
        min_value=Decimal("0.0001"),
        max_value=Decimal("5"),
        places=4,
        allow_nan=False,
        allow_infinity=False,
    ),
)
_PRICES = st.decimals(
    min_value=Decimal("1"),
    max_value=Decimal("100000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_FEE_BPS = st.integers(min_value=0, max_value=100)
_MINUTES = st.integers(min_value=0, max_value=100_000)
_QTY_QUANTUM = Decimal("0.0001")

starting_cash = st.decimals(
    min_value=Decimal("1000"),
    max_value=Decimal("5000000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def operations(draw: st.DrawFn) -> list[Operation]:
    """Draw up to 25 fills and bars to fold, in order, into a snapshot.

    Times mostly increase with the index (a bar loop is chronological) and
    occasionally jump anywhere, so stale bars occur. SELL quantities are drawn
    against the quantity the earlier BUYs would hold, so full closes, partial
    sells, oversells and unheld sells all occur.
    """
    held: dict[Pair, Decimal] = {}
    ops: list[Operation] = []
    for index in range(draw(st.integers(min_value=0, max_value=25))):
        minutes = draw(_MINUTES) if draw(st.integers(0, 3)) == 0 else (index + 1) * 60
        kind = draw(st.sampled_from(["buy", "buy", "sell", "sell", "bar", "bar"]))
        pair = draw(_PAIRS)
        price = draw(_PRICES)
        if kind == "bar":
            ops.append(BarOp(make_bar(pair, price, minutes=minutes)))
            continue
        current = held.get(pair, Decimal(0))
        if kind == "buy":
            qty = draw(_QUANTITIES)
            held[pair] = current + qty
            side = OrderSide.BUY
        else:
            half = (current / 2).quantize(_QTY_QUANTUM)
            qty = draw(
                st.sampled_from(
                    [
                        current if current > 0 else Decimal("0.01"),
                        half if half >= _QTY_QUANTUM else Decimal("0.01"),
                        current + _QTY_QUANTUM,
                    ]
                )
                | _QUANTITIES
            )
            held[pair] = current - qty if qty <= current else current
            side = OrderSide.SELL
        fee = fee_for(qty * price, draw(_FEE_BPS))
        ops.append(FillOp(make_fill(side, qty, price, fee=fee, pair=pair, minutes=minutes)))
    return ops
