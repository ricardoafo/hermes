"""The machine's figures for the System screen, standard library only (issue #717).

- cpu: the one-minute load average from ``/proc/loadavg`` divided by the cpu
  count;
- memory: ``MemTotal`` and ``MemAvailable`` from ``/proc/meminfo``;
- disk: :func:`shutil.disk_usage` of the ledger's directory;
- ledger size: the ledger file's size;
- last snapshot: the newest ``*.sqlite3`` in the deploy snapshot directory
  (``install_release.sh`` writes ``pre-<sha12>.sqlite3`` there).

Each value is read on its own and is ``None`` where it cannot be read (no
``/proc`` on a PC, no snapshot directory), so the screen says "not available
here" for that value only. Nothing is written.

Reference: openspec/archive/717-system-screen/change.md.
"""

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, NamedTuple

from hermes.application.console.system import MachineStats

__all__ = ["DEFAULT_SNAPSHOT_DIR", "DiskUsage", "ProcMachineReader"]

DEFAULT_SNAPSHOT_DIR: Final = Path("/var/lib/hermes/backups")
_SNAPSHOT_GLOB: Final = "*.sqlite3"
_KIB: Final = 1024


class DiskUsage(NamedTuple):
    """Total, used and free bytes of one disk."""

    total: int
    used: int
    free: int


def _disk_usage(path: Path) -> DiskUsage:
    usage = shutil.disk_usage(path)
    return DiskUsage(usage.total, usage.used, usage.free)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


@dataclass(frozen=True, slots=True)
class ProcMachineReader:
    """Read cpu, memory, disk, ledger size and the last snapshot.

    Attributes:
        ledger: The ledger file.
        snapshots: Where the ledger snapshots are written.
        proc: The ``/proc`` directory; injectable for tests.
        cpu_count: Returns the number of cpus, or ``None``.
        disk_usage: Returns total, used and free bytes of a path's disk.
    """

    ledger: Path
    snapshots: Path = DEFAULT_SNAPSHOT_DIR
    proc: Path = Path("/proc")
    cpu_count: Callable[[], int | None] = field(default=os.cpu_count)
    disk_usage: Callable[[Path], DiskUsage] = field(default=_disk_usage)

    def machine(self) -> MachineStats:
        """Return every figure that can be read here; the others are ``None``."""
        snapshot = self._last_snapshot()
        disk = self._disk()
        return MachineStats(
            cpu_load=self._cpu_load(),
            memory_used=self._memory_used(),
            disk_used=None if disk is None else disk.used / disk.total,
            disk_free_bytes=None if disk is None else disk.free,
            ledger_bytes=self._ledger_bytes(),
            last_snapshot=None if snapshot is None else snapshot[0],
            last_snapshot_at=None if snapshot is None else snapshot[1],
        )

    def _cpu_load(self) -> float | None:
        text = _read(self.proc / "loadavg")
        cpus = self.cpu_count()
        if text is None or not cpus:
            return None
        try:
            return float(text.split()[0]) / cpus
        except ValueError, IndexError:
            return None

    def _memory_used(self) -> float | None:
        text = _read(self.proc / "meminfo")
        if text is None:
            return None
        values: dict[str, int] = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0]) * _KIB
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if not total or available is None:
            return None
        return (total - available) / total

    def _disk(self) -> DiskUsage | None:
        try:
            usage = self.disk_usage(self.ledger.parent)
        except OSError:
            return None
        return usage if usage.total > 0 else None

    def _ledger_bytes(self) -> int | None:
        try:
            return self.ledger.stat().st_size
        except OSError:
            return None

    def _last_snapshot(self) -> tuple[str, datetime] | None:
        try:
            found = [
                (path.stat().st_mtime, path.name) for path in self.snapshots.glob(_SNAPSHOT_GLOB)
            ]
        except OSError:
            return None
        if not found:
            return None
        mtime, name = max(found)
        return name, datetime.fromtimestamp(mtime, tz=UTC)
