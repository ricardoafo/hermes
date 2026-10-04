"""Tests for the TUI's journal source in the console composition (#508).

Invariant protected here: ``recent_journal_lines`` turns journalctl's JSON into
log lines and returns ``None`` on a machine without journalctl, so the TUI can
say where to look instead of failing. journalctl is replaced by a fake reader.
"""

from __future__ import annotations

import pytest

from hermes.adapters.systemd_journal import JournalUnavailableError
from hermes.entrypoints import console as console_module


def test_recent_journal_lines_parses_the_reader_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """The TUI's journal source turns journalctl JSON into log lines, or None without it."""
    envelope = (
        '{"__REALTIME_TIMESTAMP": "1791043205000000",'
        ' "MESSAGE": "{\\"event\\": \\"feed.retry\\", \\"level\\": \\"warning\\"}"}'
    )

    class _Reader:
        def lines(self, *, last: int) -> list[str]:
            assert last == 50
            return [envelope, "-- No entries --"]

    class _Missing:
        def lines(self, *, last: int) -> list[str]:
            raise JournalUnavailableError("no journalctl")

    monkeypatch.setattr(console_module, "JournalctlReader", _Reader)
    parsed = console_module.recent_journal_lines(50)
    monkeypatch.setattr(console_module, "JournalctlReader", _Missing)
    missing = console_module.recent_journal_lines(50)

    assert ([line.event for line in parsed or []], missing) == (["feed.retry"], None)
