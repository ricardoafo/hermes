"""``hermes logs`` — hermes's journal, readable (#376).

Usage: ``hermes logs [--last N] [-f] [--level LEVEL] [--since 1h] [--cycle ID] [--json] [--utc]``

Wraps ``journalctl -u hermes.service -o json`` and prints one line per entry:
``HH:MM:SS  LEVEL  event  key=value...  …corrtail``. ``--level warning``
keeps warnings and worse; ``--cycle`` keeps one bar cycle by its correlation
id or its tail. Warnings are yellow and errors red on a terminal; plain text
otherwise. Times are Madrid unless ``--utc``.

Exit codes: 0 success, 2 usage or no journalctl on this machine.

Reference: openspec/changes/376-hermes-logs/change.md.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC
from typing import Final, TextIO

from rich.console import Console

from hermes.adapters.systemd_journal import JournalctlReader, JournalUnavailableError
from hermes.application.console.logs import (
    LogLevel,
    filter_lines,
    log_line_to_json,
    parse_journal_entry,
    render_log_line,
)
from hermes.application.console.render import MADRID
from hermes.entrypoints._streams import resolve_err

__all__ = ["main"]

_USAGE: Final = (
    "Usage: hermes logs [--last N] [-f] [--level LEVEL] [--since 1h] [--cycle ID] [--json] [--utc]"
)
_RELATIVE: Final = re.compile(r"^\d+[smhdw]$")


def main(
    argv: list[str],
    *,
    stream: TextIO,
    reader: JournalctlReader | None = None,
    err: TextIO | None = None,
) -> int:
    """Run ``hermes logs``.

    Args:
        argv: Arguments after ``logs``.
        stream: Output stream; colour follows Rich's TTY and ``NO_COLOR`` detection.
        reader: Journal source; a fake in tests.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parsed = _parse(argv, stream, err)
    if isinstance(parsed, int):
        return parsed
    source = reader or JournalctlReader()
    console = Console(file=stream, emoji=False, markup=False, highlight=False)
    tz = UTC if parsed.utc else MADRID
    try:
        raw = source.lines(last=parsed.last, since=_since(parsed.since), follow=parsed.follow)
        entries = (line for line in map(parse_journal_entry, raw) if line is not None)
        for line in filter_lines(entries, min_level=parsed.level, cycle=parsed.cycle):
            if parsed.json:
                stream.write(json.dumps(log_line_to_json(line)) + "\n")
            else:
                console.print(render_log_line(line, tz), soft_wrap=True)
    except JournalUnavailableError as exc:
        err.write(f"ERROR: {exc}.\n")
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


def _parse(argv: list[str], stream: TextIO, err: TextIO) -> argparse.Namespace | int:
    parser = argparse.ArgumentParser(prog="hermes logs", add_help=False)
    parser.add_argument("--last", type=int, default=50)
    parser.add_argument("-f", "--follow", action="store_true")
    parser.add_argument("--level", default="debug")
    parser.add_argument("--since", default=None)
    parser.add_argument("--cycle", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--utc", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
        parsed.level = LogLevel.parse(parsed.level)
    except SystemExit, ValueError:
        err.write(f"ERROR: bad arguments. {_USAGE}\n")
        return 2
    if parsed.help:
        stream.write(_USAGE + "\n")
        return 0
    if parsed.last < 1:
        err.write(f"ERROR: --last must be at least 1. {_USAGE}\n")
        return 2
    return parsed


def _since(value: str | None) -> str | None:
    """Turn ``1h`` into journalctl's ``-1h``; pass anything else through."""
    if value is None:
        return None
    return f"-{value}" if _RELATIVE.match(value) else value
