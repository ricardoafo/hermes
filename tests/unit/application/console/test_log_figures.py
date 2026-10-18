"""Tests for the Logs screen's figures (issue #716).

Invariants protected here:

- every journal line becomes one query row with the time, level, service,
  event, coin, details and trace columns; raw lines count as ``info``;
- the service comes from the journal's systemd unit (``hermes.service`` is
  the ``engine``), the coin from ``coin``, ``pair`` or ``symbol``;
- the shared query language filters these rows (``level>=warn service:engine
  coin:ETH since:7d``) and facet counts are exact;
- volume buckets split the window evenly, count each line once in its level,
  and leave out lines outside the window;
- a trace gathers exactly the lines of one correlation id, grouped by event
  namespace, with offsets from the first line and gaps between lines;
- follow mode appends only lines not shown yet, never reorders the shown
  ones, and keeps at most the newest ``keep`` lines;
- the selected line's JSON is ``hermes logs --json`` data, indented.

The fixture is a journalctl JSON export (tests/fixtures/journal/logs_screen.jsonl).
"""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.log_figures import (
    LOG_QUERY_FIELDS,
    append_new_lines,
    line_coin,
    log_line_pretty_json,
    log_query_rows,
    trace_tree,
    volume_buckets,
)
from hermes.application.console.logs import LogLine, parse_journal_entry, service_name
from hermes.application.console.query import (
    FacetCount,
    facet_counts,
    filter_rows,
    parse_query,
)
from tests.support.paths import REPO_ROOT

_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "journal" / "logs_screen.jsonl"
_MADRID = ZoneInfo("Europe/Madrid")
_NOW = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_TRACE_A = "01J9Z8T3M5BQR2FVKN4XK7Q2ZP"
_TRACE_B = "01J9Z8T3M5BQR2FVKN4XH1M9XA"


def _lines() -> list[LogLine]:
    raw = _FIXTURE.read_text(encoding="utf-8").splitlines()
    return [line for line in map(parse_journal_entry, raw) if line is not None]


def _rows() -> tuple[dict[str, object], ...]:
    return log_query_rows(_lines(), _MADRID)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def test_every_fixture_line_becomes_a_row() -> None:
    """The twelve journal entries give twelve rows, in journal order."""
    assert [row["event"] for row in _rows()][:4] == [
        "serve.bar.processed",
        None,
        "ws.heartbeat",
        "serve.bar.started",
    ]
    assert len(_rows()) == 12


def test_a_row_carries_the_seven_columns() -> None:
    """The feed retry row reads time, level, service, event, coin, details, trace."""
    row = _rows()[6]
    assert {key: row[key] for key in ("time", "level", "service", "event", "coin")} == {
        "time": "10-03 10:00:06",
        "level": "warning",
        "service": "engine",
        "event": "feed.retry",
        "coin": "ETH",
    }
    assert (row["details"], row["trace"]) == ("pair=ETH/USDT attempt=2", "K7Q2ZP")


def test_a_raw_systemd_line_counts_as_info_with_its_text() -> None:
    """The systemd line has no event or trace, level info, and its text as details."""
    row = _rows()[1]
    assert (row["level"], row["event"], row["trace"], row["service"], row["details"]) == (
        "info",
        None,
        None,
        "engine",
        "Started hermes.service - Hermes trading engine.",
    )


def test_the_date_facets_use_the_display_timezone() -> None:
    """A 22:00 UTC line on 2 October is 3 October in Madrid (UTC+2)."""
    line = LogLine(at=datetime(2026, 10, 2, 22, 30, tzinfo=UTC), level="info", event="x")
    row = log_query_rows([line], _MADRID)[0]
    assert (row["year"], row["month"], row["day"], row["time"]) == (
        2026,
        "2026-10",
        "2026-10-03",
        "10-03 00:30:00",
    )


def test_service_names_come_from_the_unit() -> None:
    """hermes.service is the engine; other hermes units drop their prefix and suffix."""
    assert [
        service_name(unit)
        for unit in (
            "hermes.service",
            "hermes-bot.service",
            "hermes-notify-failure@hermes.service",
            "init.scope",
        )
    ] == ["engine", "bot", "notify-failure", "init"]


def test_the_bot_line_names_the_bot_service() -> None:
    """The slow-command warning came from hermes-bot.service."""
    assert _rows()[8]["service"] == "bot"


