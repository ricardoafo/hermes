"""Unit tests for the exclusive OS file lock (issue #855).

Invariants under test:

- One holder at a time: a second open of the lock file cannot take it, even
  in the same process, until the holder releases it.
- A holder that dies frees the lock with no clean-up.
- ``locked`` waits for a holder, and gives up with ``LockTimeoutError``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from hermes.infrastructure.file_lock import FileLock, LockTimeoutError, locked

_HOLD_AND_WAIT = """
import os
import sys
from pathlib import Path
from hermes.infrastructure.file_lock import FileLock
lock = FileLock(Path(sys.argv[1]))
assert lock.try_acquire()
print(os.getpid(), flush=True)
sys.stdin.read()
"""
_RELEASE_WAIT_SECONDS = 5.0
"""Windows frees a dead process's locks asynchronously, so the next holder may wait a moment."""


def test_a_second_lock_on_a_held_file_is_refused(tmp_path: Path) -> None:
    """While one FileLock holds the file, another on the same path cannot take it."""
    first = FileLock(tmp_path / "x.lock")
    assert first.try_acquire()
    try:
        assert FileLock(tmp_path / "x.lock").try_acquire() is False
    finally:
        first.release()


def test_a_released_lock_can_be_taken_again(tmp_path: Path) -> None:
    """After release, the next FileLock on the same path takes it."""
    first = FileLock(tmp_path / "x.lock")
    assert first.try_acquire()
    first.release()
    second = FileLock(tmp_path / "x.lock")
    assert second.try_acquire()
    second.release()


def test_a_holder_process_that_is_killed_frees_the_lock(tmp_path: Path) -> None:
    """A holder killed with no chance to clean up leaves the lock free for the next one.

    This is what lets systemd restart serve after a crash or ``kill -9``.
    """
    path = tmp_path / "x.lock"
    holder = subprocess.Popen(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", _HOLD_AND_WAIT, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        # The pid the script itself reports: on Windows a venv python may be a
        # launcher whose child is the process that holds the lock.
        holder_pid = int(holder.stdout.readline())
        refused_while_alive = FileLock(path).try_acquire() is False
        os.kill(holder_pid, signal.SIGTERM)
    finally:
        holder.kill()
        holder.wait(timeout=30)
    after = FileLock(path)
    after.acquire(timeout=_RELEASE_WAIT_SECONDS)
    assert (refused_while_alive, after.held) == (True, True)
    after.release()


def test_locked_gives_up_after_the_timeout(tmp_path: Path) -> None:
    """``locked`` raises LockTimeoutError instead of waiting for ever on a stuck holder."""
    holder = FileLock(tmp_path / "x.lock")
    assert holder.try_acquire()
    try:
        with pytest.raises(LockTimeoutError), locked(tmp_path / "x.lock", timeout=0.05):
            pass
    finally:
        holder.release()


def test_locked_runs_the_block_only_once_the_holder_lets_go(tmp_path: Path) -> None:
    """A waiter enters the block only after the holder releases."""
    holder = FileLock(tmp_path / "x.lock")
    assert holder.try_acquire()
    order: list[str] = []

    def waiter() -> None:
        with locked(tmp_path / "x.lock", timeout=10):
            order.append("waiter")

    thread = threading.Thread(target=waiter)
    thread.start()
    thread.join(timeout=0.2)
    order.append("released")
    holder.release()
    thread.join(timeout=10)
    assert order == ["released", "waiter"]


def test_the_holder_writes_its_pid(tmp_path: Path) -> None:
    """The lock file names the last holding process, for the refusal message."""
    lock = FileLock(tmp_path / "x.lock")
    assert lock.try_acquire()
    lock.release()
    assert FileLock(tmp_path / "x.lock").holder_pid() == str(os.getpid())
