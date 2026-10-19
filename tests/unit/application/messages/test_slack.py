"""Tests for the Slack templates.

Invariants protected here:

- every notifiable event has a Slack template; the message is a section block
  for the headline and details plus a context block ending in the one ref;
- the shape per family is the one in the shape table (verbose for trade
  lifecycle, compact for health and reports);
- markup characters in free text are escaped so Slack cannot read a reason as
  a link or a mention;
- before certification the digest carries no outcome field name, no P&L value
  and no attachment; the CERTIFIED phase alone attaches the CSV;
- an event with no Slack template raises instead of posting nothing.

Reference: docs/decisions/2026-10-04-notification-templates-v2.md;
    openspec/archive/144-notification-system/change.md (R-1, R-4).
"""

# ruff: noqa: RUF001 - the typographic minus sign is the character under test

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import date
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
from hermes.application.messages.slack import FileAttachment, SlackMessage, slack_template
from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest, TradeSummary
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from tests.support.notification_samples import (
    BTC_USDT,
    CORRELATION_ID,
    NOON_SATURDAY_UTC,
    samples,
)


def _section(message: SlackMessage) -> str:
    return str(message.blocks[0]["text"]["text"])


def _context(message: SlackMessage) -> str:
    return str(message.blocks[-1]["elements"][0]["text"])


@pytest.mark.parametrize("name", sorted(samples()))
def test_every_event_is_a_section_then_a_context_ending_in_the_ref(name: str) -> None:
    """Each message is a bold-headline section followed by a context block that ends in the ref."""
    message = slack_template(samples()[name])

    assert (
        [block["type"] for block in message.blocks],
        _section(message).startswith("*"),
        _context(message).endswith("…K7Q2ZP"),
    ) == (["section", "context"], True, True)


def test_signal_entry_parent_is_the_headline_with_the_utc_time() -> None:
    """A signal's parent message is only the verbose headline and the ref: the plan is not in it."""
    message = slack_template(samples()["signal-entry"])

    assert (_section(message), _context(message)) == (
        "*▲ Entry long BTC · 52,410.20 USDT · Sat 14:00 (12:00 UTC)*",
        "swing-donchian-4h · …K7Q2ZP",
    )


def test_signal_entry_plan_reply_has_the_plan_and_the_withheld_actual() -> None:
    """The thread reply of a signal carries the stop, distance, ATR multiple and withheld actual."""
    plan = slack_template(samples()["signal-entry"]).plan

    assert plan is not None
    assert plan.blocks[0]["text"]["text"].splitlines() == [
        "Plan: stop 50,516.00 USDT (−3.6%, 1.8 ATR)",
        "Actual: withheld until certification",
    ]


def test_signal_fallback_text_is_the_headline_and_the_plan_text_is_the_plan() -> None:
    """The parent fallback names the coin; the reply fallback names the stop."""
    message = slack_template(samples()["signal-entry"])

    assert message.plan is not None
    assert message.text.startswith("▲ Entry long BTC") and "stop" not in message.text
    assert message.plan.text.startswith("Plan: stop 50,516.00 USDT")


def test_event_without_a_plan_has_no_thread_reply() -> None:
    """A compact health event stays one message: it has no plan reply."""
    message = slack_template(samples()["strategy-paused-critical"])
    assert (message.plan, message.text.count("Plan:")) == (None, 0)


def test_health_event_is_compact_on_slack() -> None:
    """A pause is compact on Slack: headline and reason in the section, hint and ref in context."""
    message = slack_template(samples()["strategy-paused-critical"])

    assert (_section(message).splitlines(), _context(message)) == (
        ["*■ Strategy paused · swing-donchian-4h · Sat 14:00*", "operator kill switch"],
        "review the strategy · …K7Q2ZP",
    )


def test_rebalance_heads_up_is_compact_on_slack() -> None:
    """The rebalance heads-up is the compact two-line report on Slack."""
    message = slack_template(samples()["rebalance-triggered"])

    assert _section(message).splitlines()[1] == (
        "35.0% vs target 25.0% · drift 10.0 pp (band 5.0 pp)"
    )


def test_markup_characters_in_free_text_are_escaped() -> None:
    """A reason with <, > and & cannot become a Slack link or mention."""
    base = samples()["anomaly-critical"]
    assert isinstance(base, AnomalyDetected)
    event = dataclasses.replace(base, reason="<!channel> a & b")

    assert "&lt;!channel&gt; a &amp; b" in _section(slack_template(event))


def test_short_events_carry_no_attachment() -> None:
    """Only the certified digest attaches a file."""
    attachments = {
        name: slack_template(event).attachments
        for name, event in samples().items()
        if name != "daily-digest"
    }

    assert set(attachments.values()) == {()}


