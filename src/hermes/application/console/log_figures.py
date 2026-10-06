"""The Logs screen's figures, computed from journal lines (issue #716).

Everything the Logs screen shows besides the lines themselves is computed
here, from :class:`~hermes.application.console.logs.LogLine` values, with no
I/O and no AI:

- :func:`log_query_rows` turns lines into rows for the shared query list
  (:mod:`hermes.application.console.query`): ``time``, ``level``, ``service``,
  ``event``, ``coin``, ``details`` and ``trace`` columns plus the ``year`` /
  ``month`` / ``day`` facets and ``at`` for ``since:``;
- :func:`volume_buckets` counts lines per level in equal time buckets over a
  window, for the volume histogram;
- :func:`trace_tree` gathers every line of one correlation id into a tree
  grouped by event namespace, with each line's offset from the first line and
  the gap since the line before;
- :func:`append_new_lines` merges a fresh journal read into the lines already
  shown, for follow mode;
- :func:`log_line_pretty_json` is the selected line as indented JSON.

The journal can hold money figures. These functions only reshape what hermes
logged for the operator's own screen; nothing here is sent anywhere.

Reference: openspec/changes/716-logs-screen/change.md.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Final

from hermes.application.console.logs import LogLine, log_line_to_json
from hermes.application.console.query import AT_FIELD, LEVEL_FIELD, LEVEL_ORDER, Row, level_rank

__all__ = [
    "LOG_FACETS",
    "LOG_QUERY_FIELDS",
    "TRACE_TAIL",
    "TraceGroup",
    "TraceStep",
    "TraceTree",
    "VolumeBucket",
    "append_new_lines",
    "line_coin",
    "log_line_pretty_json",
    "log_query_rows",
    "trace_tree",
    "volume_buckets",
]

LOG_QUERY_FIELDS: Final = ("year", "month", "day", "level", "service", "event", "coin", "trace")
LOG_FACETS: Final = ("level", "service", "coin")
TRACE_TAIL: Final = 6
_RAW_LEVEL: Final = "info"
_QUOTES: Final = ("USDT", "USDC", "EUR", "USD")
_RAW_NAMESPACE: Final = "raw"


def line_coin(line: LogLine) -> str | None:
    """Return the coin a line is about, from its ``coin``, ``pair`` or ``symbol`` field.

    Args:
        line: A journal line.

    Returns:
        The coin in upper case (``BTC`` from ``pair=BTC/USDT`` or
        ``symbol=BTCUSDT``), or ``None`` when the line names none.
    """
    fields = dict(line.fields)
    if fields.get("coin"):
        return fields["coin"].upper()
    pair = fields.get("pair")
    if pair and "/" in pair:
        return pair.partition("/")[0].upper() or None
    symbol = (fields.get("symbol") or pair or "").upper()
    for quote in _QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol.removesuffix(quote)
    return None


def _details(line: LogLine) -> str:
    if line.raw is not None:
        return line.raw
    return " ".join(f"{key}={value}" for key, value in line.fields)


def log_query_rows(lines: Iterable[LogLine], tz: tzinfo) -> tuple[dict[str, object], ...]:
    """Return journal lines as rows for the shared query list, in journal order.

    Args:
        lines: Parsed journal lines.
        tz: Timezone for the ``time`` column and the date facets.

    Returns:
        One mapping per line: ``at`` (UTC, for ``since:``), ``time``
        (``MM-DD HH:MM:SS`` local), ``year``, ``month``, ``day``, ``level``
        (raw lines count as ``info``), ``service``, ``event``, ``coin``,
        ``details`` (``key=value`` pairs, or the raw text) and ``trace`` (the
        correlation id's last six characters, as ``hermes logs`` shows it).
        A field the line does not carry is ``None``.
    """
    rows: list[dict[str, object]] = []
    for line in lines:
        local = line.at.astimezone(tz)
        corr = line.correlation_id
        rows.append(
            {
                AT_FIELD: line.at,
                "time": f"{local:%m-%d %H:%M:%S}",
                "year": local.year,
                "month": f"{local:%Y-%m}",
                "day": local.date().isoformat(),
                LEVEL_FIELD: line.level or _RAW_LEVEL,
                "service": line.service,
                "event": line.event or None,
                "coin": line_coin(line),
                "details": _details(line) or None,
                "trace": corr[-TRACE_TAIL:] if corr else None,
            }
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class VolumeBucket:
    """How many lines of each level fall in one slice of the window.

    Attributes:
        start: The bucket's first instant (inclusive).
        counts: Lines per level, in :data:`~hermes.application.console.query.LEVEL_ORDER`.
    """

    start: datetime
    counts: tuple[int, ...]

    @property
    def total(self) -> int:
        """Return the lines in this bucket, every level together."""
        return sum(self.counts)


def volume_buckets(
    rows: Iterable[Row], *, end: datetime, window: timedelta, count: int
) -> tuple[VolumeBucket, ...]:
    """Count rows per level in ``count`` equal buckets over ``window`` ending at ``end``.

    Args:
        rows: Rows with ``at`` and ``level`` (see :func:`log_query_rows`).
        end: The window's last instant; a row exactly at ``end`` lands in the
            last bucket.
        window: The window's length.
        count: How many buckets; at least one.

    Returns:
        ``count`` buckets, oldest first. Rows outside the window are not
        counted; an unknown level counts as ``info``.
    """
    count = max(count, 1)
    start = end - window
    width = window / count
    counts = [[0] * len(LEVEL_ORDER) for _ in range(count)]
    info = LEVEL_ORDER.index(_RAW_LEVEL)
    for row in rows:
        at = row.get(AT_FIELD)
        if not isinstance(at, datetime) or at < start or at > end:
            continue
        index = min(int((at - start) / width), count - 1)
        rank = level_rank(row.get(LEVEL_FIELD))
        counts[index][info if rank is None else rank] += 1
    return tuple(
        VolumeBucket(start + width * index, tuple(level)) for index, level in enumerate(counts)
    )


@dataclass(frozen=True, slots=True)
class TraceStep:
    """One line of a trace.

    Attributes:
        line: The journal line.
        offset: Time since the trace's first line.
        gap: Time since the line before it in the trace (zero for the first).
    """

    line: LogLine
    offset: timedelta
    gap: timedelta


@dataclass(frozen=True, slots=True)
class TraceGroup:
    """The lines of one event namespace (``serve`` for ``serve.bar.processed``).

    Attributes:
        name: The namespace.
        steps: Its lines, in time order.
    """

    name: str
    steps: tuple[TraceStep, ...]

    @property
    def offset(self) -> timedelta:
        """Return when the group's first line came, from the trace's start."""
        return self.steps[0].offset

    @property
    def span(self) -> timedelta:
        """Return the time from the group's first line to its last."""
        return self.steps[-1].offset - self.steps[0].offset


@dataclass(frozen=True, slots=True)
class TraceTree:
    """Every line of one correlation id, as a tree with timings.

    Attributes:
        correlation_id: The full correlation id.
        started: The first line's time.
        groups: One group per event namespace, in order of first appearance.
    """

    correlation_id: str
    started: datetime
    groups: tuple[TraceGroup, ...]

    @property
    def count(self) -> int:
        """Return how many lines the trace holds."""
        return sum(len(group.steps) for group in self.groups)

    @property
    def span(self) -> timedelta:
        """Return the time from the trace's first line to its last."""
        return max(group.steps[-1].offset for group in self.groups)


def _namespace(line: LogLine) -> str:
    return line.event.partition(".")[0] if line.event else _RAW_NAMESPACE


def trace_tree(lines: Iterable[LogLine], correlation_id: str) -> TraceTree | None:
    """Gather every line of one correlation id into a tree with timings.

    Args:
        lines: Journal lines, any order.
        correlation_id: The full id to gather.

    Returns:
        The tree, lines in time order (journal order on equal times), grouped
        by event namespace; ``None`` when no line carries the id.
    """
    ordered = sorted(
        (line for line in lines if line.correlation_id == correlation_id), key=lambda x: x.at
    )
    if not ordered:
        return None
    started = ordered[0].at
    groups: dict[str, list[TraceStep]] = {}
    previous = started
    for line in ordered:
        step = TraceStep(line, line.at - started, line.at - previous)
        groups.setdefault(_namespace(line), []).append(step)
        previous = line.at
    return TraceTree(
        correlation_id,
        started,
        tuple(TraceGroup(name, tuple(steps)) for name, steps in groups.items()),
    )


def append_new_lines(
    shown: Sequence[LogLine], fresh: Iterable[LogLine], *, keep: int
) -> tuple[LogLine, ...]:
    """Append the lines of a fresh journal read that are not shown yet (follow mode).

    Args:
        shown: The lines shown now, oldest first.
        fresh: The last lines of the journal, oldest first.
        keep: The most lines to hold; the oldest go first.

    Returns:
        ``shown`` followed by the fresh lines not already in it and no older
        than its last line, trimmed to the newest ``keep``.
    """
    seen = set(shown)
    last = shown[-1].at if shown else None
    new = [line for line in fresh if line not in seen and (last is None or line.at >= last)]
    merged = (*shown, *new)
    return merged[-keep:] if keep > 0 else ()


def log_line_pretty_json(line: LogLine) -> str:
    """Return ``line`` as indented JSON, the way ``hermes logs --json`` holds it.

    Args:
        line: The journal line.

    Returns:
        Two-space indented JSON, non-ASCII kept as is.
    """
    return json.dumps(log_line_to_json(line), indent=2, ensure_ascii=False)
