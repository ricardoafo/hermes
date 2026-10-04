"""Tests for the change-only heartbeat alerts (ADR-015 D-07, D-08).

Invariants protected here:

- OK sends nothing, and staying OK keeps sending nothing;
- late sends one silent message, once;
- down sends one loud message, then reminders at 15 min, 1 h, 4 h and every
  12 h after it went down;
- recovery sends one silent "back after N min";
- quiet hours hold reminders until they end, but the first down still rings;
- no pushed message carries money.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta

from hermes.application.bot.heartbeat_alerts import HeartbeatAlerter, Outbound
from hermes.application.status import VerdictLevel

# 10:00 UTC on a Sunday in October is 12:00 Madrid: outside quiet hours.
_T0 = datetime(2026, 10, 4, 10, tzinfo=UTC)
_DOWN = "hermes is down"
_LATE = "The 12:00 check is 20 minutes late"


def _minutes(
    alerter: HeartbeatAlerter, level: VerdictLevel, headline: str, *mins: int
) -> list[list[Outbound]]:
    return [alerter.observe(level, headline, _T0 + timedelta(minutes=m)) for m in mins]


def test_ok_sends_nothing() -> None:
    """While all is well the phone stays silent."""
    alerter = HeartbeatAlerter()

    assert _minutes(alerter, VerdictLevel.OK, "All good", 0, 1, 60) == [[], [], []]


def test_late_sends_one_silent_message_once() -> None:
    """A late check is worth a glance, not a ring, and only once."""
    alerter = HeartbeatAlerter()

    sent = _minutes(alerter, VerdictLevel.LATE, _LATE, 0, 1, 30)

    assert sent == [[Outbound(f"{_LATE}. Nothing to do yet.", silent=True)], [], []]


def test_down_rings_once_then_reminds_on_the_backoff() -> None:
    """One loud message, then reminders at 15 min, 1 h, 4 h, then every 12 h.

    Quiet hours are switched off here (equal start and end) so the 12 h step
    is not held overnight; the quiet-hours test covers that.
    """
    alerter = HeartbeatAlerter(quiet_start=time(0, 0), quiet_end=time(0, 0))
    first = alerter.observe(VerdictLevel.DOWN, _DOWN, _T0)
    reminders_at = []
    for minute in range(1, 16 * 60 + 1):
        now = _T0 + timedelta(minutes=minute)
        if alerter.observe(VerdictLevel.DOWN, _DOWN, now):
            reminders_at.append(minute)

    assert [m.silent for m in first] == [False]
    assert first[0].text.startswith("hermes is down since 12:00")
    assert reminders_at == [15, 60, 240, 240 + 12 * 60]


def test_recovery_sends_one_silent_back_after_message() -> None:
    """Back to OK after 25 min down: one quiet "back after 25 min"."""
    alerter = HeartbeatAlerter()
    alerter.observe(VerdictLevel.DOWN, _DOWN, _T0)

    sent = alerter.observe(VerdictLevel.OK, "All good", _T0 + timedelta(minutes=25))

    assert sent == [Outbound("hermes is back after 25 min.", silent=True)]


def test_late_then_down_counts_downtime_from_when_it_went_late() -> None:
    """Late at 0, down at 10, back at 30: back after 30 min."""
    alerter = HeartbeatAlerter()
    alerter.observe(VerdictLevel.LATE, _LATE, _T0)
    down = alerter.observe(VerdictLevel.DOWN, _DOWN, _T0 + timedelta(minutes=10))

    back = alerter.observe(VerdictLevel.OK, "All good", _T0 + timedelta(minutes=30))

    assert [m.silent for m in down] == [False]
    assert back == [Outbound("hermes is back after 30 min.", silent=True)]


def test_quiet_hours_hold_reminders_but_the_first_down_rings() -> None:
    """Down at 22:50 Madrid rings; the 23:05 reminder waits until 08:00."""
    start = datetime(2026, 10, 4, 20, 50, tzinfo=UTC)  # 22:50 Madrid (CEST)
    alerter = HeartbeatAlerter()
    first = alerter.observe(VerdictLevel.DOWN, _DOWN, start)
    sent_at = [
        minute
        for minute in range(1, 10 * 60)
        if alerter.observe(VerdictLevel.DOWN, _DOWN, start + timedelta(minutes=minute))
    ]

    assert [m.silent for m in first] == [False]
    # 08:00 Madrid is 06:00 UTC, 550 minutes after 20:50 UTC.
    assert sent_at[0] == 550


def test_pushed_messages_never_carry_money() -> None:
    """No euro sign, amount or P&L word in any heartbeat push."""
    alerter = HeartbeatAlerter()
    texts = [
        m.text
        for sent in (
            alerter.observe(VerdictLevel.LATE, _LATE, _T0),
            alerter.observe(VerdictLevel.DOWN, _DOWN, _T0 + timedelta(minutes=5)),
            alerter.observe(VerdictLevel.DOWN, _DOWN, _T0 + timedelta(minutes=25)),
            alerter.observe(VerdictLevel.OK, "All good", _T0 + timedelta(minutes=40)),
        )
        for m in sent
    ]

    assert [t for t in texts if re.search(r"€|p&l|pnl|net |profit", t, re.I)] == []
