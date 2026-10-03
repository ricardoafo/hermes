"""Tests for Slack message templates.

Verifies for every concrete template:
- The returned SlackMessage has a non-empty ``text`` fallback.
- ``blocks`` starts with a header block, ends with a context footer, and
  the middle section contains the key facts.
- Attachments are empty for short events. The DailyTradeDigest carries
  its P&L CSV only in the CERTIFIED phase; by default it renders blind.
- The dispatcher raises ``ValueError`` for an event type without a template.

Reference: docs/decisions/2026-08-23-slack-alongside-telegram.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.messages.reporting_phase import (
    OUTCOME_FIELD_NAMES,
    OUTCOME_WITHHELD_NOTICE,
    ReportingPhase,
)
from hermes.application.messages.slack import (
    FileAttachment,
    SlackMessage,
    slack_template,
)
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest, TradeSummary
from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.events.strategy_resumed import StrategyResumed
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.events.trade_rejected import TradeRejected
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.domain.value_objects.weight import Weight

_TS = datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"
_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)


def _header_text(msg: SlackMessage) -> str:
    """Return the header block's rendered plain_text field."""
    return msg.blocks[0]["text"]["text"]


def _section_text(msg: SlackMessage) -> str:
    """Return the second block's mrkdwn text (the section body)."""
    return msg.blocks[1]["text"]["text"]


def _context_text(msg: SlackMessage) -> str:
    """Return the last block's context text (the ref footer)."""
    return msg.blocks[-1]["elements"][0]["text"]


class TestStrategyPausedSlack:
    """StrategyPaused renders a 3-block message with no attachments."""

    def _event(self) -> StrategyPaused:
        return StrategyPaused(
            occurred_at=_TS,
            correlation_id=_CID,
            strategy_id="swing.v1",
            reason="30% drawdown breached",
            paused_at=_TS,
            severity=AnomalySeverity.CRITICAL,
        )

    def test_message_has_three_blocks(self) -> None:
        """The rendered SlackMessage has exactly header + section + context."""
        msg = slack_template(self._event())
        assert len(msg.blocks) == 3

    def test_header_names_event_and_timestamp(self) -> None:
        """The header block names the event and the ISO-8601 timestamp."""
        msg = slack_template(self._event())
        header = _header_text(msg)
        assert "Strategy paused" in header
        assert "2026-08-23T14:22:03+00:00" in header

    def test_section_body_names_strategy_and_reason(self) -> None:
        """The section body includes the strategy_id, severity, and reason."""
        section = _section_text(slack_template(self._event()))
        assert "swing.v1" in section
        assert "CRITICAL" in section
        assert "30% drawdown breached" in section

    def test_context_footer_contains_correlation_tail(self) -> None:
        """The context footer contains 'ref:' followed by the 6-char tail."""
        assert "WY6PH1" in _context_text(slack_template(self._event()))

    def test_no_attachments(self) -> None:
        """StrategyPaused ships zero attachments — it is a short event."""
        assert slack_template(self._event()).attachments == ()


class TestStrategyResumedSlack:
    """StrategyResumed renders as a short recovery notice."""

    def test_body_names_strategy_and_pause_duration(self) -> None:
        """The section names the strategy_id and pause duration in seconds."""
        event = StrategyResumed(
            occurred_at=_TS,
            correlation_id=_CID,
            strategy_id="swing.v1",
            resumed_at=_TS,
            pause_duration_seconds=900,
        )
        section = _section_text(slack_template(event))
        assert "swing.v1" in section
        assert "900" in section


class TestBalanceLowSlack:
    """BalanceLow renders with the asset, current, and threshold amounts."""

    def test_body_names_asset_current_and_threshold(self) -> None:
        """The section body names asset, current amount, and threshold."""
        event = BalanceLow(
            occurred_at=_TS,
            correlation_id=_CID,
            asset=Currency.USDT,
            current=Money(amount=Decimal("50"), currency=Currency.USDT),
            threshold=Money(amount=Decimal("100"), currency=Currency.USDT),
            checked_at=_TS,
        )
        section = _section_text(slack_template(event))
        assert "USDT" in section
        assert "50" in section
        assert "100" in section


class TestTradeRejectedSlack:
    """TradeRejected renders with the exchange, side, pair, and reason."""

    def test_body_names_exchange_pair_side_and_reason(self) -> None:
        """The section names exchange, pair, side, and rejection reason."""
        event = TradeRejected(
            occurred_at=_TS,
            correlation_id=_CID,
            pair=_PAIR,
            side=TradeSide.BUY,
            rejection_reason="insufficient balance",
            exchange="binance",
            attempted_at=_TS,
        )
        section = _section_text(slack_template(event))
        assert "binance" in section
        assert "BTC/USDT" in section
        assert "BUY" in section
        assert "insufficient balance" in section