def test_coin_comes_from_coin_pair_or_symbol() -> None:
    """coin=, pair=BTC/USDT and symbol=ETHUSDT each name a coin; other fields name none."""
    lines = [
        LogLine(at=_NOW, event="e", fields=(("coin", "sol"),)),
        LogLine(at=_NOW, event="e", fields=(("pair", "BTC/USDT"),)),
        LogLine(at=_NOW, event="e", fields=(("symbol", "ETHUSDT"),)),
        LogLine(at=_NOW, event="e", fields=(("channel", "slack"),)),
        LogLine(at=_NOW, event="e", fields=(("symbol", "USDT"),)),
    ]
    assert [line_coin(line) for line in lines] == ["SOL", "BTC", "ETH", None, None]


# ---------------------------------------------------------------------------
# Query and facets
# ---------------------------------------------------------------------------


def test_the_spec_query_keeps_engine_warnings_about_eth_this_week() -> None:
    """``level>=warn service:engine coin:ETH since:7d`` keeps the one feed retry."""
    query = parse_query("level>=warn service:engine coin:ETH since:7d", LOG_QUERY_FIELDS)
    kept = filter_rows(_rows(), query, now=_NOW)
    assert [(row["event"], row["trace"]) for row in kept] == [("feed.retry", "K7Q2ZP")]


def test_a_trace_query_keeps_one_cycle() -> None:
    """``trace:H1M9XA`` keeps the three lines of the 12:00 cycle."""
    kept = filter_rows(_rows(), parse_query("trace:h1m9xa", LOG_QUERY_FIELDS), now=_NOW)
    assert [row["event"] for row in kept] == [
        "serve.bar.started",
        "serve.notify.failed",
        "serve.bar.processed",
    ]


def test_facet_counts_for_level_service_and_coin() -> None:
    """The fixture holds 8 info, 2 warning, 1 debug, 1 error; 11 engine, 1 bot; 4 BTC."""
    rows = _rows()
    assert facet_counts(rows, "level") == (
        FacetCount("info", 8),
        FacetCount("warning", 2),
        FacetCount("debug", 1),
        FacetCount("error", 1),
    )
    assert facet_counts(rows, "service") == (FacetCount("engine", 11), FacetCount("bot", 1))
    assert facet_counts(rows, "coin") == (
        FacetCount("BTC", 4),
        FacetCount("ETH", 3),
        FacetCount("SOL", 2),
    )


# ---------------------------------------------------------------------------
# Volume histogram
# ---------------------------------------------------------------------------


def test_day_window_buckets_count_each_line_once() -> None:
    """Over the last 24 h in 4 buckets of 6 h, the lines land where their time says."""
    buckets = volume_buckets(_rows(), end=_NOW, window=timedelta(days=1), count=4)
    starts = [b.start for b in buckets]
    assert starts == [
        datetime(2026, 10, 2, 12, 12, tzinfo=UTC),
        datetime(2026, 10, 2, 18, 12, tzinfo=UTC),
        datetime(2026, 10, 3, 0, 12, tzinfo=UTC),
        datetime(2026, 10, 3, 6, 12, tzinfo=UTC),
    ]
    # debug, info, warning, error, critical
    assert [b.counts for b in buckets] == [
        (0, 1, 0, 0, 0),
        (1, 0, 0, 0, 0),
        (0, 0, 0, 0, 0),
        (0, 6, 2, 1, 0),
    ]


def test_lines_outside_the_window_are_not_counted() -> None:
    """The 28 September line is outside a 1-day window and inside a 7-day one."""
    day = volume_buckets(_rows(), end=_NOW, window=timedelta(days=1), count=1)
    week = volume_buckets(_rows(), end=_NOW, window=timedelta(days=7), count=1)
    assert (day[0].total, week[0].total) == (11, 12)


def test_a_line_at_the_window_end_lands_in_the_last_bucket() -> None:
    """A row exactly at ``end`` counts in the last bucket, not past it."""
    row = {"at": _NOW, "level": "error"}
    buckets = volume_buckets([row], end=_NOW, window=timedelta(hours=4), count=4)
    assert [b.total for b in buckets] == [0, 0, 0, 1]


