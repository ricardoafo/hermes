"""Shadow-test safety: EUR recording never changes what the strategy does.

Runs the scripted serve market (one ENTRY, one EXIT) through the swing
strategy, the sizing calculator, the simulated exchange (fees included) and
the portfolio tracker, recording every fill with :class:`TradeRecorder`. The
run happens twice: once with the FX provider answering, once with it down.
Every signal, sizing result, fill, balance and portfolio state must be
byte-identical; only the ledger's EUR columns may differ.

Reference: issue #386 "Shadow-test safety", change ``trade-eur-values`` R-5,
ADR-016 item 9.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.exchanges.simulated import SimulatedExchangeAdapter
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.application.ledger.trade_recorder import TradeRecorder
from hermes.application.order_router.sizing import SizingCalculator, SizingInput
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.support.serve_loop import BTC, H4_BASE, FakeClock, fresh_strategy, history
from tests.unit.application.ledger.fake_fx import FakeFx

pytestmark = pytest.mark.integration

_STRATEGY = StrategyId("swing")
_CAPITAL = Decimal("10000")
_FEES = FeeSchedule.from_tiers(
    pair=BTC,
    tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=10, taker_bps=10),),
)


def _book(pair: Pair, price: Decimal, clock: FakeClock) -> Orderbook:
    return Orderbook(
        pair=pair,
        bids=(OrderbookLevel(price=price - 1, quantity=Decimal("1000")),),
        asks=(OrderbookLevel(price=price, quantity=Decimal("1000")),),
        snapshot_time=clock.now(),
    )


def _run(fx: FakeFx) -> tuple[list[str], sqlite3.Connection]:
    """Drive the scripted market; return the behaviour trace and the ledger connection."""
    clock = FakeClock(H4_BASE)
    last_close: dict[str, Decimal] = {"px": Decimal("1000")}
    exchange = SimulatedExchangeAdapter(
        fill_model=OrderbookWalkFillModel(apply_slippage=False),
        orderbook_fn=lambda pair: _book(pair, last_close["px"], clock),
        clock=clock,
        fee_schedule_fn=lambda _pair: _FEES,
        initial_balances={Currency.USDT: _CAPITAL},
    )
    tracker = PortfolioTracker(
        PortfolioSnapshot.seed(_STRATEGY, Money(amount=_CAPITAL, currency=Currency.USDT))
    )
    strategy = fresh_strategy()
    conn = sqlite3.connect(":memory:")
    recorder = TradeRecorder(SqliteLedger(conn), EurPricer(fx))

    trace: list[str] = []
    for bar in history(14):
        tracker.mark(bar)
        signal = strategy.on_bar(bar, tracker.state())
        trace.append(repr(signal))
        if signal is None:
            continue
        clock.current = bar.close_time
        last_close["px"] = bar.close
        if signal.kind == SignalKind.ENTRY:
            assert signal.entry_price is not None
            sizing = SizingCalculator().compute(
                SizingInput(
                    equity=_CAPITAL,
                    entry_px=signal.entry_price,
                    atr=signal.atr_context,
                    stop_atr_mult=Decimal("1.5"),
                    risk_pct=Decimal("0.01"),
                    f_star_estimate=Decimal("0.20"),
                )
            )
            trace.append(repr(sizing))
            side, qty = OrderSide.BUY, sizing.unwrap().qty
        else:
            side, qty = OrderSide.SELL, exchange.get_balance(currency=Currency.BTC).amount
        fill = exchange.place_market_order(
            side=side,
            order=Order(
                side=side,
                pair=BTC,
                qty=Quantity(amount=qty, asset=Currency.BTC),
                strategy_id=_STRATEGY,
            ),
        )
        trace.append(repr(fill))
        trace.append(repr(tracker.apply_fill(fill)))
        recorder.record(fill, correlation_id=f"c-{len(trace)}")
        trace.append(repr(exchange.get_balance(currency=Currency.USDT)))
        trace.append(repr(exchange.get_balance(currency=Currency.BTC)))
    trace.append(repr(tracker.state()))
    return trace, conn


def _fx_up() -> FakeFx:
    """Answer every date of the scripted market, each resolved to itself (final)."""
    start = H4_BASE.date() - timedelta(days=20)
    days = [date.fromordinal(start.toordinal() + i) for i in range(40)]
    return FakeFx(rates={day: (Decimal("1.0912"), day) for day in days})


def test_strategy_sizing_and_fills_are_identical_with_and_without_fx() -> None:
    """Invariant (A-5.1): FX availability changes only ledger EUR columns, never behaviour."""
    with_fx, ledger_with = _run(_fx_up())
    without_fx, ledger_without = _run(FakeFx(down=True))

    assert "\n".join(with_fx).encode() == "\n".join(without_fx).encode()
    statuses = (
        ledger_with.execute("SELECT side, eur_status FROM trades ORDER BY id").fetchall(),
        ledger_without.execute("SELECT side, eur_status FROM trades ORDER BY id").fetchall(),
    )
    assert statuses == (
        [("BUY", "ok"), ("SELL", "ok")],
        [("BUY", "pending"), ("SELL", "pending")],
    )
