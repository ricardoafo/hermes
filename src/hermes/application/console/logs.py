"""Readable lines from hermes's journal (#376).

``journalctl -u hermes.service -o json`` gives one JSON envelope per entry;
its ``MESSAGE`` is the structlog JSON hermes printed (ADR-008 §1). This module
turns those envelopes into :class:`LogLine` values, filters them by level and
correlation id, and renders them as::

    16:00:05  INFO   serve.bar.processed   pair=BTC/USDT interval=4h  …K7Q2ZP

Each line also names its service, from the journal's systemd unit
(``hermes.service`` is the ``engine``, ``hermes-bot.service`` the ``bot``).

Lines whose message is not structlog JSON (systemd's own messages,
tracebacks) pass through raw. No field is added or dropped: the line shows
what hermes logged, which by ADR-008 never includes secrets or P&L.

Reference: openspec/changes/376-hermes-logs/change.md.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from enum import IntEnum
from typing import Final

from rich.text import Text

__all__ = [
    "LogLevel",
    "LogLine",
    "filter_lines",
    "log_line_to_json",
    "parse_journal_entry",
    "render_log_line",
    "service_name",
]

_CORR_TAIL: Final = 6
_SKIP_FIELDS: Final = frozenset({"event", "level", "timestamp", "correlation_id", "logger"})
_LEVEL_STYLE: Final = {"warning": "yellow", "error": "red", "critical": "bold red"}
_ENGINE_UNIT: Final = "hermes"
_ENGINE: Final = "engine"
_UNIT_PREFIX: Final = "hermes-"


class LogLevel(IntEnum):
    """Structlog levels in severity order, for ``--level`` filtering."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    @classmethod
    def parse(cls, name: str) -> LogLevel:
        """Return the level called ``name`` (case-insensitive; ``warn`` is WARNING).

        Raises:
            ValueError: ``name`` is not a level.
        """
        key = name.strip().upper()
        if key == "WARN":
            key = "WARNING"
        try:
            return cls[key]
        except KeyError as exc:
            msg = f"unknown level {name!r}"
            raise ValueError(msg) from exc


@dataclass(frozen=True, slots=True)
class LogLine:
    """One journal entry.

    Attributes:
        at: When journald recorded it, UTC-aware.
        level: Structlog level name, lower case; ``""`` for raw lines.
        event: Structlog event name; ``""`` for raw lines.
        fields: The other structlog key/values, in logged order.
        correlation_id: The bar cycle's ULID, if logged.
        raw: The message as logged, for lines that are not structlog JSON.
        service: The systemd unit as a short name (``engine``, ``bot``), or
            ``None`` when the entry names no unit.
    """

    at: datetime
    level: str = ""
    event: str = ""
    fields: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    correlation_id: str | None = None
    raw: str | None = None
    service: str | None = None

    @property
    def severity(self) -> LogLevel:
        """Return the level for filtering; raw lines count as INFO."""
        try:
            return LogLevel.parse(self.level) if self.level else LogLevel.INFO
        except ValueError:
            return LogLevel.INFO


def service_name(unit: str) -> str:
    """Return the short service name of a systemd unit.

    Args:
        unit: A unit name such as ``hermes.service`` or ``hermes-bot.service``.

    Returns:
        ``engine`` for ``hermes.service``; the unit without ``hermes-`` and its
        suffix otherwise (``bot``, ``health``, ``notify-failure``).
    """
    base = unit.partition("@")[0]
    stem = base.rpartition(".")[0] or base
    if stem == _ENGINE_UNIT:
        return _ENGINE
    return stem.removeprefix(_UNIT_PREFIX)


def _service(envelope: dict[str, object]) -> str | None:
    """Return the service an envelope belongs to; systemd's own lines carry ``UNIT``."""
    unit = envelope.get("UNIT") or envelope.get("_SYSTEMD_UNIT")
    return service_name(unit) if isinstance(unit, str) and unit else None