class TestAnomalyDetectedSlack:
    """AnomalyDetected renders source, severity, and reason."""

    def test_body_names_source_severity_and_reason(self) -> None:
        """The section body names source, severity, and reason."""
        event = AnomalyDetected(
            occurred_at=_TS,
            correlation_id=_CID,
            source="swing.risk",
            severity=AnomalySeverity.CRITICAL,
            reason="30% drawdown breached",
        )
        section = _section_text(slack_template(event))
        assert "swing.risk" in section
        assert "CRITICAL" in section
        assert "30% drawdown breached" in section


class TestTradeExecutedSlack:
    """TradeExecuted renders as POSITION_LARGE — 4 fact lines."""

    def test_body_names_pair_side_qty_and_price(self) -> None:
        """The section body includes pair, side, qty, and price fields."""
        event = TradeExecuted(
            occurred_at=_TS,
            correlation_id=_CID,
            pair=_PAIR,
            side=TradeSide.SELL,
            qty=Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
            price=Price(amount=Decimal("68000"), pair=_PAIR),
            fee=Money(amount=Decimal("34"), currency=Currency.USDT),
            strategy_id=StrategyId("swing"),
        )
        section = _section_text(slack_template(event))
        assert "BTC/USDT" in section
        assert "SELL" in section
        assert "0.5" in section
        assert "68000" in section


class TestRebalanceTriggeredSlack:
    """RebalanceTriggered renders with pair, weights, and band."""

    def test_body_names_pair_and_weights(self) -> None:
        """The section body includes pair, current weight, target weight, and band."""
        event = RebalanceTriggered(
            occurred_at=_TS,
            correlation_id=_CID,
            pair=_PAIR,
            current_weight=Weight(value=Decimal("0.35")),
            target_weight=Weight(value=Decimal("0.25")),
            band=Weight(value=Decimal("0.05")),
        )
        section = _section_text(slack_template(event))
        assert "BTC/USDT" in section
        assert "0.35" in section
        assert "0.25" in section
        assert "0.05" in section


class TestDailyTradeDigestSlack:
    """DailyTradeDigest renders with a CSV attachment carrying every row."""

    def _event(self) -> DailyTradeDigest:
        winner = TradeSummary(
            pair=Pair(base=Currency.BTC, quote=Currency.USDT),
            pnl=Money(amount=Decimal("120.00"), currency=Currency.EUR),
        )
        loser = TradeSummary(
            pair=Pair(base=Currency.ETH, quote=Currency.USDT),
            pnl=Money(amount=Decimal("-45.50"), currency=Currency.EUR),
        )
        return DailyTradeDigest(
            occurred_at=_TS,
            correlation_id=_CID,
            day=date(2026, 8, 23),
            total_trades=12,
            filled=10,
            rejected=2,
            total_notional=Money(amount=Decimal("12500.50"), currency=Currency.EUR),
            top_winners=(winner,),
            top_losers=(loser,),
        )

    def test_body_names_counts_and_notional(self) -> None:
        """The section body includes total trades, filled/rejected counts, and notional."""
        section = _section_text(slack_template(self._event()))
        assert "2026-08-23" in section
        assert "12" in section
        assert "10" in section
        assert "12500.50" in section

    def test_attachment_is_a_single_csv(self) -> None:
        """Once certified, the message ships exactly one CSV attachment with the digest suffix."""
        msg = slack_template(self._event(), phase=ReportingPhase.CERTIFIED)
        assert len(msg.attachments) == 1
        attachment: FileAttachment = msg.attachments[0]
        assert attachment.name == "digest-2026-08-23.csv"
        assert attachment.mime == "text/csv"

    def test_csv_content_contains_expected_rows(self) -> None:
        """The certified CSV rows encode header + winners + losers in the documented order."""
        msg = slack_template(self._event(), phase=ReportingPhase.CERTIFIED)
        content = msg.attachments[0].content.decode("utf-8")
        lines = content.strip().splitlines()
        assert lines[0] == "bucket,pair,pnl_amount,pnl_currency"
        # Order matters: winners first, then losers, per the template contract.
        assert lines[1] == "winner,BTC/USDT,120.00,EUR"
        assert lines[2] == "loser,ETH/USDT,-45.50,EUR"


def _rendered_surface(msg: SlackMessage) -> str:
    """Return every string a recipient can see: fallback, blocks and attachments."""
    attachments = " ".join(f"{a.name} {a.content.decode('utf-8')}" for a in msg.attachments)
    return f"{msg.text} {json.dumps(msg.blocks)} {attachments}".lower()


