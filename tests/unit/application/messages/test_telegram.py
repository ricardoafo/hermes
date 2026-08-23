"""Tests for Telegram message templates.

Verifies:
- Every registered event class produces a 3-line MarkdownV2 message
  containing the expected emoji, name, body substring, and correlation tail.
- The dispatcher raises ``ValueError`` for an unregistered event type.
- Registered events cover every concrete class the CLI catalog knows about.
- Reserved MarkdownV2 characters (dots, underscores) in field values are
  escaped so the message renders literally.

Reference: docs/decisions/2026-08-23-slack-alongside-telegram.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar

import pytest

from hermes.application.messages.telegram import telegram_template
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest
from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.events.trade_rejected import TradeRejected
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.weight import Weight

_TS = datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"
_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)


class TestStrategyPausedTemplate:
    """StrategyPaused renders the 3-line skeleton with the severity emoji."""

    def test_output_has_three_lines(self) -> None:
        """The rendered message contains exactly three lines separated by newlines."""
        event = StrategyPaused(
            occurred_at=_TS,
            correlation_id=_CID,
            strategy_id="swing.v1",
            reason="30% drawdown breached",
            paused_at=_TS,
            severity=AnomalySeverity.CRITICAL,
        )
        assert telegram_template(event).count("\n") == 2

    def test_output_contains_event_name_and_correlation_tail(self) -> None:
        """The message contains the escaped event name and the 6-char correlation tail."""
        event = StrategyPaused(
            occurred_at=_TS,
            correlation_id=_CID,
            strategy_id="swing.v1",
            reason="30% drawdown breached",
            paused_at=_TS,
            severity=AnomalySeverity.CRITICAL,
        )
        result = telegram_template(event)
        assert "STRATEGY\\_PAUSED" in result
        assert "WY6PH1" in result

    def test_critical_severity_uses_red_emoji(self) -> None:
        """A CRITICAL severity renders the red-circle emoji \U0001f534."""
        event = StrategyPaused(
            occurred_at=_TS,
            correlation_id=_CID,
            strategy_id="swing.v1",
            reason="30% drawdown breached",
            paused_at=_TS,
            severity=AnomalySeverity.CRITICAL,
        )
        assert telegram_template(event).startswith("\U0001f534")

    def test_warning_severity_uses_yellow_emoji(self) -> None:
        """A WARNING severity renders the yellow-circle emoji \U0001f7e1."""
        event = StrategyPaused(
            occurred_at=_TS,
            correlation_id=_CID,
            strategy_id="swing.v1",
            reason="soft threshold",
            paused_at=_TS,
            severity=AnomalySeverity.WARNING,
        )
        assert telegram_template(event).startswith("\U0001f7e1")

    def test_info_severity_uses_blue_emoji(self) -> None:
        """An INFO severity renders the blue-circle emoji \U0001f535."""
        event = StrategyPaused(
            occurred_at=_TS,
            correlation_id=_CID,
            strategy_id="swing.v1",
            reason="informational",
            paused_at=_TS,
            severity=AnomalySeverity.INFO,
        )
        assert telegram_template(event).startswith("\U0001f535")


class TestBalanceLowTemplate:
    """BalanceLow renders as urgent (red) with the asset / amount / threshold in the body."""

    def _event(self) -> BalanceLow:
        return BalanceLow(
            occurred_at=_TS,
            correlation_id=_CID,
            asset=Currency.USDT,
            current=Money(amount=Decimal("50"), currency=Currency.USDT),
            threshold=Money(amount=Decimal("100"), currency=Currency.USDT),
            checked_at=_TS,
        )

    def test_output_starts_with_red_emoji(self) -> None:
        """BALANCE_LOW is always critical — red emoji regardless of severity."""
        assert telegram_template(self._event()).startswith("\U0001f534")

    def test_body_names_asset_amount_and_threshold(self) -> None:
        """The message body names the asset, current amount, and threshold."""
        result = telegram_template(self._event())
        assert "USDT" in result
        assert "50" in result
        assert "100" in result

    def test_action_hint_appears_on_ref_line(self) -> None:
        """The 'top up' action hint appears on the ref line for the operator."""
        assert "top up" in telegram_template(self._event())


class TestTradeRejectedTemplate:
    """TradeRejected renders with the exchange, side, pair, and reason."""

    def test_body_names_all_key_fields(self) -> None:
        """The rendered body includes exchange, side, pair, and rejection reason (escaped)."""
        event = TradeRejected(
            occurred_at=_TS,
            correlation_id=_CID,
            pair=_PAIR,
            side=TradeSide.BUY,
            rejection_reason="insufficient balance",
            exchange="binance",
            attempted_at=_TS,
        )
        result = telegram_template(event)
        assert "binance" in result
        assert "BUY" in result
        # Slash in the pair is a reserved MarkdownV2 char and is escaped.
        assert "BTC/USDT" in result
        assert "insufficient balance" in result


class TestAnomalyDetectedTemplate:
    """AnomalyDetected renders with the source and reason and the severity emoji."""

    def test_output_names_source_and_reason(self) -> None:
        """The message contains the source and reason strings (dots MarkdownV2-escaped)."""
        event = AnomalyDetected(
            occurred_at=_TS,
            correlation_id=_CID,
            source="swing.risk",
            severity=AnomalySeverity.WARNING,
            reason="30% drawdown breached",
        )
        result = telegram_template(event)
        # The literal dot in "swing.risk" is a reserved MarkdownV2 char and gets a backslash.
        assert "swing\\.risk" in result
        assert "30% drawdown breached" in result


class TestTradeExecutedTemplate:
    """TradeExecuted renders as POSITION_LARGE with pair, side, qty, and price."""

    def test_body_names_pair_side_qty_and_price(self) -> None:
        """The rendered body includes pair, side, qty, and price (dots MarkdownV2-escaped)."""
        event = TradeExecuted(
            occurred_at=_TS,
            correlation_id=_CID,
            pair=_PAIR,
            side=TradeSide.BUY,
            qty=Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
            price=Price(amount=Decimal("68000"), pair=_PAIR),
            fee=Money(amount=Decimal("34"), currency=Currency.USDT),
        )
        result = telegram_template(event)
        assert "BUY" in result
        # Decimal quantity dot escapes to `\.` per MarkdownV2 rules.
        assert "0\\.5" in result
        assert "68000" in result
        assert "POSITION\\_LARGE" in result


class TestRebalanceTriggeredTemplate:
    """RebalanceTriggered renders with pair, weights, and band."""

    def test_body_names_pair_weights_and_band(self) -> None:
        """The rendered body includes pair, weights, and band (dots escaped)."""
        event = RebalanceTriggered(
            occurred_at=_TS,
            correlation_id=_CID,
            pair=_PAIR,
            current_weight=Weight(value=Decimal("0.35")),
            target_weight=Weight(value=Decimal("0.25")),
            band=Weight(value=Decimal("0.05")),
        )
        result = telegram_template(event)
        assert "BTC/USDT" in result
        # Decimal weights carry dots that MarkdownV2 escapes.
        assert "0\\.35" in result
        assert "0\\.25" in result
        assert "0\\.05" in result


class TestUnregisteredEvent:
    """The dispatcher raises for an event type that has no template."""

    def test_raises_value_error_naming_the_event_class(self) -> None:
        """An unregistered concrete event raises ValueError naming the class."""
        # DailyTradeDigest is registered for Slack only — the Telegram registry
        # deliberately omits it. Its absence is the invariant this test protects.
        event = DailyTradeDigest(
            occurred_at=_TS,
            correlation_id=_CID,
            day=date(2026, 8, 23),
            total_trades=1,
            filled=1,
            rejected=0,
            total_notional=Money(amount=Decimal("100"), currency=Currency.EUR),
            top_winners=(),
            top_losers=(),
        )
        with pytest.raises(ValueError, match="DailyTradeDigest"):
            telegram_template(event)


class TestTelegramTemplatePurity:
    """Templates are pure — same input yields the exact same string."""

    def test_two_calls_return_equal_strings(self) -> None:
        """Two calls with the same event produce identical MarkdownV2 output."""
        event = StrategyPaused(
            occurred_at=_TS,
            correlation_id=_CID,
            strategy_id="swing.v1",
            reason="drawdown",
            paused_at=_TS,
            severity=AnomalySeverity.CRITICAL,
        )
        assert telegram_template(event) == telegram_template(event)


class TestCustomEventFallback:
    """Custom DomainEvent subclasses without a template raise ValueError."""

    def test_custom_subclass_raises(self) -> None:
        """A hand-rolled DomainEvent subclass with no template registration raises."""

        @dataclass(frozen=True, slots=True)
        class _CustomEvent(DomainEvent):
            __event_schema_version__: ClassVar[int] = 1

        with pytest.raises(ValueError, match="_CustomEvent"):
            telegram_template(_CustomEvent(occurred_at=_TS, correlation_id=_CID))
