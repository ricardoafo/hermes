"""The standard-library machine reader (issue #717).

Invariants:

- cpu is the one-minute load average per cpu; memory is
  ``(MemTotal - MemAvailable) / MemTotal``; disk is used / total of the
  ledger's disk;
- the ledger size is the file's size and the last snapshot is the newest
  ``*.sqlite3`` in the snapshot directory;
- each figure that cannot be read is ``None`` on its own ("not available
  here"), so a PC without ``/proc`` still shows the disk.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.system.machine import DiskUsage, ProcMachineReader
from hermes.application.console.system import MachineStats


def _proc(tmp_path: Path) -> Path:
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("1.00 0.80 0.50 2/300 4242\n", encoding="utf-8")
    (proc / "meminfo").write_text(
        "MemTotal:        4000000 kB\nMemFree:          500000 kB\nMemAvailable:    1000000 kB\n",
        encoding="utf-8",
    )
    return proc


def _ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / "hermes.sqlite3"
    ledger.write_bytes(b"x" * 4096)
    return ledger


def _snapshots(tmp_path: Path) -> Path:
    snapshots = tmp_path / "backups"
    snapshots.mkdir()
    for name, mtime in (("pre-old.sqlite3", 1_759_000_000), ("pre-new.sqlite3", 1_759_400_000)):
        path = snapshots / name
        path.touch()
        os.utime(path, (mtime, mtime))
    (snapshots / "notes.txt").touch()
    return snapshots


def _reader(tmp_path: Path, **overrides: object) -> ProcMachineReader:
    values: dict[str, object] = {
        "cpu_count": lambda: 4,
        "disk_usage": lambda _path: DiskUsage(100, 63, 37),
        **overrides,
    }
    values.setdefault("ledger", _ledger(tmp_path))
    values.setdefault("snapshots", _snapshots(tmp_path))
    if "proc" not in values:
        values["proc"] = _proc(tmp_path)
    return ProcMachineReader(**values)  # type: ignore[arg-type]


def test_every_figure_is_read_on_the_vps(tmp_path: Path) -> None:
    """Load 1.0 on 4 cpus is 25%; 3 of 4 GB used is 75%; 63 of 100 on disk."""
    assert _reader(tmp_path).machine() == MachineStats(
        cpu_load=0.25,
        memory_used=0.75,
        disk_used=0.63,
        disk_free_bytes=37,
        ledger_bytes=4096,
        last_snapshot="pre-new.sqlite3",
        last_snapshot_at=datetime.fromtimestamp(1_759_400_000, tz=UTC),
    )


def test_without_proc_cpu_and_memory_are_not_available_but_disk_is(tmp_path: Path) -> None:
    """A PC has no /proc: cpu and memory None, the disk and ledger still read."""
    stats = _reader(tmp_path, proc=tmp_path / "absent").machine()
    assert (stats.cpu_load, stats.memory_used, stats.disk_used, stats.ledger_bytes) == (
        None,
        None,
        0.63,
        4096,
    )


def test_missing_ledger_and_snapshot_directory_are_not_available(tmp_path: Path) -> None:
    """No ledger file and no snapshot directory: both figures None."""
    stats = _reader(
        tmp_path, ledger=tmp_path / "none.sqlite3", snapshots=tmp_path / "no-backups"
    ).machine()
    assert (stats.ledger_bytes, stats.last_snapshot, stats.last_snapshot_at) == (None, None, None)


def test_an_unreadable_disk_is_not_available(tmp_path: Path) -> None:
    """disk_usage raising OSError leaves disk and free space None."""

    def broken(_path: Path) -> DiskUsage:
        raise OSError("gone")

    stats = _reader(tmp_path, disk_usage=broken).machine()
    assert (stats.disk_used, stats.disk_free_bytes) == (None, None)


@pytest.mark.parametrize(
    "meminfo",
    ["MemTotal: 4000000 kB\n", "MemAvailable: 100 kB\n", "garbage\n"],
)
def test_memory_without_both_fields_is_not_available(tmp_path: Path, meminfo: str) -> None:
    """MemTotal and MemAvailable are both needed; a partial file gives None."""
    proc = _proc(tmp_path)
    (proc / "meminfo").write_text(meminfo, encoding="utf-8")
    stats = _reader(tmp_path, proc=proc).machine()
    assert (stats.memory_used, stats.cpu_load) == (None, 0.25)


def test_unknown_cpu_count_leaves_cpu_not_available(tmp_path: Path) -> None:
    """os.cpu_count() may return None; cpu is then None, not a division error."""
    stats = _reader(tmp_path, cpu_count=lambda: None).machine()
    assert (stats.cpu_load, stats.memory_used) == (None, 0.75)