def parse_journal_entry(envelope_json: str) -> LogLine | None:
    """Parse one ``journalctl -o json`` line.

    Args:
        envelope_json: One line of journalctl's JSON output.

    Returns:
        The log line, or ``None`` when the line is not a journal envelope.
    """
    try:
        envelope = json.loads(envelope_json)
    except ValueError:
        return None
    if not isinstance(envelope, dict):
        return None
    at = _realtime(envelope.get("__REALTIME_TIMESTAMP"))
    message = envelope.get("MESSAGE")
    if isinstance(message, list):  # journald stores non-UTF-8 text as a byte list
        message = bytes(int(b) for b in message).decode("utf-8", errors="replace")
    text = "" if message is None else str(message)
    service = _service(envelope)
    try:
        payload = json.loads(text)
    except ValueError:
        return LogLine(at=at, raw=text, service=service)
    if not isinstance(payload, dict) or "event" not in payload:
        return LogLine(at=at, raw=text, service=service)
    fields = tuple(
        (str(key), _value(value)) for key, value in payload.items() if key not in _SKIP_FIELDS
    )
    corr = payload.get("correlation_id")
    return LogLine(
        at=at,
        level=str(payload.get("level", "info")).lower(),
        event=str(payload["event"]),
        fields=fields,
        correlation_id=None if corr is None else str(corr),
        service=service,
    )


def filter_lines(
    lines: Iterable[LogLine], *, min_level: LogLevel = LogLevel.DEBUG, cycle: str | None = None
) -> Iterator[LogLine]:
    """Yield the lines at or above ``min_level`` whose correlation id ends with ``cycle``.

    Args:
        lines: Parsed lines.
        min_level: Lowest level to keep.
        cycle: A full correlation id or its tail; ``None`` keeps every line.
            Raw lines carry no id, so a cycle filter drops them.
    """
    wanted = None if cycle is None else cycle.strip().upper()
    for line in lines:
        if line.severity < min_level:
            continue
        if wanted is not None and not (
            line.correlation_id and line.correlation_id.upper().endswith(wanted)
        ):
            continue
        yield line


def render_log_line(line: LogLine, tz: tzinfo = UTC) -> Text:
    """Return ``HH:MM:SS  LEVEL  event  key=value...  …corrtail`` as Rich text.

    Warnings are yellow, errors red; the console drops colour off a terminal.

    Args:
        line: The line.
        tz: Display timezone for the clock.
    """
    clock = line.at.astimezone(tz).strftime("%H:%M:%S")
    if line.raw is not None:
        return Text(f"{clock}  {'':8} {line.raw}")
    level = line.level.upper()
    text = Text(f"{clock}  ")
    text.append(f"{level:<8}", style=_LEVEL_STYLE.get(line.level, ""))
    text.append(f" {line.event}")
    if line.fields:
        text.append("  " + " ".join(f"{k}={v}" for k, v in line.fields))
    if line.correlation_id:
        text.append(f"  …{line.correlation_id[-_CORR_TAIL:]}", style="dim")
    return text


def log_line_to_json(line: LogLine) -> dict[str, object]:
    """Return ``line`` as JSON-ready data for ``hermes logs --json``.

    ``service`` is included only when the entry named a unit.
    """
    data: dict[str, object] = {"at": line.at.isoformat()}
    if line.service is not None:
        data["service"] = line.service
    if line.raw is not None:
        data["raw"] = line.raw
        return data
    data.update({"level": line.level, "event": line.event, "fields": dict(line.fields)})
    if line.correlation_id:
        data["correlation_id"] = line.correlation_id
    return data


def _realtime(value: object) -> datetime:
    """Convert journald's ``__REALTIME_TIMESTAMP`` (µs since epoch) to UTC."""
    try:
        return datetime.fromtimestamp(int(str(value)) / 1_000_000, tz=UTC)
    except TypeError, ValueError:
        return datetime.fromtimestamp(0, tz=UTC)


def _value(value: object) -> str:
    """Render one structlog value compactly; nested values as JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), default=str)