def _digest(winner_pnl: Decimal, loser_pnl: Decimal) -> DailyTradeDigest:
    """Return a digest with fully populated outcome fields."""
    return DailyTradeDigest(
        occurred_at=_TS,
        correlation_id=_CID,
        day=date(2026, 8, 23),
        total_trades=12,
        filled=10,
        rejected=2,
        total_notional=Money(amount=Decimal("12500.50"), currency=Currency.EUR),
        top_winners=(
            TradeSummary(
                pair=Pair(base=Currency.BTC, quote=Currency.USDT),
                pnl=Money(amount=winner_pnl, currency=Currency.EUR),
            ),
        ),
        top_losers=(
            TradeSummary(
                pair=Pair(base=Currency.ETH, quote=Currency.USDT),
                pnl=Money(amount=loser_pnl, currency=Currency.EUR),
            ),
        ),
    )


class TestDailyTradeDigestBlind:
    """Before certification the digest carries no outcome field or value (ADR-012 ruling 1)."""

    def test_default_phase_is_pre_certification(self) -> None:
        """Calling without a phase renders blind: no attachment at all."""
        msg = slack_template(_digest(Decimal("120.00"), Decimal("-45.50")))
        assert msg.attachments == ()

    def test_no_outcome_field_name_appears(self) -> None:
        """None of the suppressed outcome field names appear anywhere in the message."""
        surface = _rendered_surface(slack_template(_digest(Decimal("120.00"), Decimal("-45.50"))))
        leaked = sorted(name for name in OUTCOME_FIELD_NAMES if name in surface)
        assert leaked == []

    def test_activity_counts_and_withheld_notice_remain(self) -> None:
        """The blind digest still reports activity and says why outcomes are missing."""
        section = _section_text(slack_template(_digest(Decimal("120.00"), Decimal("-45.50"))))
        assert "*Filled*: `10`" in section
        assert "*Total notional*: `12500.50 EUR`" in section
        assert OUTCOME_WITHHELD_NOTICE in section

    def test_certified_phase_restores_outcomes(self) -> None:
        """The CERTIFIED phase is the only path that carries the per-pair P&L CSV."""
        msg = slack_template(
            _digest(Decimal("120.00"), Decimal("-45.50")), phase=ReportingPhase.CERTIFIED
        )
        assert "120.00" in _rendered_surface(msg)
        assert OUTCOME_WITHHELD_NOTICE not in _rendered_surface(msg)

    @given(
        winner=st.decimals(
            min_value=Decimal("1000000.01"), max_value=Decimal("9999999.99"), places=2
        ),
        loser=st.decimals(
            min_value=Decimal("-9999999.99"), max_value=Decimal("-1000000.01"), places=2
        ),
    )
    def test_no_pnl_value_appears_for_any_amount(self, winner: Decimal, loser: Decimal) -> None:
        """Property: no P&L amount reaches the blind message, whatever its value.

        Amounts are drawn outside the range of the activity figures so a
        match can only be a leak, never a coincidence with a count.
        """
        surface = _rendered_surface(slack_template(_digest(winner, loser)))
        assert str(winner) not in surface
        assert str(loser) not in surface


class TestSlackDispatchFallback:
    """The dispatcher raises for an event type without a template."""

    def test_custom_subclass_raises_value_error(self) -> None:
        """A hand-rolled DomainEvent subclass with no template registration raises."""

        @dataclass(frozen=True, slots=True)
        class _CustomEvent(DomainEvent):
            __event_schema_version__: ClassVar[int] = 1

        with pytest.raises(ValueError, match="_CustomEvent"):
            slack_template(_CustomEvent(occurred_at=_TS, correlation_id=_CID))


class TestSlackTemplatePurity:
    """Templates are pure — same input yields equal-by-value SlackMessage."""

    def test_two_calls_return_equal_messages(self) -> None:
        """Two calls with the same event produce equal SlackMessage tuples."""
        event = StrategyPaused(
            occurred_at=_TS,
            correlation_id=_CID,
            strategy_id="swing.v1",
            reason="drawdown",
            paused_at=_TS,
            severity=AnomalySeverity.CRITICAL,
        )
        assert slack_template(event) == slack_template(event)


class TestFileAttachmentShape:
    """FileAttachment is a NamedTuple with three fields."""

    def test_named_tuple_field_layout(self) -> None:
        """FileAttachment exposes name, mime, and content fields in that order."""
        attachment = FileAttachment(name="a.csv", mime="text/csv", content=b"x")
        assert attachment.name == "a.csv"
        assert attachment.mime == "text/csv"
        assert attachment.content == b"x"
