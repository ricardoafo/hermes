"""Tests for the TUI's journal source in the console composition (#508).

Invariant protected here: ``recent_journal_lines`` turns journalctl's JSON into
log lines and returns ``None`` on a machine without journalctl, so the TUI can
say where to look instead of failing. journalctl is replaced by a fake reader.
"""

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


def test_journal_window_lines_reads_the_window_and_is_none_without_journalctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INV: the activity feed asks for the last window only, and an unreadable journal is None."""
    from datetime import timedelta  # noqa: PLC0415 - local to this test

    asked: list[str | None] = []

    class _Reader:
        def lines(self, *, last: int, since: str | None = None) -> list[str]:
            asked.append(since)
            return ['{"__REALTIME_TIMESTAMP": "1791043205000000", "MESSAGE": "raw text"}']

    class _Missing:
        def lines(self, *, last: int, since: str | None = None) -> list[str]:
            raise JournalUnavailableError("no journalctl")

    monkeypatch.setattr(console_module, "JournalctlReader", _Reader)
    parsed = console_module.journal_window_lines(timedelta(days=1))
    monkeypatch.setattr(console_module, "JournalctlReader", _Missing)
    missing = console_module.journal_window_lines(timedelta(days=1))

    assert (asked, [line.raw for line in parsed or []], missing) == (
        ["-86400s"],
        ["raw text"],
        None,
    )
