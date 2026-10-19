"""Tests for the Telegram templates.

Invariants protected here:

- every notifiable event except the Slack-only digest has a Telegram template,
  including the resume that mirrors a pause (R-1);
- every message ends with exactly one correlation ref and carries no emoji;
- reserved MarkdownV2 characters in free text are escaped, so a reason can
  never corrupt the message;
- the shape per family is the one in the shape table (compact for trade
  lifecycle and health, one line for the rebalance heads-up);
- an event with no Telegram template raises instead of sending nothing.

Reference: docs/decisions/2026-10-04-notification-templates-v2.md;
    openspec/archive/144-notification-system/change.md (R-1).
"""

import dataclasses
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest

from hermes.application.messages.telegram import telegram_template
from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.domain.events.base import DomainEvent
from tests.support.notification_samples import NOON_SATURDAY_UTC, samples

_TELEGRAM_EVENTS = sorted(name for name in samples() if name != "daily-digest")
_UNESCAPED_RESERVED = re.compile(r"(?<!\\)[_*\[\]()~`>#+=|{}.!-]")


@pytest.mark.parametrize("name", _TELEGRAM_EVENTS)
def test_every_message_ends_with_one_ref(name: str) -> None:
    """The last line is the ref, and it appears nowhere else in the message."""
    text = telegram_template(samples()[name])

    assert (text.splitlines()[-1].endswith("\u2026K7Q2ZP"), text.count("K7Q2ZP")) == (True, 1)


@pytest.mark.parametrize("name", _TELEGRAM_EVENTS)
def test_every_message_is_plain_text_without_emoji(name: str) -> None:
    """Severity is a plain glyph from the approved set; no character is in an emoji block."""
    text = telegram_template(samples()[name])

    assert [c for c in text if ord(c) >= 0x1F000] == []


@pytest.mark.parametrize("name", _TELEGRAM_EVENTS)
def test_every_reserved_markdown_character_is_escaped(name: str) -> None:
    """No MarkdownV2 reserved character appears unescaped in any rendered message."""
    text = telegram_template(samples()[name])

    assert _UNESCAPED_RESERVED.findall(text) == []


def test_resume_has_a_telegram_template_that_mirrors_the_pause() -> None:
    """A resume reaches Telegram, so a pushed pause is followed by its recovery (rule 7)."""
    assert telegram_template(samples()["strategy-resumed"]).splitlines()[:2] == [
        "\u25cf Strategy resumed \u00b7 swing\\-donchian\\-4h \u00b7 Sat 14:00",
        "back after 15 min",
    ]


def test_signal_entry_is_the_compact_three_lines() -> None:
    """A signal is compact on Telegram: headline, stop line, strategy and ref."""
    assert telegram_template(samples()["signal-entry"]).splitlines() == [
        "\u25b2 Entry long BTC \u00b7 52,410\\.20 USDT",
        "stop 50,516\\.00 USDT \\(\u22123\\.6%\\) \u00b7 Sat 14:00",
        "swing\\-donchian\\-4h \u00b7 \u2026K7Q2ZP",
    ]


def test_rebalance_heads_up_is_a_single_line() -> None:
    """The rebalance heads-up is one pointer line on Telegram, not a report."""
    assert telegram_template(samples()["rebalance-triggered"]) == (
        "\u25d0 Rebalance heads\\-up BTC: drift 10\\.0 pp \\(band 5\\.0 pp\\) \u00b7 \u2026K7Q2ZP"
    )


def test_free_text_with_reserved_characters_is_escaped() -> None:
    """A reason with dots, brackets and a dash renders literally instead of breaking the parse."""
    base = samples()["anomaly-critical"]
    assert isinstance(base, AnomalyDetected)
    event = dataclasses.replace(base, reason="a.b (c) - d!")

    assert "a\\.b \\(c\\) \\- d\\!" in telegram_template(event)


def test_digest_is_slack_only_and_raises_naming_the_class() -> None:
    """The daily digest has no Telegram template: reports live on Slack."""
    with pytest.raises(ValueError, match="DailyTradeDigest"):
        telegram_template(samples()["daily-digest"])


def test_custom_event_without_a_template_raises() -> None:
    """A hand-rolled DomainEvent subclass with no builder raises ValueError."""

    @dataclass(frozen=True, slots=True)
    class _CustomEvent(DomainEvent):
        __event_schema_version__: ClassVar[int] = 1

    with pytest.raises(ValueError, match="_CustomEvent"):
        telegram_template(_CustomEvent(occurred_at=NOON_SATURDAY_UTC, correlation_id="X"))


def test_two_calls_return_equal_strings() -> None:
    """Templates are pure: the same event renders to the identical string."""
    event = samples()["strategy-paused-critical"]

    assert telegram_template(event) == telegram_template(event)


def test_telegram_entry_with_a_rate_is_in_euros_and_escaped() -> None:
    """Invariant (668 R-1): the push shows EUR prices and the dot stays MarkdownV2-escaped."""
    text = telegram_template(samples()["signal-entry"], eur_rate=Decimal("1.25"))
    assert text.splitlines()[:2] == [
        "▲ Entry long BTC · €41,928\\.16",
        "stop €40,412\\.80 \\(−3\\.6%\\) · Sat 14:00",  # noqa: RUF001 - the typographic minus is the character under test
    ]


def test_telegram_entry_without_a_rate_stays_in_usdt() -> None:
    """Invariant (668 R-2): no rate, the USDT text of before."""
    assert telegram_template(samples()["signal-entry"]).splitlines()[0] == (
        "▲ Entry long BTC · 52,410\\.20 USDT"
    )
