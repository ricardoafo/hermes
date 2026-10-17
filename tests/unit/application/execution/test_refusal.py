"""Tests for ``refusal_of``, the ledger record of a signal that did not fill (#708).

Invariants protected here:

- an executed or already-filled signal gives no refusal record;
- a risk-halted entry is recorded with the overlay's rule id (``RO-07``), not
  the router's ``HALT`` marker, and a fixed reason code;
- a kill-breaker halt is recorded as rule ``kill``;
- every other decline keeps its rule id and gets that rule's fixed code;
- the record is timed at the signal and sided by its kind (entry BUY, exit
  SELL), with the strategy, pair and venue of the call;
- whatever text a rule's reason carries, the record holds only a code from
  the fixed table, never the text (decision record 2026-10-03, rule 4).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.execution.kernel import Declined, Executed, Halted
from hermes.application.execution.refusal import KILL_RULE, UNCLASSIFIED, refusal_of
from hermes.application.execution.shadow import AlreadyFilled, ShadowOutcome
from hermes.application.order_router.router import KillCircuitBreaker
from hermes.application.strategy_protocol.kill_trigger_evaluator import KillDecision
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.events.trade_rejected import TradeRejected
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_AT = datetime(2024, 1, 3, 3, 59, 59, 999000, tzinfo=UTC)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_STRATEGY = StrategyId("swing-donchian-4h")
_CORRELATION = "01J9Z8T3M5BQR2FVKN4XWY6PH1"
_CODES = {
    "macro_volatility",
    "drawdown_limit",
    "stale_orderbook",
    "kill_criterion",
    "no_book",
    "no_position",
    "fill_model_rejected",
    "portfolio_refused",
    "sizing_failed",
    "max_open_positions",
    "routing_error",
    "invalid_signal",
    "correlation_lock",
    "spread_spike",
    "slippage_limit",
    "stablecoin_depeg_warn",
    "stablecoin_depeg_halt",
    UNCLASSIFIED,
}


def _signal(kind: SignalKind = SignalKind.ENTRY) -> StrategySignal:
    return StrategySignal(
        kind=kind,
        side=SignalSide.LONG,
        entry_price=Decimal("2000"),
        stop_price=Decimal("1900"),
        atr_context=Decimal("20"),
        signal_time=_AT,
        exit_reason="chandelier" if kind is SignalKind.EXIT else None,
    )


def _refusal(outcome: ShadowOutcome, kind: SignalKind = SignalKind.ENTRY) -> TradeRejected | None:
    return refusal_of(
        outcome,
        _signal(kind),
        pair=_ETH,
        strategy_id=_STRATEGY,
        exchange="binance",
        correlation_id=_CORRELATION,
    )


def _trade() -> TradeExecuted:
    return TradeExecuted(
        occurred_at=_AT,
        correlation_id=_CORRELATION,
        pair=_ETH,
        side=TradeSide.BUY,
        qty=Quantity(amount=Decimal("0.1"), asset=Currency.ETH),
        price=Price(amount=Decimal("2000"), pair=_ETH),
        fee=Money(amount=Decimal("0.2"), currency=Currency.USDT),
        strategy_id=_STRATEGY,
    )


def _rule_and_code(event: TradeRejected | None) -> tuple[str | None, str]:
    assert event is not None
    return event.rule, event.rejection_reason


def test_a_filled_signal_gives_no_refusal() -> None:
    """A signal already recorded filled, or filled now, is a trade, never a refusal."""
    order = Order(
        side=OrderSide.BUY,
        pair=_ETH,
        qty=Quantity(amount=Decimal("0.1"), asset=Currency.ETH),
        strategy_id=_STRATEGY,
    )
    fill = Fill(
        order=order,
        executed_qty=Decimal("0.1"),
        avg_price=Decimal("2000"),
        fee=Money(amount=Decimal("0.2"), currency=Currency.USDT),
        timestamp=_AT,
    )
    outcomes = (AlreadyFilled(trade=_trade()), Executed(fill=fill, trade=_trade()))

    assert [_refusal(outcome) for outcome in outcomes] == [None, None]


def test_a_risk_halted_entry_is_recorded_with_the_overlay_rule_id() -> None:
    """RO-07 refusing the entry is recorded as RO-07 / macro_volatility, not HALT."""
    halted = Declined(
        reason="RO-07: BTC 1D log-return -0.1234 < -0.10 — halting new entries", rule="HALT"
    )
    assert _rule_and_code(_refusal(halted)) == ("RO-07", "macro_volatility")


def test_a_halt_naming_no_known_rule_is_recorded_as_unclassified() -> None:
    """A HALT whose reason names no overlay rule keeps HALT with the fixed unclassified code."""
    halted = Declined(reason="halted for a reason no table lists", rule="HALT")
    assert _rule_and_code(_refusal(halted)) == ("HALT", UNCLASSIFIED)


def test_a_kill_breaker_halt_is_recorded_as_rule_kill() -> None:
    """The §10 kill breaker is recorded as kill / kill_criterion, without its reason text."""
    breaker = KillCircuitBreaker(
        decision=KillDecision(triggered=True, reason="max drawdown 16.2% >= 15%")
    )
    assert _rule_and_code(_refusal(Halted(breaker=breaker))) == (KILL_RULE, "kill_criterion")


@pytest.mark.parametrize(
    ("rule", "code"),
    [
        ("no-book", "no_book"),
        ("no-position", "no_position"),
        ("fill_model", "fill_model_rejected"),
        ("portfolio", "portfolio_refused"),
        ("sizing", "sizing_failed"),
        ("orphan-position", "max_open_positions"),
        ("routing", "routing_error"),
        ("signal", "invalid_signal"),
        ("something-new", UNCLASSIFIED),
    ],
)
def test_a_declined_signal_keeps_its_rule_with_the_fixed_code(rule: str, code: str) -> None:
    """Each decline rule maps to its own code; an unknown rule maps to unclassified."""
    declined = Declined(reason="free text 123.45", rule=rule)
    assert _rule_and_code(_refusal(declined)) == (rule, code)


def test_a_refused_entry_record_carries_the_signal_facts() -> None:
    """The record is the BUY of an entry, at the signal time, for the call's pair and venue."""
    event = _refusal(Declined(reason="no order book", rule="no-book"))
    assert event == TradeRejected(
        occurred_at=_AT,
        correlation_id=_CORRELATION,
        pair=_ETH,
        side=TradeSide.BUY,
        rejection_reason="no_book",
        exchange="binance",
        attempted_at=_AT,
        strategy_id=_STRATEGY,
        signal_kind=SignalKind.ENTRY,
        rule="no-book",
    )


def test_a_refused_exit_is_recorded_as_a_sell() -> None:
    """An exit not filled (#706 keeps the coin) is recorded as the SELL that did not happen."""
    event = _refusal(Declined(reason="no order book", rule="no-book"), SignalKind.EXIT)
    assert event is not None
    assert (event.side, event.signal_kind) == (TradeSide.SELL, SignalKind.EXIT)


@given(
    reason=st.text(min_size=1).filter(str.strip),
    rule=st.sampled_from(["HALT", "no-book", "fill_model", "sizing", "portfolio", "x"]),
)
def test_the_reason_code_never_carries_the_rule_text(reason: str, rule: str) -> None:
    """Whatever a rule's reason says, the record keeps a code from the fixed table only."""
    _, code = _rule_and_code(_refusal(Declined(reason=reason, rule=rule)))
    assert code in _CODES
