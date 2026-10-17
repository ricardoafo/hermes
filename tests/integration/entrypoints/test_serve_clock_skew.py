"""Serve records and alerts its clock leaving exchange time (issue #859 item 2, option A).

Invariants protected here:

- a skew past the settle delay is one ``CRITICAL`` clock-skew anomaly in the
  ledger and one alert on every channel, however many bars follow;
- a restart while the clock is still off records and alerts nothing new;
- a restart with the clock back records one ``INFO`` anomaly, so
  ``hermes status`` turns green again;
- a clock within the limit records nothing.

The feed's measured skew is scripted; the end-to-end measurement against the
fake exchange is in ``tests/integration/scenarios/test_chaos_clock.py``.
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from hermes.domain.events.anomaly_detected import CLOCK_SKEW_SOURCE, AnomalyDetected
from tests.support.serve_loop import (
    FakeFeed,
    RecordingChannel,
    build,
    events,
    h4_bar,
    history,
    live,
    run_once,
)

pytestmark = pytest.mark.integration

_FIVE_MINUTES_AHEAD = 300_000


def _boot(db: Path, skew_ms: int | None, *, live_from: int) -> RecordingChannel:
    """Run serve once over bars up to ``live_from + 1`` with the feed reporting ``skew_ms``."""
    feed = FakeFeed(
        warm=history(live_from - 1), live_bars=live(live_from, live_from + 1), clock_skew_ms=skew_ms
    )
    telegram = RecordingChannel()
    now = h4_bar(live_from + 1).close_time + timedelta(seconds=1)
    run_once(build(db, feed, now, telegram=telegram), feed)
    return telegram


def _recorded(db: Path) -> list[str]:
    """Return the severities of the clock-skew anomalies in the ledger, oldest first."""
    payloads = (json.loads(row["payload_json"]) for row in events(db, "AnomalyDetected"))
    return [p["severity"] for p in payloads if p["source"] == CLOCK_SKEW_SOURCE]


def _alerted(channel: RecordingChannel) -> list[str]:
    """Return the severities of the clock-skew alerts the channel received."""
    return [
        e.severity.value
        for e in channel.seen
        if isinstance(e, AnomalyDetected) and e.source == CLOCK_SKEW_SOURCE
    ]


# SEV-1: no rubric row matches; a clock that silently halts every entry must page the operator
def test_a_clock_five_minutes_ahead_is_recorded_and_alerted_once(tmp_path: Path) -> None:
    """Two live bars with the clock off give one CRITICAL row and one alert, not two."""
    db = tmp_path / "ledger.sqlite3"

    telegram = _boot(db, _FIVE_MINUTES_AHEAD, live_from=11)

    assert (_recorded(db), _alerted(telegram)) == (["CRITICAL"], ["CRITICAL"])


def test_a_restart_with_the_clock_still_off_adds_nothing(tmp_path: Path) -> None:
    """The off state survives the restart through the ledger, so it is not alerted again."""
    db = tmp_path / "ledger.sqlite3"
    _boot(db, _FIVE_MINUTES_AHEAD, live_from=11)

    telegram = _boot(db, _FIVE_MINUTES_AHEAD, live_from=13)

    assert (_recorded(db), _alerted(telegram)) == (["CRITICAL"], [])


def test_a_restart_with_the_clock_fixed_records_its_return(tmp_path: Path) -> None:
    """Once the clock is back, one INFO row clears the red status and a quiet alert says so."""
    db = tmp_path / "ledger.sqlite3"
    _boot(db, _FIVE_MINUTES_AHEAD, live_from=11)

    telegram = _boot(db, 40, live_from=13)

    assert (_recorded(db), _alerted(telegram)) == (["CRITICAL", "INFO"], ["INFO"])


def test_a_clock_within_the_settle_delay_records_nothing(tmp_path: Path) -> None:
    """A skew of a few seconds, under the 5 s settle delay, is on time."""
    db = tmp_path / "ledger.sqlite3"

    telegram = _boot(db, -4_999, live_from=11)

    assert (_recorded(db), _alerted(telegram)) == ([], [])
