"""Unit tests for the journal-to-feed helpers (#653).

Invariants:

- folding never loses a coin: the folded lines' ``count`` sums to the bar rows in;
- rows that are not bar checks or signals pass through folding untouched;
- a signal with no bar beside it stays a row of its own;
- a journal line below the window is ignored;
- a bar line without the entry fields adds no counts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.journal_activity import fold_checks, journal_activity
from hermes.application.console.logs import LogLine
from hermes.application.console.views import ActivityItem, ActivityKind

_AT = datetime(2026, 10, 3, 12, tzinfo=UTC)


def _bar(at: datetime, coin: str = "BTC") -> ActivityItem:
    return ActivityItem(at=at, kind=ActivityKind.BAR, coin=coin, text="4h check")


@given(coins=st.lists(st.integers(1, 6), min_size=1, max_size=8))
def test_folded_counts_add_up_to_the_bar_rows(coins: list[int]) -> None:
    """INV: the sum of folded ``count`` equals the number of bar rows, whatever the candles."""
    items = [_bar(_AT + timedelta(hours=4 * n), f"C{i}") for i, n in enumerate(coins)]
    folded = fold_checks(items)
    assert sum(i.count for i in folded) == len(items)
    assert all(i.kind is ActivityKind.BAR for i in folded)


def test_other_rows_pass_through_and_a_lone_signal_stays() -> None:
    """INV: opened rows and a signal with no bar beside it are not touched by folding."""
    opened = ActivityItem(at=_AT, kind=ActivityKind.OPENED, coin="BTC")
    signal = ActivityItem(at=_AT, kind=ActivityKind.SIGNAL, coin="ETH", text="exit long")
    assert fold_checks([opened, signal]) == (opened, signal)


def test_lines_before_the_window_are_ignored() -> None:
    """INV: a restart older than ``since`` produces no row."""
    old = LogLine(at=_AT - timedelta(days=9), event="serve.run.started", fields=(("release", "a"),))
    assert journal_activity([old], since=_AT - timedelta(days=7)).items == ()


def test_a_bar_line_without_entry_fields_adds_no_counts() -> None:
    """INV: a daily or in-position bar line carries no entry fields and counts nothing."""
    line = LogLine(
        at=_AT,
        event="serve.bar.processed",
        fields=(("pair", "BTC/USDT"), ("interval", "4h"), ("close_time", _AT.isoformat())),
    )
    assert dict(journal_activity([line]).entry_counts) == {}
