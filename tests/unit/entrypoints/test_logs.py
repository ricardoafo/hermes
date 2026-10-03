"""Tests for ``hermes logs`` (#376).

Invariants protected here:

- journalctl's JSON envelopes become ``HH:MM:SS  LEVEL  event  k=v  …corr`` lines,
  in journal order, with non-structlog messages passed through raw;
- ``--level`` keeps that level and worse; ``--cycle`` keeps one correlation id
  (full or tail) and drops raw lines;
- the journalctl command line is fixed (unit, JSON output, line count, since,
  follow), and ``1h`` becomes ``-1h``;
- no journalctl means exit 2 with a message; bad arguments exit 2;
- ``--json`` gives the same entries as data; no P&L word appears anywhere.

journalctl is replaced by a fake process: no real journal in unit tests.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from hermes.adapters.systemd_journal import JournalctlReader
from hermes.application.console.logs import LogLevel, filter_lines, parse_journal_entry
from hermes.entrypoints import cli
from hermes.entrypoints import logs as logs_module

_T0 = datetime(2026, 10, 3, 16, 0, 5, tzinfo=UTC)
_CORR_A = "01J9Z8T3M5BQR2FVKN4XK7Q2ZP"
_CORR_B = "01J9Z8T3M5BQR2FVKN4XH1M9XA"
_PNL_WORDS = re.compile(r"p&l|pnl|profit|equity|sharpe|drawdown|win rate", re.I)


def _envelope(message: str, seconds: int = 0) -> str:
    micros = int(_T0.timestamp() * 1_000_000) + seconds * 1_000_000
    return json.dumps({"__REALTIME_TIMESTAMP": str(micros), "MESSAGE": message})


def _structlog(level: str, event: str, corr: str | None = None, **fields: object) -> str:
    payload: dict[str, object] = {"event": event, "level": level, **fields}
    payload["timestamp"] = "2026-10-03T16:00:05Z"
    if corr is not None:
        payload["correlation_id"] = corr
    return json.dumps(payload)


_JOURNAL = [
    _envelope(_structlog("info", "serve.bar.processed", _CORR_A, pair="BTC/USDT", interval="4h")),
    _envelope(_structlog("warning", "feed.retry", _CORR_A, attempt=2), 60),
    _envelope("Started hermes.service - Hermes trading engine.", 120),
    _envelope(_structlog("error", "serve.notify.failed", _CORR_B), 180),
]


class _FakeProcess:
    """Stands in for subprocess.Popen: canned stdout, records termination."""

    def __init__(self, lines: list[str]) -> None:
        self.stdout: Iterator[str] = iter(line + "\n" for line in lines)
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


class _Spawn:
    """Records the argv and returns a fake process."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.argv: list[str] = []
        self.process: _FakeProcess | None = None

    def __call__(self, argv: list[str]) -> Any:
        self.argv = argv
        self.process = _FakeProcess(self.lines)
        return self.process


def _reader(lines: list[str] | None = None) -> tuple[JournalctlReader, _Spawn]:
    spawn = _Spawn(_JOURNAL if lines is None else lines)
    return JournalctlReader(executable="/usr/bin/journalctl", spawn=cast("Any", spawn)), spawn


def _run(args: list[str], lines: list[str] | None = None) -> tuple[int, str, _Spawn]:
    reader, spawn = _reader(lines)
    out = io.StringIO()
    code = logs_module.main(args, stream=out, reader=reader)
    return code, out.getvalue(), spawn


def test_lines_render_in_journal_order_with_corr_tail_and_raw_passthrough() -> None:
    """Each entry is one readable line; systemd's own message passes through raw."""
    code, out, spawn = _run(["--utc"])

    assert code == 0
    assert out.splitlines() == [
        "16:00:05  INFO     serve.bar.processed  pair=BTC/USDT interval=4h  …K7Q2ZP",
        "16:01:05  WARNING  feed.retry  attempt=2  …K7Q2ZP",
        "16:02:05           Started hermes.service - Hermes trading engine.",
        "16:03:05  ERROR    serve.notify.failed  …H1M9XA",
    ]
    assert spawn.process is not None and spawn.process.terminated


