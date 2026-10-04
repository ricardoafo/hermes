"""One sample of every notifiable event, shared by the notification tests.

The samples are fixed (time, ids, amounts) so snapshots are stable, and their
free-text fields never contain an outcome word, so the blind scan can reject
any P&L word that a template adds on its own.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest, TradeSummary
from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.events.strategy_resumed import StrategyResumed
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.events.trade_rejected import TradeRejected
from hermes.domain.strategy.signal import SignalKind, SignalSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.domain.value_objects.weight import Weight

__all__ = ["BTC_USDT", "CORRELATION_ID", "NOON_SATURDAY_UTC", "STRATEGY", "samples"]

NOON_SATURDAY_UTC = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
"""Saturday 14:00 in Madrid (CEST, UTC+2)."""
CORRELATION_ID = "01J9Z8T3M5BQR2FVKN4YK7Q2ZP"
BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
STRATEGY = StrategyId("swing-donchian-4h")


def _entry() -> SignalEmitted:
    return SignalEmitted(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        strategy_id=STRATEGY,
        pair=BTC_USDT,
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=Decimal("52410.2"),
        stop_price=Decimal("50516"),
        atr_context=Decimal("1052"),
    )


def _exit() -> SignalEmitted:
    return SignalEmitted(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        strategy_id=STRATEGY,
        pair=BTC_USDT,
        kind=SignalKind.EXIT,
        side=SignalSide.LONG,
        entry_price=Decimal("52410.2"),
        stop_price=Decimal("54100"),
        atr_context=Decimal("1052"),
        exit_reason="trailing_stop",
    )


def _fill() -> TradeExecuted:
    return TradeExecuted(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        pair=BTC_USDT,
        side=TradeSide.BUY,
        qty=Quantity(amount=Decimal("0.004"), asset=Currency.BTC),
        price=Price(amount=Decimal("52410.2"), pair=BTC_USDT),
        fee=Money(amount=Decimal("0.21"), currency=Currency.USDT),
        strategy_id=STRATEGY,
    )


def _rejected() -> TradeRejected:
    return TradeRejected(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        pair=BTC_USDT,
        side=TradeSide.BUY,
        rejection_reason="insufficient balance",
        exchange="binance",
        attempted_at=NOON_SATURDAY_UTC,
    )


def _paused(severity: AnomalySeverity) -> StrategyPaused:
    return StrategyPaused(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        strategy_id="swing-donchian-4h",
        reason="operator kill switch",
        paused_at=NOON_SATURDAY_UTC,
        severity=severity,
    )


def _resumed() -> StrategyResumed:
    return StrategyResumed(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        strategy_id="swing-donchian-4h",
        resumed_at=NOON_SATURDAY_UTC,
        pause_duration_seconds=900,
    )


def _anomaly(severity: AnomalySeverity) -> AnomalyDetected:
    return AnomalyDetected(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        source="reconciler",
        severity=severity,
        reason="ledger and exchange balances disagree",
    )


def _balance_low() -> BalanceLow:
    return BalanceLow(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        asset=Currency.USDT,
        current=Money(amount=Decimal("50"), currency=Currency.USDT),
        threshold=Money(amount=Decimal("100"), currency=Currency.USDT),
        checked_at=NOON_SATURDAY_UTC,
    )


def _rebalance() -> RebalanceTriggered:
    return RebalanceTriggered(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        pair=BTC_USDT,
        current_weight=Weight(value=Decimal("0.35")),
        target_weight=Weight(value=Decimal("0.25")),
        band=Weight(value=Decimal("0.05")),
    )


def _digest() -> DailyTradeDigest:
    return DailyTradeDigest(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        day=date(2026, 10, 3),
        total_trades=12,
        filled=10,
        rejected=2,
        total_notional=Money(amount=Decimal("12500.50"), currency=Currency.EUR),
        top_winners=(
            TradeSummary(pair=BTC_USDT, pnl=Money(amount=Decimal("120.00"), currency=Currency.EUR)),
        ),
        top_losers=(
            TradeSummary(
                pair=Pair(base=Currency.ETH, quote=Currency.USDT),
                pnl=Money(amount=Decimal("-45.50"), currency=Currency.EUR),
            ),
        ),
    )


def samples() -> dict[str, DomainEvent]:
    """Return one fixed sample per notifiable event, keyed by a snapshot-friendly name.

    Returns:
        Entry and exit signals, fill, rejection, pause (critical and
        warning), resume, anomaly (critical and info), low balance,
        rebalance heads-up and daily digest.

    """
    return {
        "signal-entry": _entry(),
        "signal-exit": _exit(),
        "position-large": _fill(),
        "trade-rejected": _rejected(),
        "strategy-paused-critical": _paused(AnomalySeverity.CRITICAL),
        "strategy-paused-warning": _paused(AnomalySeverity.WARNING),
        "strategy-resumed": _resumed(),
        "anomaly-critical": _anomaly(AnomalySeverity.CRITICAL),
        "anomaly-info": _anomaly(AnomalySeverity.INFO),
        "balance-low": _balance_low(),
        "rebalance-triggered": _rebalance(),
        "daily-digest": _digest(),
    }
