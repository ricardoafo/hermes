"""Guards that the backtest and shadow sides of M-09 count fills the same way (issue #1179).

M-09 compares the shadow's round trips with its replay's. The backtest's
performance file and the shadow window used to carry a round-trip loop each,
and the same Pearson, round-trip and severity rules were written several
times over, so the copies could drift apart unseen.

Invariants:

- for any valid fill sequence, the performance file's trades and the shadow
  window's round trips are the same trips: same count, and each net P&L is
  the performance trade's gross minus its fees (property);
- no module under ``src/hermes`` outside its one home computes a Pearson
  coefficient, runs an average-cost round-trip loop or keeps a severity table
  for ``RiskAction`` (the guard against the copies coming back).
"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.backtest.performance import round_trips as performance_trips
from hermes.application.calibration.shadow_window import round_trips as shadow_trips
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from tests.support.fill_kernel import STRATEGY

_PAIRS = (
    Pair(base=Currency.BTC, quote=Currency.USDT),
    Pair(base=Currency.ETH, quote=Currency.USDT),
)
_T0 = datetime(2026, 10, 3, tzinfo=UTC)
_SRC = Path(__file__).resolve().parents[4] / "src" / "hermes"

type _Op = tuple[Pair, OrderSide, Decimal, Decimal, Decimal]

_amount = st.decimals(min_value=Decimal("0.01"), max_value=Decimal(1000), places=8)
_fee = st.decimals(min_value=Decimal(0), max_value=Decimal(5), places=8)


@st.composite
def _valid_ops(draw: st.DrawFn) -> list[_Op]:
    """Buys and sells in two pairs, each sell at most what its pair holds."""
    held = dict.fromkeys(_PAIRS, Decimal(0))
    ops: list[_Op] = []
    for _ in range(draw(st.integers(min_value=0, max_value=20))):
        pair = draw(st.sampled_from(_PAIRS))
        price, fee = draw(_amount), draw(_fee)
        if held[pair] > 0 and draw(st.booleans()):
            qty = held[pair] if draw(st.booleans()) else min(draw(_amount), held[pair])
            held[pair] -= qty
            ops.append((pair, OrderSide.SELL, qty, price, fee))
            continue
        qty = draw(_amount)
        held[pair] += qty
        ops.append((pair, OrderSide.BUY, qty, price, fee))
    return ops


def _as_fill(i: int, op: _Op) -> Fill:
    pair, side, qty, price, fee = op
    order = Order(
        side=side, pair=pair, qty=Quantity(amount=qty, asset=pair.base), strategy_id=STRATEGY
    )
    return Fill(
        order=order,
        executed_qty=qty,
        avg_price=price,
        fee=Money(fee, pair.quote),
        timestamp=_T0 + timedelta(hours=i),
    )


def _as_trade(i: int, op: _Op) -> TradeExecuted:
    pair, side, qty, price, fee = op
    return TradeExecuted(
        occurred_at=_T0 + timedelta(hours=i),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        pair=pair,
        side=TradeSide(side.value),
        qty=Quantity(amount=qty, asset=pair.base),
        price=Price(amount=price, pair=pair),
        fee=Money(fee, pair.quote),
        strategy_id=STRATEGY,
    )


@given(ops=_valid_ops())
def test_both_sides_count_the_same_round_trips(ops: list[_Op]) -> None:
    """The performance file and the shadow window see the same trips with the same net P&L."""
    backtest = performance_trips(_as_fill(i, op) for i, op in enumerate(ops))
    shadow = shadow_trips(_as_trade(i, op) for i, op in enumerate(ops))

    assert [float(t.net_pnl) for t in shadow] == pytest.approx(
        [t.gross_pnl - t.fees for t in backtest], rel=1e-12, abs=1e-9
    )


def _sources() -> dict[str, str]:
    return {
        str(path.relative_to(_SRC)): path.read_text(encoding="utf-8")
        for path in sorted(_SRC.rglob("*.py"))
    }


@pytest.mark.parametrize(
    ("pattern", "home"),
    [
        (r"statistics\.correlation\(|def _?pearson\(", {"domain/statistics/correlation.py"}),
        (r"\bsold_cost\b", {"domain/portfolio/round_trips.py"}),
        (r"RiskAction\.(OK|WARN|HALT)\s*:\s*\d", set()),
    ],
    ids=["pearson", "round-trip-loop", "severity-table"],
)
def test_each_rule_has_one_copy(pattern: str, home: set[str]) -> None:
    """Pearson, the round-trip loop and the severity order live in one module each."""
    found = {name for name, text in _sources().items() if re.search(pattern, text)}

    assert found ^ home == set()
