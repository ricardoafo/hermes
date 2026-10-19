"""The one-line daily "hermes is alive" summary (#373).

``hermes status --summary`` prints it; ``deploy/hermes-daily-line.sh`` sends
it to Slack once a day. It reads the same :class:`StatusReport` as every
other console surface and adds the last 24 hours of journal health:

    hermes alive · 18x4h + 3x1d bars in 24 h · 0 signals ·
    next 4h bar 20:00 UTC (22:00 Madrid) · 0 errors · 2 warnings
    (serve.notify.failed x2) · 0 restarts

(one line; wrapped here). Non-zero error and warning counts name their most
frequent events. A late or down verdict replaces "alive" and leads with the
verdict headline. No P&L, ever.

Reference: openspec/archive/373-daily-alive-line/change.md.
"""

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

from hermes.application.status import MADRID, StatusReport, VerdictLevel
from hermes.domain.market_data.kline import KlineInterval

__all__ = ["JournalCounts", "count_journal", "daily_line"]

_TOP_EVENTS: Final = 3
_SEP: Final = " · "


@dataclass(frozen=True, slots=True)
class JournalCounts:
    """Error and warning events seen in the journal window.

    Attributes:
        errors: Event name to count for ``level=error`` (and critical) lines.
        warnings: Event name to count for ``level=warning`` lines.
    """

    errors: Counter[str] = field(default_factory=Counter)
    warnings: Counter[str] = field(default_factory=Counter)


def count_journal(lines: Iterable[str]) -> JournalCounts:
    """Count error and warning events in hermes's JSON journal lines.

    Prod logs are one JSON object per line (structlog ``JSONRenderer``,
    ADR-008 §1). Lines that are not JSON objects (systemd's own messages,
    tracebacks) are skipped.

    Args:
        lines: Journal lines, e.g. ``journalctl -u hermes.service -o cat``.

    Returns:
        Per-level event counts.
    """
    errors: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        level = str(entry.get("level", "")).lower()
        event = str(entry.get("event", "?"))
        if level in {"error", "critical"}:
            errors[event] += 1
        elif level == "warning":
            warnings[event] += 1
    return JournalCounts(errors=errors, warnings=warnings)


def daily_line(report: StatusReport, counts: JournalCounts, restarts: int) -> str:
    """Return the one-line daily summary of ``report``.

    Args:
        report: The status report at send time.
        counts: Journal errors and warnings over the same 24 hours.
        restarts: How many times systemd restarted hermes.service in them.

    Returns:
        The line, without a trailing newline.
    """
    if report.verdict.level is VerdictLevel.OK:
        head = "hermes alive"
    else:
        head = f"hermes {report.verdict.level.value.upper()}: {report.verdict.headline}"
    parts = [
        head,
        _bars(report),
        f"{report.signals_24h} signal{'s' if report.signals_24h != 1 else ''}",
    ]
    nxt = _next_4h(report)
    if nxt:
        parts.append(nxt)
    parts.append(_level("error", counts.errors))
    parts.append(_level("warning", counts.warnings))
    parts.append(f"{restarts} restart{'s' if restarts != 1 else ''}")
    return _SEP.join(parts)


def _bars(report: StatusReport) -> str:
    """Return ``18x4h + 3x1d bars in 24 h``."""
    counted = " + ".join(f"{n}x{interval.value}" for interval, n in report.bars_24h)
    return f"{counted or '0'} bars in 24 h"


def _next_4h(report: StatusReport) -> str:
    """Return when the next 4h bar closes, in UTC and Madrid, or ``""``."""
    closes = [
        s.next_close
        for s in report.streams
        if s.interval is KlineInterval.H4 and s.next_close is not None
    ]
    if not closes:
        return ""
    at = min(closes)
    return f"next 4h bar {at:%H:%M} UTC ({at.astimezone(MADRID):%H:%M} Madrid)"


def _level(name: str, events: Counter[str]) -> str:
    """Return ``0 errors`` or ``2 errors (serve.notify.failed x2)``."""
    total = sum(events.values())
    label = f"{total} {name}{'s' if total != 1 else ''}"
    if not total:
        return label
    top = ", ".join(f"{event} x{n}" for event, n in events.most_common(_TOP_EVENTS))
    return f"{label} ({top})"