def _digest(winner_pnl: Decimal, loser_pnl: Decimal) -> DailyTradeDigest:
    """Return a digest with fully populated outcome fields."""
    return DailyTradeDigest(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        day=date(2026, 8, 23),
        total_trades=12,
        filled=10,
        rejected=2,
        total_notional=Money(amount=Decimal("12500.50"), currency=Currency.EUR),
        top_winners=(
            TradeSummary(pair=BTC_USDT, pnl=Money(amount=winner_pnl, currency=Currency.EUR)),
        ),
        top_losers=(
            TradeSummary(
                pair=Pair(base=Currency.ETH, quote=Currency.USDT),
                pnl=Money(amount=loser_pnl, currency=Currency.EUR),
            ),
        ),
    )


def _surface(message: SlackMessage) -> str:
    """Return every string a recipient can see: fallback, blocks and attachments."""
    files = " ".join(f"{a.name} {a.content.decode('utf-8')}" for a in message.attachments)
    return f"{message.text} {json.dumps(message.blocks)} {files}".lower()


def test_blind_digest_has_no_attachment_and_no_outcome_field_name() -> None:
    """Default phase is pre-certification: no CSV, and no suppressed outcome field name appears."""
    message = slack_template(_digest(Decimal("120.00"), Decimal("-45.50")))
    surface = _surface(message)

    assert (message.attachments, sorted(n for n in OUTCOME_FIELD_NAMES if n in surface)) == (
        (),
        [],
    )


def test_blind_digest_keeps_activity_and_says_why_outcomes_are_missing() -> None:
    """The blind digest still reports activity and states that outcome figures are withheld."""
    section = _section(slack_template(_digest(Decimal("120.00"), Decimal("-45.50"))))

    assert section.splitlines()[1:] == [
        "10/12 filled · 2 rejected · €12,500.50 traded",
        OUTCOME_WITHHELD_NOTICE,
    ]


def test_certified_digest_attaches_one_csv_with_winners_then_losers() -> None:
    """The CERTIFIED phase alone attaches the CSV, winners first, then losers."""
    message = slack_template(
        _digest(Decimal("120.00"), Decimal("-45.50")), phase=ReportingPhase.CERTIFIED
    )
    attachment = message.attachments[0]

    assert (
        len(message.attachments),
        attachment.name,
        attachment.mime,
        attachment.content.decode("utf-8").splitlines(),
    ) == (
        1,
        "digest-2026-08-23.csv",
        "text/csv",
        [
            "bucket,pair,pnl_amount,pnl_currency",
            "winner,BTC/USDT,120.00,EUR",
            "loser,ETH/USDT,-45.50,EUR",
        ],
    )


def test_certified_digest_drops_the_withheld_notice() -> None:
    """Once certified the digest no longer says outcomes are withheld."""
    message = slack_template(
        _digest(Decimal("120.00"), Decimal("-45.50")), phase=ReportingPhase.CERTIFIED
    )

    assert OUTCOME_WITHHELD_NOTICE not in _surface(message)


@given(
    winner=st.decimals(min_value=Decimal("1000000.01"), max_value=Decimal("9999999.99"), places=2),
    loser=st.decimals(min_value=Decimal("-9999999.99"), max_value=Decimal("-1000000.01"), places=2),
)
def test_no_pnl_value_appears_for_any_amount(winner: Decimal, loser: Decimal) -> None:
    """Property: no P&L amount reaches the blind digest, whatever its value.

    Amounts are drawn outside the range of the activity figures so a match can
    only be a leak, never a coincidence with a count.
    """
    surface = _surface(slack_template(_digest(winner, loser)))

    assert (str(winner) in surface, str(loser) in surface) == (False, False)


def test_custom_event_without_a_template_raises() -> None:
    """A hand-rolled DomainEvent subclass with no builder raises ValueError."""

    @dataclass(frozen=True, slots=True)
    class _CustomEvent(DomainEvent):
        __event_schema_version__: ClassVar[int] = 1

    with pytest.raises(ValueError, match="_CustomEvent"):
        slack_template(_CustomEvent(occurred_at=NOON_SATURDAY_UTC, correlation_id="X"))


def test_two_calls_return_equal_messages() -> None:
    """Templates are pure: the same event renders to an equal SlackMessage."""
    event = samples()["strategy-paused-critical"]

    assert slack_template(event) == slack_template(event)


def test_file_attachment_field_layout() -> None:
    """FileAttachment exposes name, mime and content in that order."""
    assert tuple(FileAttachment(name="a.csv", mime="text/csv", content=b"x")) == (
        "a.csv",
        "text/csv",
        b"x",
    )


def test_slack_entry_with_a_rate_is_in_euros() -> None:
    """Invariant (668 R-1): the fallback text and the blocks show EUR prices."""
    message = slack_template(samples()["signal-entry"], eur_rate=Decimal("1.25"))
    assert (
        message.text.startswith("▲ Entry long BTC · €41,928.16"),
        "USDT" in str(message.blocks),
    ) == (
        True,
        False,
    )


def test_slack_entry_without_a_rate_stays_in_usdt() -> None:
    """Invariant (668 R-2): no rate, the USDT text of before."""
    message = slack_template(samples()["signal-entry"])
    assert message.text.startswith("▲ Entry long BTC · 52,410.20 USDT")
