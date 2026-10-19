"""Tests for the journalctl line source: it releases the pipe it opens (#759)."""

import subprocess
import sys

import pytest

from hermes.adapters.systemd_journal import JournalctlReader, JournalUnavailableError
from hermes.domain.errors import InfraError


def test_lines_closes_the_process_pipe_when_the_stream_ends() -> None:
    """Invariant: a finished read leaves no open pipe to the child, so none leaks to the GC."""
    spawned: list[subprocess.Popen[str]] = []

    def spawn(_argv: list[str]) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [sys.executable, "-c", "print('a'); print('b')"],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        spawned.append(process)
        return process

    reader = JournalctlReader(executable="/usr/bin/journalctl", spawn=spawn)

    assert list(reader.lines(last=2)) == ["a", "b"]
    (process,) = spawned
    assert process.stdout is not None
    assert process.stdout.closed


def test_lines_closes_the_process_pipe_when_the_caller_stops_early() -> None:
    """Invariant: stopping a follow stream (Ctrl-C) also closes the pipe."""
    spawned: list[subprocess.Popen[str]] = []

    def spawn(_argv: list[str]) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; print('a', flush=True); time.sleep(30)"],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        spawned.append(process)
        return process

    reader = JournalctlReader(executable="/usr/bin/journalctl", spawn=spawn)
    stream = reader.lines(last=1, follow=True)

    assert next(stream) == "a"
    stream.close()
    (process,) = spawned
    assert process.stdout is not None
    assert process.stdout.closed


def test_a_missing_journalctl_is_caught_as_an_infra_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #1187: no journalctl raises an ``InfraError``, the adapter I/O root."""
    monkeypatch.setattr("shutil.which", lambda _name: None)

    with pytest.raises(InfraError) as caught:
        JournalctlReader().argv(last=1, since=None, follow=False)

    assert (type(caught.value), str(caught.value)) == (
        JournalUnavailableError,
        "journalctl is not available here; run hermes logs on the VPS",
    )


def test_a_missing_journalctl_is_still_caught_as_a_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #1187: re-parenting keeps every older ``except RuntimeError`` working."""
    monkeypatch.setattr("shutil.which", lambda _name: None)

    with pytest.raises(RuntimeError, match="journalctl is not available here"):
        JournalctlReader().argv(last=1, since=None, follow=False)
