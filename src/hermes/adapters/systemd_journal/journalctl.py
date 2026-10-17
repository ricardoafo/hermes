"""``journalctl`` as a line source for ``hermes logs`` (#376).

Runs ``journalctl -u hermes.service -o json`` with an argument list (no shell)
and yields its stdout lines. ``follow=True`` adds ``-f`` and streams until the
caller stops iterating (Ctrl-C). The process runner is injectable so unit
tests never start a real journalctl.

Reference: openspec/changes/376-hermes-logs/change.md.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Final

__all__ = ["JournalUnavailableError", "JournalctlReader"]

_UNIT: Final = "hermes.service"


class JournalUnavailableError(RuntimeError):
    """There is no ``journalctl`` on this machine (a dev box)."""


def _popen(argv: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8"
    )


@dataclass(frozen=True, slots=True)
class JournalctlReader:
    """Stream a hermes unit's journal entries as JSON lines (hermes.service by default).

    Attributes:
        executable: Path to journalctl; ``None`` looks it up on ``PATH``.
        spawn: Starts the process; the default wraps :class:`subprocess.Popen`.
        unit: The unit whose journal is read; the System screen's service
            detail reads the other hermes units (#717).
    """

    executable: str | None = None
    spawn: Callable[[list[str]], subprocess.Popen[str]] = field(default=_popen)
    unit: str = _UNIT

    def argv(self, *, last: int, since: str | None, follow: bool) -> list[str]:
        """Return the journalctl command line.

        Args:
            last: How many most recent entries to start from.
            since: A journalctl ``--since`` value, e.g. ``-1h``; ``None`` for no bound.
            follow: Keep streaming new entries.

        Raises:
            JournalUnavailableError: journalctl is not installed.
        """
        exe = self.executable or shutil.which("journalctl")
        if exe is None:
            msg = "journalctl is not available here; run hermes logs on the VPS"
            raise JournalUnavailableError(msg)
        argv = [exe, "--unit", self.unit, "--output", "json", "--no-pager", "--lines", str(last)]
        if since is not None:
            argv += ["--since", since]
        if follow:
            argv.append("--follow")
        return argv

    def lines(self, *, last: int, since: str | None = None, follow: bool = False) -> Iterator[str]:
        """Yield journalctl's JSON lines, oldest first.

        Args:
            last: How many most recent entries to start from.
            since: A journalctl ``--since`` value; ``None`` for no bound.
            follow: Keep streaming new entries until the caller stops.

        Raises:
            JournalUnavailableError: journalctl is not installed.
        """
        process = self.spawn(self.argv(last=last, since=since, follow=follow))
        try:
            if process.stdout is not None:
                yield from (line.rstrip("\n") for line in process.stdout)
        finally:
            process.terminate()
            process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
