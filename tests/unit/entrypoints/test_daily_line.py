"""Tests for the one-line daily summary (``hermes status --summary``, #373).

Invariants protected here:

- the line carries bars per interval in 24 h, signals, the next 4h bar in UTC
  and Madrid, error and warning counts, and restarts, in that order;
- non-zero error and warning counts name their most frequent events;
- a late or down verdict replaces "alive" with the verdict headline;
- only hermes's JSON journal lines count, by level;
- no P&L word ever appears.
"""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path

from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.application.status import StatusReport, UnitState, build_report
from hermes.domain.strategy.signal import SignalKind
from hermes.entrypoints import status as status_module
from hermes.entrypoints.daily_line import JournalCounts, count_journal, daily_line
from tests.support.paths import REPO_ROOT
from tests.support.status_ledger import (
    ETH,
    INTERVALS,
    LAST_4H,
    NOW,
    PAIRS,
    signal,
    write_ledger,
)

_CONFIG = REPO_ROOT / "config"
_PNL_WORDS = re.compile(
    r"p&l|pnl|profit|loss|equity|sharpe|drawdown|win rate|return", re.IGNORECASE
)


def _report(tmp_path: Path, *, now=NOW, unit: UnitState = UnitState.RUNNING) -> StatusReport:
    """Build a report over a fixture ledger: every stream current, one ETH entry."""
    path = write_ledger(
        tmp_path / "hermes.sqlite3",
        signals=(signal(ETH, SignalKind.ENTRY, LAST_4H - timedelta(hours=4)),),
    )
    with SqliteStatusReader.open(path) as reader:
        return build_report(
            reader,
            pairs=PAIRS,
            intervals=INTERVALS,
            now=now,
            settle=timedelta(seconds=5),
            grace=timedelta(seconds=900),
            unit=unit,
        )


def _json(level: str, event: str) -> str:
    """Return one structlog JSON line."""
    return json.dumps({"event": event, "level": level, "timestamp": "2026-10-03T12:00:00Z"})


def test_all_good_line_lists_every_fact_in_order(tmp_path: Path) -> None:
    """The quiet-day line: alive, bars, signals, next 4h bar, zero errors, warnings, restarts."""
    line = daily_line(_report(tmp_path), JournalCounts(), restarts=0)

    assert line == (
        "hermes alive · 3x4h + 3x1d bars in 24 h · 1 signal"
        " · next 4h bar 20:00 UTC (22:00 Madrid)"
        " · 0 errors · 0 warnings · 0 restarts"
    )


def test_non_zero_counts_name_their_top_events(tmp_path: Path) -> None:
    """Errors and warnings name the most frequent events; plurals follow the counts."""
    counts = JournalCounts(
        errors=Counter({"serve.run.failed": 1}),
        warnings=Counter({"serve.notify.failed": 2, "feed.retry": 1}),
    )

    line = daily_line(_report(tmp_path), counts, restarts=1)

    assert line.endswith(
        " · 1 error (serve.run.failed x1)"
        " · 3 warnings (serve.notify.failed x2, feed.retry x1)"
        " · 1 restart"
    )


def test_a_late_check_replaces_alive_with_the_verdict(tmp_path: Path) -> None:
    """A late day must not read as "alive"."""
    late = _report(tmp_path, now=LAST_4H + timedelta(hours=4, minutes=30))

    line = daily_line(late, JournalCounts(), restarts=0)

    assert line.startswith("hermes LATE: The 22:00 check is 29 minutes late · ")


def test_a_down_unit_leads_with_down(tmp_path: Path) -> None:
    """A failed unit leads the line with DOWN."""
    line = daily_line(_report(tmp_path, unit=UnitState.FAILED), JournalCounts(), restarts=5)

    assert line.startswith("hermes DOWN: hermes is down · ")


def test_count_journal_counts_json_lines_by_level_and_skips_the_rest() -> None:
    """Only hermes's JSON lines count; systemd text and tracebacks are ignored."""
    lines = [
        _json("error", "serve.run.failed"),
        _json("critical", "serve.run.failed"),
        _json("warning", "serve.notify.failed"),
        _json("info", "serve.bar.processed"),
        "Started hermes.service - Hermes trading engine.",
        "Traceback (most recent call last):",
        "[1, 2]",
    ]

    counts = count_journal(lines)

    assert (counts.errors, counts.warnings) == (
        Counter({"serve.run.failed": 2}),
        Counter({"serve.notify.failed": 1}),
    )


def test_summary_cli_reads_the_journal_from_stdin_and_takes_restarts(tmp_path: Path) -> None:
    """`hermes status --summary --journal - --restarts 2` prints one line."""
    path = write_ledger(tmp_path / "hermes.sqlite3")
    out = io.StringIO()
    stdin = io.StringIO(_json("warning", "serve.notify.failed") + "\n")

    code = status_module.main(
        [
            "--config-dir",
            str(_CONFIG),
            "--db",
            str(path),
            "--summary",
            "--journal",
            "-",
            "--restarts",
            "2",
        ],
        stream=out,
        now=NOW,
        unit_state=lambda: UnitState.RUNNING,
        stdin=stdin,
    )

    assert code == 0
    assert out.getvalue().splitlines() == [
        "hermes alive · 3x4h + 3x1d bars in 24 h · 0 signals"
        " · next 4h bar 20:00 UTC (22:00 Madrid)"
        " · 0 errors · 1 warning (serve.notify.failed x1) · 2 restarts"
    ]


def test_summary_line_never_carries_pnl_words(tmp_path: Path) -> None:
    """Blind until certification: the daily line names no outcome figure."""
    counts = JournalCounts(errors=Counter({"x": 1}), warnings=Counter({"y": 1}))

    line = daily_line(_report(tmp_path), counts, restarts=3)

    assert _PNL_WORDS.findall(line) == []
