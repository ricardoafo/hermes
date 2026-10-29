"""Reviewed snapshots of every notification event on both channels.

A wording change shows up as a snapshot diff that the reviewer reads before
running ``--snapshot-update``. The snapshots only fix the canonical rendering;
the behavior of each field is asserted in ``test_notes.py``, ``test_telegram.py``
and ``test_slack.py``.

Invariant protected: the rendered Telegram text and Slack message of every
sample event stay exactly as reviewed.

Reference: openspec/archive/144-notification-system/change.md (test plan).
"""

from decimal import Decimal

import pytest
from syrupy.assertion import SnapshotAssertion

from hermes.application.messages.slack import slack_template
from hermes.application.messages.telegram import telegram_template
from tests.support.notification_samples import samples

_TELEGRAM_EVENTS = sorted(name for name in samples() if name != "daily-digest")


@pytest.mark.parametrize("name", _TELEGRAM_EVENTS)
def test_telegram_message_matches_snapshot(name: str, snapshot: SnapshotAssertion) -> None:
    """The Telegram text of each event matches its reviewed snapshot."""
    assert telegram_template(samples()[name]).splitlines() == snapshot


@pytest.mark.parametrize("name", sorted(samples()))
def test_slack_message_matches_snapshot(name: str, snapshot: SnapshotAssertion) -> None:
    """The Slack fallback text and blocks of each event match their reviewed snapshot."""
    message = slack_template(samples()[name])

    plan = message.plan
    assert {
        "text": message.text,
        "blocks": message.blocks,
        **({} if plan is None else {"plan": {"text": plan.text, "blocks": plan.blocks}}),
    } == snapshot


_EUR_RATE = Decimal("1.25")
_PRICED_EVENTS = ("signal-entry", "signal-exit", "position-large")


@pytest.mark.parametrize("name", _PRICED_EVENTS)
def test_telegram_message_with_a_rate_matches_snapshot(
    name: str, snapshot: SnapshotAssertion
) -> None:
    """The Telegram text of each priced event, in EUR at 1.25 USDT per euro, matches."""
    assert telegram_template(samples()[name], eur_rate=_EUR_RATE).splitlines() == snapshot


@pytest.mark.parametrize("name", _PRICED_EVENTS)
def test_slack_message_with_a_rate_matches_snapshot(name: str, snapshot: SnapshotAssertion) -> None:
    """The Slack text and blocks of each priced event, in EUR at 1.25 USDT per euro, match."""
    message = slack_template(samples()[name], eur_rate=_EUR_RATE)

    plan = message.plan
    assert {
        "text": message.text,
        "blocks": message.blocks,
        **({} if plan is None else {"plan": {"text": plan.text, "blocks": plan.blocks}}),
    } == snapshot