@given(
    st.lists(st.integers(0, 24 * 3600), max_size=40),
    st.integers(1, 30),
)
def test_buckets_never_lose_or_invent_a_line(seconds: list[int], count: int) -> None:
    """Every row inside the window is counted exactly once, whatever the bucket count."""
    rows = [{"at": _NOW - timedelta(seconds=s), "level": "info"} for s in seconds]
    buckets = volume_buckets(rows, end=_NOW, window=timedelta(days=1), count=count)
    assert (len(buckets), sum(b.total for b in buckets)) == (count, len(seconds))


# ---------------------------------------------------------------------------
# Trace tree
# ---------------------------------------------------------------------------


def test_a_trace_holds_exactly_its_lines_grouped_by_namespace() -> None:
    """The 08:00 cycle groups its five lines by namespace in order of first appearance."""
    tree = trace_tree(_lines(), _TRACE_A)
    assert tree is not None
    assert [(g.name, [s.line.event for s in g.steps]) for g in tree.groups] == [
        ("serve", ["serve.bar.started", "serve.bar.processed"]),
        ("market", ["market.candles.fetched"]),
        ("signal", ["signal.checked"]),
        ("feed", ["feed.retry"]),
    ]
    assert tree.count == 5


def test_trace_timings_are_offsets_and_gaps() -> None:
    """Offsets run from the first line; gaps from the line before; span is first to last."""
    tree = trace_tree(_lines(), _TRACE_A)
    assert tree is not None
    serve = tree.groups[0]
    assert [(s.offset, s.gap) for s in serve.steps] == [
        (timedelta(0), timedelta(0)),
        (timedelta(seconds=2.4), timedelta(seconds=1.3)),
    ]
    assert (tree.span, serve.span, tree.groups[3].offset) == (
        timedelta(seconds=2.4),
        timedelta(seconds=2.4),
        timedelta(seconds=1.1),
    )


def test_an_unknown_trace_is_none() -> None:
    """No line carries the id: there is no tree."""
    found = [trace_tree(_lines(), corr) for corr in ("01J9Z8T3M5BQR2FVKN4XNOPE00", _TRACE_B)]
    assert [None if tree is None else tree.count for tree in found] == [None, 3]


def test_a_trace_starts_at_its_own_first_line() -> None:
    """The 12:00 cycle starts at 12:00:04 and spans 5 s."""
    tree = trace_tree(_lines(), _TRACE_B)
    assert tree is not None
    assert (tree.started, tree.span, tree.count) == (
        datetime(2026, 10, 3, 12, 0, 4, tzinfo=UTC),
        timedelta(seconds=5),
        3,
    )


# ---------------------------------------------------------------------------
# Follow
# ---------------------------------------------------------------------------


def test_follow_appends_only_new_lines() -> None:
    """A re-read overlapping the shown lines appends the two new ones, in order."""
    lines = _lines()
    merged = append_new_lines(lines[:10], lines[6:], keep=100)
    assert merged == tuple(lines)


def test_follow_drops_the_oldest_beyond_keep() -> None:
    """With keep=5 only the newest five lines stay."""
    lines = _lines()
    assert append_new_lines(lines[:8], lines[8:], keep=5) == tuple(lines[7:])


def test_follow_ignores_lines_older_than_the_last_shown() -> None:
    """A line older than the newest shown one is not appended (no reordering)."""
    lines = _lines()
    assert append_new_lines(lines[5:], lines[:2], keep=100) == tuple(lines[5:])


@given(st.integers(0, 12), st.integers(0, 12))
def test_follow_never_duplicates_a_line(split: int, overlap: int) -> None:
    """Appending any overlapping re-read yields the journal once, in journal order."""
    lines = _lines()
    start = max(split - overlap, 0)
    assert append_new_lines(lines[:split], lines[start:], keep=100) == tuple(lines)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_pretty_json_is_the_logs_json_indented() -> None:
    """The selected line reads as indented ``hermes logs --json`` data with its service."""
    text = log_line_pretty_json(_lines()[6])
    assert json.loads(text) == {
        "at": "2026-10-03T08:00:06.100000+00:00",
        "service": "engine",
        "level": "warning",
        "event": "feed.retry",
        "fields": {"pair": "ETH/USDT", "attempt": "2"},
        "correlation_id": _TRACE_A,
    }
    assert text.splitlines()[1] == '  "at": "2026-10-03T08:00:06.100000+00:00",'
