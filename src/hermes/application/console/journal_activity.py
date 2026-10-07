"""Activity rows read from the journal, and the one-line 4h check (#653).

The ledger knows bar checks and signals; restarts, warnings and errors live
only in the journal. This module turns parsed :class:`LogLine` values into
feed rows, read-only and without a new service:

- ``serve.run.started`` is a restart row that names the release; when the
  release differs from the previous start's, the row reads ``deployed`` and
  names the release it replaced;
- warning, error and critical lines become warning and error rows showing the
  event name, the coin and the ``error`` class only. No other field is copied,
  so a price, a size or a balance logged next to a warning never reaches the
  feed (ADR-008, ADR-012);
- ``serve.bar.processed`` lines contribute yes/no entry-condition counts to the
  matching check line, never a price or a distance (ADR-012 D-06).

:func:`fold_checks` then folds every per-coin bar check and signal of one
candle into a single quiet line: ``checked 3 coins · no signal``.

Reference: openspec/changes/653-live-activity/change.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from hermes.application.console.activity import candle_boundary
from hermes.application.console.logs import LogLevel, LogLine
from hermes.application.console.views import ActivityItem, ActivityKind, RuleCheck

__all__ = ["EntryCounts", "JournalActivity", "fold_checks", "journal_activity", "rule_checks"]

CheckKey = tuple[datetime, str]
"""One check: the candle boundary and the ``<interval> check`` text of its bar rows."""

_STARTED: Final = "serve.run.started"
_PROCESSED: Final = "serve.bar.processed"
_UNKNOWN_RELEASE: Final = "unknown"
_ENTRY_FIELDS: Final = ("entry_breakout", "entry_momentum", "entry_regime")
_YES: Final = "yes"
_DEFAULT_INTERVAL: Final = "4h"


@dataclass(frozen=True, slots=True)
class EntryCounts:
    """How many coins held each entry condition on one check.

    Attributes:
        reporting: Coins whose journal line carried the entry fields.
        breakout: Of those, how many had the breakout condition.
        momentum: Of those, how many had the momentum condition.
        regime: Of those, how many had the regime condition.
    """

    reporting: int
    breakout: int
    momentum: int
    regime: int

    def text(self) -> str:
        """Return ``breakout 1/3 · momentum 2/3 · regime 3/3``."""
        n = self.reporting
        return (
            f"breakout {self.breakout}/{n} · momentum {self.momentum}/{n} "
            f"· regime {self.regime}/{n}"
        )


@dataclass(frozen=True, slots=True)
class JournalActivity:
    """What the journal adds to the feed.

    Attributes:
        items: Restart, warning and error rows, oldest first.
        entry_counts: Entry-condition counts per check, from the bar lines.
    """

    items: tuple[ActivityItem, ...]
    entry_counts: Mapping[CheckKey, EntryCounts]


def journal_activity(lines: Iterable[LogLine], *, since: datetime | None = None) -> JournalActivity:
    """Read restarts, warnings, errors and entry-condition counts out of journal lines.

    Args:
        lines: Parsed journal lines in any order.
        since: Drop lines recorded before this instant, if given.

    Returns:
        The rows and counts; both empty for a journal without hermes lines.
    """
    ordered = sorted((line for line in lines if since is None or line.at >= since), key=_at)
    items: list[ActivityItem] = []
    tallies: dict[CheckKey, list[int]] = {}
    previous_release: str | None = None
    for line in ordered:
        if line.event == _STARTED:
            release = _release(line)
            items.append(_restart(line, release, previous_release))
            previous_release = release or previous_release
        elif line.event == _PROCESSED:
            _tally(tallies, line)
        elif line.event and line.severity >= LogLevel.WARNING:
            items.append(_problem(line))
    counts = {key: EntryCounts(*tally) for key, tally in tallies.items()}
    return JournalActivity(items=tuple(items), entry_counts=counts)


def fold_checks(
    items: Sequence[ActivityItem], entry_counts: Mapping[CheckKey, EntryCounts] | None = None
) -> tuple[ActivityItem, ...]:
    """Fold each candle's bar checks and signals into one quiet line.

    Bar rows of the same boundary and interval become ``checked N coins`` with
    ``no signal`` or the signals that candle raised; when the journal reported
    entry conditions for it, their counts follow. Signals with no bar row
    beside them stay as they are. Every other row passes through untouched.

    Args:
        items: Feed rows in any order.
        entry_counts: Yes/no counts per check, from :func:`journal_activity`.

    Returns:
        The rows with the quiet ones folded, oldest first.
    """
    counts = entry_counts or {}
    groups: dict[CheckKey, list[ActivityItem]] = {}
    signals: dict[datetime, list[ActivityItem]] = {}
    kept: list[ActivityItem] = []
    for item in items:
        if item.kind is ActivityKind.BAR:
            groups.setdefault((item.at, item.text or ""), []).append(item)
        elif item.kind is ActivityKind.SIGNAL:
            signals.setdefault(item.at, []).append(item)
        else:
            kept.append(item)
    absorbed: set[datetime] = set()
    for key in sorted(groups):
        at = key[0]
        raised = [] if at in absorbed else signals.get(at, [])
        if raised:
            absorbed.add(at)
        kept.append(_check_line(key, groups[key], raised, counts.get(key)))
    for at, loose in signals.items():
        if at not in absorbed:
            kept.extend(loose)
    return tuple(sorted(kept, key=_at))


def rule_checks(
    lines: Iterable[LogLine], *, since: datetime | None = None
) -> tuple[RuleCheck, ...]:
    """Return which entry rules held per coin and check, newest first (#714).

    Only ``serve.bar.processed`` lines that carry all three yes/no entry
    fields count; a coin checked twice for the same candle (a restart) keeps
    its latest line.

    Args:
        lines: Parsed journal lines in any order.
        since: Drop checks of candles that closed before this instant, if given.

    Returns:
        One check per coin, interval and candle, newest candle first, then by coin.
    """
    latest: dict[tuple[datetime, str, str], RuleCheck] = {}
    for line in sorted(lines, key=_at):
        if line.event != _PROCESSED:
            continue
        check = _rule_check(dict(line.fields))
        if check is not None and (since is None or check.at >= since):
            latest[check.at, check.coin, check.interval] = check
    return tuple(sorted(latest.values(), key=lambda c: (-c.at.timestamp(), c.coin, c.interval)))


def _rule_check(fields: Mapping[str, str]) -> RuleCheck | None:
    pair = fields.get("pair")
    if not pair or not all(name in fields for name in _ENTRY_FIELDS):
        return None
    try:
        closed = datetime.fromisoformat(fields["close_time"])
    except KeyError, ValueError:
        return None
    breakout, momentum, regime = (fields[name] == _YES for name in _ENTRY_FIELDS)
    return RuleCheck(
        at=candle_boundary(closed),
        coin=pair.split("/")[0],
        interval=fields.get("interval", _DEFAULT_INTERVAL),
        breakout=breakout,
        momentum=momentum,
        regime=regime,
    )


def _at(value: ActivityItem | LogLine) -> datetime:
    return value.at


def _release(line: LogLine) -> str | None:
    name = dict(line.fields).get("release")
    return None if name in (None, "", _UNKNOWN_RELEASE) else name


def _restart(line: LogLine, release: str | None, previous: str | None) -> ActivityItem:
    if release is None:
        text = "restarted"
    elif previous is not None and release != previous:
        text = f"deployed {release} (was {previous})"
    else:
        text = f"restarted · release {release}"
    return ActivityItem(at=line.at, kind=ActivityKind.RESTART, text=text)


def _problem(line: LogLine) -> ActivityItem:
    fields = dict(line.fields)
    text = line.event
    if fields.get("error"):
        text += f" ({fields['error']})"
    pair = fields.get("pair")
    kind = ActivityKind.WARNING if line.severity is LogLevel.WARNING else ActivityKind.ERROR
    return ActivityItem(at=line.at, kind=kind, coin=pair.split("/")[0] if pair else None, text=text)


def _tally(tallies: dict[CheckKey, list[int]], line: LogLine) -> None:
    fields = dict(line.fields)
    if not all(name in fields for name in _ENTRY_FIELDS):
        return
    try:
        closed = datetime.fromisoformat(fields["close_time"])
    except KeyError, ValueError:
        return
    key = (candle_boundary(closed), f"{fields.get('interval', _DEFAULT_INTERVAL)} check")
    tally = tallies.setdefault(key, [0, 0, 0, 0])
    tally[0] += 1
    for slot, name in enumerate(_ENTRY_FIELDS, start=1):
        tally[slot] += fields[name] == _YES


def _check_line(
    key: CheckKey,
    bars: Sequence[ActivityItem],
    raised: Sequence[ActivityItem],
    counts: EntryCounts | None,
) -> ActivityItem:
    at, label = key
    interval = label.removesuffix(" check")
    n = len(bars)
    prefix = "checked" if interval in {_DEFAULT_INTERVAL, ""} else f"{interval} checked"
    parts = [f"{prefix} {n} {'coin' if n == 1 else 'coins'}"]
    parts.append(", ".join(f"{s.text} {s.coin}".strip() for s in raised) if raised else "no signal")
    if counts is not None and counts.reporting:
        parts.append(counts.text())
    return ActivityItem(at=at, kind=ActivityKind.BAR, text=" · ".join(parts), count=n)
