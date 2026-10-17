"""Tests for the shared status layer: stream states and the verdict.

Invariants protected here:

- a stream is late only once ``now`` passes its next close plus settle plus
  grace, and never-processed streams count as late ("never run");
- the verdict is down whenever systemd says the unit is not running, before
  any stream rule; down when serve's clock is off (#859); late when any
  stream is late; all good otherwise;
- the report counts signals in the 24 h and 7 d windows the reader returns.

A fake reader stands in for the ledger; the SQLite reader has its own tests
in ``tests/unit/adapters/ledger/test_status_reader.py``, and the CLI tests
run both together.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.status import (
    StatusReport,
    StreamState,
    UnitState,
    VerdictLevel,
    build_report,
    ledger_missing_report,
)
from hermes.domain.events.anomaly_detected import (
    CLOCK_SKEW_SOURCE,
    AnomalyDetected,
    AnomalySeverity,
)
from hermes.domain.events.risk_halt_recorded import HaltKind, RiskHaltRecorded
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.status import ShadowEntry
from tests.support.status_ledger import BTC, ETH, INTERVALS, LAST_1D, LAST_4H, NOW, PAIRS

_SETTLE = timedelta(seconds=5)
_GRACE = timedelta(seconds=900)
_ONE_MS = timedelta(milliseconds=1)


class _FakeReader:
    """StatusReadPort over a dict of closes and a list of signal times."""

    def __init__(
        self,
        closes: dict[tuple[Pair, KlineInterval], datetime | None],
        signal_times: tuple[datetime, ...] = (),
        entries: tuple[ShadowEntry, ...] = (),
        halts: tuple[RiskHaltRecorded, ...] = (),
        clock_skew: AnomalyDetected | None = None,
    ) -> None:
        """Store the canned answers."""
        self.closes = closes
        self.signal_times = signal_times
        self.entries = entries
        self.halts = halts
        self.clock_skew = clock_skew

    def last_processed_close(self, pair: Pair, interval: KlineInterval) -> datetime | None:
        """Return the canned inclusive close."""
        return self.closes.get((pair, interval))

    def count_bars_since(self, interval: KlineInterval, since: datetime) -> int:
        """Count canned closes of ``interval`` at or after ``since``."""
        return sum(
            1
            for (_, i), close in self.closes.items()
            if i is interval and close is not None and close >= since
        )

    def count_signals_since(self, since: datetime) -> int:
        """Count canned signal times at or after ``since``."""
        return sum(1 for t in self.signal_times if t >= since)

    def open_entries(self) -> tuple[ShadowEntry, ...]:
        """Return the canned entries."""
        return self.entries

    def halts_since(self, since: datetime) -> tuple[RiskHaltRecorded, ...]:
        """Return the canned halts at or after ``since``."""
        return tuple(h for h in self.halts if h.occurred_at >= since)

    def last_clock_skew(self) -> AnomalyDetected | None:
        """Return the canned clock-skew anomaly."""
        return self.clock_skew


def _current() -> dict[tuple[Pair, KlineInterval], datetime | None]:
    """Every stream at its latest boundary before NOW (inclusive closes)."""
    return {
        (pair, interval): (LAST_4H if interval is KlineInterval.H4 else LAST_1D) - _ONE_MS
        for pair in PAIRS
        for interval in INTERVALS
    }


def _report(
    reader: _FakeReader, *, now: datetime = NOW, unit: UnitState = UnitState.RUNNING
) -> StatusReport:
    """Build a report over the shadow universe with the configured settle and grace."""
    return build_report(
        reader,
        pairs=PAIRS,
        intervals=INTERVALS,
        now=now,
        settle=_SETTLE,
        grace=_GRACE,
        unit=unit,
    )


def test_current_streams_give_all_good_with_nothing_to_do() -> None:
    """Every stream on time and the unit running reads as all good, your move nothing."""
    report = _report(_FakeReader(_current()))

    assert (report.verdict.level, report.verdict.headline, report.verdict.next_step) == (
        VerdictLevel.OK,
        "All good",
        "nothing",
    )
    assert report.verdict.reason == "Watching BTC, ETH and SOL. Every check ran on time."
    assert {s.state for s in report.streams} == {StreamState.ON_TIME}


def test_stream_due_time_is_next_boundary_plus_settle() -> None:
    """The 4h stream that closed at 16:00 is next due at 20:00 plus the settle delay."""
    report = _report(_FakeReader(_current()))
    btc_4h = next(s for s in report.streams if (s.pair, s.interval) == (BTC, KlineInterval.H4))

    assert (btc_4h.last_close, btc_4h.next_close, btc_4h.due) == (
        LAST_4H,
        LAST_4H + timedelta(hours=4),
        LAST_4H + timedelta(hours=4) + _SETTLE,
    )


def test_a_stream_is_on_time_until_due_plus_grace_and_late_one_second_after() -> None:
    """The boundary of lateness is exactly due + grace."""
    due = LAST_4H + timedelta(hours=4) + _SETTLE
    at_edge = _report(_FakeReader(_current()), now=due + _GRACE)
    past_edge = _report(_FakeReader(_current()), now=due + _GRACE + timedelta(seconds=1))

    assert at_edge.verdict.level is VerdictLevel.OK
    assert past_edge.verdict.level is VerdictLevel.LATE


def test_an_overdue_stream_gives_late_with_the_check_time_in_madrid() -> None:
    """A missing 20:00 UTC bar reads as the 22:00 (Madrid) check being late, in minutes."""
    now = LAST_4H + timedelta(hours=4) + _SETTLE + timedelta(minutes=20)
    report = _report(_FakeReader(_current()), now=now)

    assert report.verdict.level is VerdictLevel.LATE
    assert report.verdict.headline == "The 22:00 check is 20 minutes late"
    late = [s for s in report.streams if s.state is StreamState.LATE]
    assert [f"{s.pair} {s.interval.value}" for s in late] == [
        "BTC/USDT 4h",
        "ETH/USDT 4h",
        "SOL/USDT 4h",
    ]


def test_a_never_processed_stream_counts_as_late() -> None:
    """A stream with no bar at all must not hide behind the others being on time."""
    closes = _current()
    closes[(ETH, KlineInterval.D1)] = None
    report = _report(_FakeReader(closes))

    assert (report.verdict.level, report.verdict.headline) == (
        VerdictLevel.LATE,
        "A check has never run",
    )
    assert report.verdict.reason == "No bar processed yet for ETH/USDT 1d."


@pytest.mark.parametrize(
    ("unit", "reason"),
    [
        (UnitState.FAILED, "hermes.service crashed, restarted until systemd gave up, and stopped."),
        (UnitState.RESTARTING, "hermes.service crashed and systemd is restarting it."),
        (UnitState.STOPPED, "hermes.service is not running."),
    ],
)
def test_a_unit_that_is_not_running_is_down_even_with_current_streams(
    unit: UnitState, reason: str
) -> None:
    """Fresh ledger rows must not mask a dead process: down wins over every stream rule."""
    report = _report(_FakeReader(_current()), unit=unit)

    assert (report.verdict.level, report.verdict.reason) == (VerdictLevel.DOWN, reason)


def test_unknown_unit_state_falls_back_to_the_stream_rules() -> None:
    """Off a systemd host the verdict comes from the ledger alone."""
    report = _report(_FakeReader(_current()), unit=UnitState.UNKNOWN)

    assert report.verdict.level is VerdictLevel.OK


def test_signal_counts_use_24_hour_and_7_day_windows() -> None:
    """A signal 2 days ago counts for 7d only; one 10 days ago counts for neither."""
    times = (NOW - timedelta(hours=1), NOW - timedelta(days=2), NOW - timedelta(days=10))
    report = _report(_FakeReader(_current(), signal_times=times))

    assert (report.signals_24h, report.signals_7d) == (1, 2)


def test_missing_ledger_report_is_down_and_names_the_path() -> None:
    """No ledger means hermes never ran here; the reason says where it looked."""
    report = ledger_missing_report(NOW, UnitState.UNKNOWN, "/var/lib/hermes/hermes.sqlite3")

    assert report.verdict.level is VerdictLevel.DOWN
    assert "/var/lib/hermes/hermes.sqlite3" in report.verdict.reason


def test_open_entries_pass_through_unchanged() -> None:
    """The report shows exactly the entries the reader returns, entry facts only."""
    entry = ShadowEntry(pair=ETH, entry_time=LAST_4H, entry_price=Decimal("2618.45"))
    report = _report(_FakeReader(_current(), entries=(entry,)))

    assert report.open_entries == (entry,)


@given(minutes_after_close=st.integers(min_value=0, max_value=60 * 24 * 3))
def test_late_iff_now_is_past_due_plus_grace(minutes_after_close: int) -> None:
    """For any instant after the last close, the 4h stream is late exactly past due + grace."""
    now = LAST_4H + timedelta(minutes=minutes_after_close)
    closes = {(BTC, KlineInterval.H4): LAST_4H - _ONE_MS}
    report = build_report(
        _FakeReader(closes),
        pairs=(BTC,),
        intervals=(KlineInterval.H4,),
        now=now,
        settle=_SETTLE,
        grace=_GRACE,
        unit=UnitState.RUNNING,
    )
    due = LAST_4H + timedelta(hours=4) + _SETTLE

    assert (report.streams[0].state is StreamState.LATE) == (now > due + _GRACE)
    assert (report.verdict.level is VerdictLevel.LATE) == (now > due + _GRACE)


def _halt_at(at: datetime, kind: HaltKind = HaltKind.HALT) -> RiskHaltRecorded:
    """Return a value-free halt record at ``at``."""
    return RiskHaltRecorded(
        occurred_at=at,
        correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        strategy_id=StrategyId("swing-donchian-4h"),
        pair=BTC,
        kind=kind,
        rule="RO-04",
        reason="stale_orderbook",
        pre_registration_ref="§10",
    )


def test_halts_of_the_last_24_hours_are_counted_and_the_newest_is_kept() -> None:
    """Two halts inside 24 hours count 2; one 3 days old is the last only when alone (#749)."""
    newest = _halt_at(NOW - timedelta(hours=1), HaltKind.KILL)
    halts = (newest, _halt_at(NOW - timedelta(hours=20)), _halt_at(NOW - timedelta(days=3)))
    report = _report(_FakeReader(_current(), halts=halts))

    assert (report.halts_24h, report.last_halt) == (2, newest)


def test_a_halt_older_than_30_days_is_not_shown() -> None:
    """The lookback is the 30-day SA-9 window; nothing older reaches the report."""
    report = _report(_FakeReader(_current(), halts=(_halt_at(NOW - timedelta(days=31)),)))

    assert (report.halts_24h, report.last_halt) == (0, None)


def test_a_halt_leaves_the_verdict_unchanged() -> None:
    """Record-only: a halt is shown but does not turn the verdict late or down."""
    report = _report(_FakeReader(_current(), halts=(_halt_at(NOW - timedelta(hours=1)),)))

    assert report.verdict.level is VerdictLevel.OK


def _skew(severity: AnomalySeverity) -> AnomalyDetected:
    """Return a clock-skew anomaly serve recorded at 13:30 Madrid on 17 Oct."""
    return AnomalyDetected(
        occurred_at=datetime(2026, 10, 17, 11, 30, tzinfo=UTC),
        correlation_id="c-skew",
        source=CLOCK_SKEW_SOURCE,
        severity=severity,
        reason="the host clock is 300000 ms ahead of Binance (limit 5000 ms)",
    )


def test_a_clock_left_off_turns_the_status_red_with_the_fix() -> None:
    """Issue #859 item 2: an uncleared CRITICAL skew is a red verdict saying how to fix it."""
    report = _report(_FakeReader(_current(), clock_skew=_skew(AnomalySeverity.CRITICAL)))

    assert (report.verdict.level, report.verdict.headline, report.verdict.reason) == (
        VerdictLevel.DOWN,
        "The server clock is off",
        "Since 17 Oct 13:30, the host clock is 300000 ms ahead of Binance (limit 5000 ms).",
    )


def test_a_clock_back_on_time_reads_as_all_good() -> None:
    """Once serve records the return to exchange time, status is green again."""
    report = _report(_FakeReader(_current(), clock_skew=_skew(AnomalySeverity.INFO)))

    assert report.verdict.level is VerdictLevel.OK


def test_a_stopped_unit_outranks_an_off_clock() -> None:
    """A stopped hermes is the first thing to fix; the clock waits behind it."""
    report = _report(
        _FakeReader(_current(), clock_skew=_skew(AnomalySeverity.CRITICAL)),
        unit=UnitState.STOPPED,
    )

    assert (report.verdict.level, report.verdict.headline) == (
        VerdictLevel.DOWN,
        "hermes is down",
    )
