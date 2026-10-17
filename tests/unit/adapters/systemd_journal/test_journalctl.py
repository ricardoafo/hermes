"""Tests for the journalctl line source: it releases the pipe it opens (#759)."""

import subprocess
import sys

from hermes.adapters.systemd_journal import JournalctlReader


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
