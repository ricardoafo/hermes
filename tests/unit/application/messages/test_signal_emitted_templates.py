"""Tests for the ``SignalEmitted`` Telegram and Slack templates.

Invariants protected here:

- both templates carry pair, kind, price and the bar time (``occurred_at``);
- Telegram output is MarkdownV2-escaped;
- aggregate blind (R-0, ADR-012 ruling 1): no outcome field name appears in
  any rendering of an entry or a populated exit, in either channel, whatever
  the reporting phase passed to Slack.

Reference: 309-serve-bar-loop change.md R-0 (A-0.1), D-4.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.application.messages.reporting_phase import OUTCOME_FIELD_NAMES, ReportingPhase
from hermes.application.messages.slack import slack_template
from hermes.application.messages.telegram import telegram_template
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_BAR_CLOSE = datetime(2026, 10, 2, 15, 59, 59, 999000, tzinfo=UTC)


def _event(kind: SignalKind) -> SignalEmitted:
    """Return a populated entry or exit event."""
    return SignalEmitted.from_signal(
        StrategySignal(
            kind=kind,
            side=SignalSide.LONG,
            entry_price=Decimal("65000.5"),
            stop_price=Decimal("62000.25"),
            atr_context=Decimal("1200.75"),
            signal_time=_BAR_CLOSE,
            exit_reason="chandelier" if kind is SignalKind.EXIT else None,
        ),
        strategy_id=StrategyId("swing-donchian-4h"),
        pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
    )


def test_telegram_entry_carries_pair_kind_price_and_bar_time_escaped() -> None:
    """The Telegram entry names the pair, kind, price and time, with reserved chars escaped."""
    text = telegram_template(_event(SignalKind.ENTRY))

    assert "BTC/USDT long entry @ 65000\\.5" in text
    assert "2026\\-10\\-02T15:59:59\\.999000\\+00:00" in text


def test_telegram_exit_names_the_exit_rule_and_the_entry_reference() -> None:
    """The Telegram exit shows the exit rule and entry reference price."""
    text = telegram_template(_event(SignalKind.EXIT))

    assert "BTC/USDT long exit \\(chandelier\\) · entry ref 65000\\.5" in text


def test_slack_entry_carries_pair_kind_price_and_bar_time() -> None:
    """The Slack entry lists pair, kind, price and bar time."""
    message = slack_template(_event(SignalKind.ENTRY))
    section = message.blocks[1]["text"]["text"]

    assert section.splitlines() == [
        "*Pair*: `BTC/USDT`",
        "*Side*: `long`",
        "*Kind*: `entry`",
        "*Price*: `65000.5`",
        "*Bar time*: `2026-10-02T15:59:59.999000+00:00`",
    ]


def test_slack_exit_lists_the_rule_and_the_entry_reference() -> None:
    """The Slack exit lists the exit rule and entry reference, with no attachment."""
    message = slack_template(_event(SignalKind.EXIT))
    section = message.blocks[1]["text"]["text"]

    assert ("*Exit rule*: `chandelier`" in section, "*Entry ref*: `65000.5`" in section) == (
        True,
        True,
    )
    assert message.attachments == ()


@pytest.mark.parametrize("kind", [SignalKind.ENTRY, SignalKind.EXIT])
@pytest.mark.parametrize("phase", list(ReportingPhase))
def test_no_rendering_contains_an_outcome_field_name(
    kind: SignalKind, phase: ReportingPhase
) -> None:
    """Aggregate blind: no outcome field name appears in any new template output."""
    event = _event(kind)
    rendered = " ".join(
        [
            telegram_template(event),
            json.dumps(slack_template(event, phase=phase)._asdict(), default=str),
        ]
    ).lower()

    leaked = sorted(name for name in OUTCOME_FIELD_NAMES if name in rendered)

    assert leaked == []