def test_times_default_to_madrid() -> None:
    """16:00 UTC in October is 18:00 in Madrid."""
    _, out, _ = _run([])

    assert out.splitlines()[0].startswith("18:00:05")


def test_level_keeps_that_level_and_worse() -> None:
    """--level warning drops info and raw lines (raw counts as info)."""
    _, out, _ = _run(["--level", "warning", "--utc"])

    assert [line.split()[1] for line in out.splitlines()] == ["WARNING", "ERROR"]


def test_cycle_keeps_one_correlation_id_by_tail() -> None:
    """--cycle K7Q2ZP keeps that bar cycle only; raw lines have no id and drop out."""
    _, out, _ = _run(["--cycle", "k7q2zp", "--utc"])

    assert [line.split()[2] for line in out.splitlines()] == ["serve.bar.processed", "feed.retry"]


def test_journalctl_command_line_is_fixed_and_relative_since_gets_a_minus() -> None:
    """Unit, JSON output, line count, --since -1h and --follow, as an argv list."""
    _, _, spawn = _run(["--last", "20", "--since", "1h", "-f"])

    assert spawn.argv == [
        "/usr/bin/journalctl",
        "--unit",
        "hermes.service",
        "--output",
        "json",
        "--no-pager",
        "--lines",
        "20",
        "--since",
        "-1h",
        "--follow",
    ]


def test_absolute_since_passes_through() -> None:
    """A timestamp goes to journalctl unchanged."""
    _, _, spawn = _run(["--since", "2026-10-03 16:00"])

    assert spawn.argv[-2:] == ["--since", "2026-10-03 16:00"]


def test_json_mode_gives_the_entries_as_data() -> None:
    """--json prints one object per entry with the logged fields."""
    _, out, _ = _run(["--json"])
    first = json.loads(out.splitlines()[0])

    assert first == {
        "at": "2026-10-03T16:00:05+00:00",
        "level": "info",
        "event": "serve.bar.processed",
        "fields": {"pair": "BTC/USDT", "interval": "4h"},
        "correlation_id": _CORR_A,
    }


def test_no_journalctl_exits_two_with_a_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a dev box without systemd the command says so instead of crashing."""
    monkeypatch.setattr("hermes.adapters.systemd_journal.journalctl.shutil.which", lambda _: None)
    out = io.StringIO()

    code = logs_module.main([], stream=out, reader=JournalctlReader())

    assert code == 2
    assert "journalctl is not available here" in out.getvalue()


@pytest.mark.parametrize("args", [["--nope"], ["--level", "loud"], ["--last", "0"]])
def test_bad_arguments_exit_two(args: list[str]) -> None:
    """Unknown flags, unknown levels and an empty window are usage errors."""
    code, out, _ = _run(args)

    assert code == 2
    assert "Usage: hermes logs" in out


def test_output_carries_no_pnl_words() -> None:
    """The console stays blind: nothing outcome-related reaches the screen."""
    _, out, _ = _run(["--utc"])

    assert _PNL_WORDS.findall(out) == []


def test_logs_is_a_registered_command() -> None:
    """`hermes logs --help` dispatches through the top-level CLI."""
    out = io.StringIO()

    assert cli.main(["logs", "--help"], stream=out) == 0
    assert out.getvalue().startswith("Usage: hermes logs")


def test_non_envelope_lines_are_skipped() -> None:
    """Non-JSON and non-object lines are ignored; an empty envelope is an empty raw line."""
    parsed = [parse_journal_entry(text) for text in ("-- No entries --", "[1, 2]", "{}")]

    assert [None if p is None else (p.raw, p.event) for p in parsed] == [None, None, ("", "")]


def test_level_filter_treats_unknown_levels_as_info() -> None:
    """A line with an odd level still counts, as info."""
    line = parse_journal_entry(_envelope(_structlog("notice", "x.y")))
    assert line is not None

    assert [entry.event for entry in filter_lines([line], min_level=LogLevel.INFO)] == ["x.y"]
