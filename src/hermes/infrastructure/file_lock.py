"""Exclusive OS file locks that the kernel drops when the holding process dies.

``flock`` on POSIX, ``msvcrt.locking`` on one byte on Windows. Both locks
belong to the open file, so a second open of the same path conflicts even
inside one process, and a crash or ``kill -9`` frees the lock with no stale
file to clean up. The lock file is never deleted: deleting it would let a
late opener lock a different inode than the current holder.

Reference: issue #855.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

__all__ = ["FileLock", "LockTimeoutError", "locked"]

_POLL_SECONDS = 0.01
_O_BINARY = getattr(os, "O_BINARY", 0)

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class LockTimeoutError(TimeoutError):
    """The lock stayed held by someone else for the whole wait."""


class FileLock:
    """One exclusive lock on ``path``, taken without blocking.

    Args:
        path: The lock file. Created on first use; its directory must exist.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        """Whether this object holds the lock."""
        return self._fd is not None

    def try_acquire(self) -> bool:
        """Take the lock if it is free and write this process id into the file.

        Returns:
            ``True`` when the lock is now held, ``False`` when another open
            file holds it.

        Raises:
            OSError: The lock file cannot be opened.
        """
        if self._fd is not None:
            return True
        writable = True
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | _O_BINARY, 0o644)
        except PermissionError:
            # A lock file another user created (root running serve by hand)
            # must not lock the service out for good: a read-only open locks too.
            fd = os.open(self.path, os.O_RDONLY | _O_BINARY)
            writable = False
        if not _try_lock(fd):
            os.close(fd)
            return False
        if writable:
            # Only the holder writes, so the pid never mixes with another one.
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        self._fd = fd
        return True

    def acquire(self, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for the lock.

        Raises:
            LockTimeoutError: The lock was still held after ``timeout``.
            OSError: The lock file cannot be opened.
        """
        deadline = time.monotonic() + timeout
        while not self.try_acquire():
            if time.monotonic() >= deadline:
                raise LockTimeoutError(f"{self.path} stayed locked for {timeout:g} s")
            time.sleep(_POLL_SECONDS)

    def release(self) -> None:
        """Drop the lock; a no-op when it is not held."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def holder_pid(self) -> str | None:
        """Return the pid the holder wrote, or ``None`` when it cannot be read.

        Windows keeps the locked byte unreadable to other handles, so this is
        ``None`` there while the lock is held.
        """
        with contextlib.suppress(OSError):
            text = self.path.read_text(encoding="ascii").strip()
            return text or None
        return None


@contextlib.contextmanager
def locked(path: Path, *, timeout: float) -> Iterator[None]:
    """Hold an exclusive lock on ``path`` for the ``with`` block.

    Args:
        path: The lock file. Its directory is created when missing.
        timeout: Seconds to wait for another holder before giving up.

    Raises:
        LockTimeoutError: Another holder kept the lock past ``timeout``.
        OSError: The lock file cannot be created.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(path)
    lock.acquire(timeout)
    try:
        yield
    finally:
        lock.release()
