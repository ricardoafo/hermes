"""What the console's System screen reads: services, releases, machine, bar timing.

The System screen (design spec screen 7, issue #717) is view only in MVP1. It
reads four small sources, each a read-only port implemented by an adapter:

- :class:`ServiceReader`: each hermes unit's systemd state and next timer run;
- :class:`ReleaseReader`: the live release and the earlier ones on disk;
- :class:`MachineReader`: cpu, memory, disk, ledger size and the last ledger
  snapshot;
- :class:`BarTimingReader`: how long after each bar's close the bar check was
  recorded.

A source that cannot answer on this machine (a PC without systemd, no release
directory) returns ``None`` (or ``None`` per value), and the screen says "not
available here". Nothing here runs a command: the VPS actions are only shown as
:data:`VPS_ACTIONS`, commands for the operator to copy.

Reference: openspec/changes/717-system-screen/change.md.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol

from hermes.application.console.logs import LogLine

__all__ = [
    "DEPLOY_STEPS",
    "DIAGNOSIS",
    "SERVICE_UNITS",
    "VPS_ACTIONS",
    "BarTimingReader",
    "CopyCommand",
    "LiveRelease",
    "MachineReader",
    "MachineStats",
    "ReleaseEntry",
    "ReleaseHistory",
    "ReleaseReader",
    "ReleaseRole",
    "ServiceReader",
    "ServiceStatus",
    "ServiceUnit",
    "SystemSources",
    "age_text",
]


@dataclass(frozen=True, slots=True)
class ServiceUnit:
    """One hermes unit the System screen lists.

    Attributes:
        name: Short name shown on the screen (``daily``).
        unit: The systemd service (``hermes-daily.service``).
        timer: The timer that starts it, or ``None`` for a long-running unit.
    """

    name: str
    unit: str
    timer: str | None = None


SERVICE_UNITS: Final = (
    ServiceUnit("hermes", "hermes.service"),
    ServiceUnit("bot", "hermes-bot.service"),
    ServiceUnit("health", "hermes-health.service", "hermes-health.timer"),
    ServiceUnit("daily", "hermes-daily.service", "hermes-daily.timer"),
    ServiceUnit("backup", "hermes-backup.service", "hermes-backup.timer"),
    ServiceUnit("archive check", "hermes-archive-check.service", "hermes-archive-check.timer"),
    ServiceUnit("tax pack", "hermes-tax-pack.service", "hermes-tax-pack.timer"),
)


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    """One unit as systemd reports it.

    Attributes:
        unit: Which unit.
        installed: systemd knows the service (``LoadState`` is not ``not-found``).
        active: ``ActiveState`` (``active``, ``inactive``, ``failed``...).
        sub: ``SubState`` (``running``, ``dead``, ``exited``...).
        since: When the unit entered its current state, if known.
        timer_active: The timer is armed; ``False`` without a timer.
        next_run: When the timer fires next, if armed.
        last_run: When the timer last fired, if ever.
    """

    unit: ServiceUnit
    installed: bool
    active: str
    sub: str
    since: datetime | None = None
    timer_active: bool = False
    next_run: datetime | None = None
    last_run: datetime | None = None

    @property
    def state(self) -> str:
        """Return ``active (running)``, ``failed (failed)`` or ``not installed``."""
        if not self.installed:
            return "not installed"
        return f"{self.active} ({self.sub})" if self.sub else self.active


class ServiceReader(Protocol):
    """Read-only source of the units' systemd state."""

    def services(self) -> tuple[ServiceStatus, ...] | None:
        """Return one status per :data:`SERVICE_UNITS` entry, or ``None`` without systemd."""
        ...


@dataclass(frozen=True, slots=True)
class LiveRelease:
    """The release the ``current`` link points at, as ``hermes status`` reads it.

    Attributes:
        name: The release directory's name (``2bc2798a1f00``) or ``unknown``.
        live_since: When the link was last flipped, if known.
    """

    name: str
    live_since: datetime | None


class ReleaseRole(StrEnum):
    """Where a release stands."""

    LIVE = "live"
    PREVIOUS = "previous"
    EARLIER = "earlier"


@dataclass(frozen=True, slots=True)
class ReleaseEntry:
    """One release directory on disk.

    Attributes:
        name: The directory name, the first 12 characters of the git sha.
        installed_at: When its install finished (the ``.installed`` marker),
            or ``None`` when the install never finished.
    """

    name: str
    installed_at: datetime | None

    @property
    def complete(self) -> bool:
        """Return whether the install finished."""
        return self.installed_at is not None


@dataclass(frozen=True, slots=True)
class ReleaseHistory:
    """The live release and every release still on disk.

    Attributes:
        live: The live release.
        previous: The release the ``previous`` link points at, if any.
        entries: The releases on disk, newest install first.
    """

    live: LiveRelease
    previous: str | None
    entries: tuple[ReleaseEntry, ...]

    def names(self) -> tuple[str, ...]:
        """Return the release names in the order shown."""
        return tuple(entry.name for entry in self.entries)

    def entry(self, name: str) -> ReleaseEntry | None:
        """Return the entry called ``name``, or ``None`` when it is gone."""
        return next((entry for entry in self.entries if entry.name == name), None)

    def role(self, name: str) -> ReleaseRole:
        """Return whether ``name`` is live, the previous release or an earlier one."""
        if name == self.live.name:
            return ReleaseRole.LIVE
        if name == self.previous:
            return ReleaseRole.PREVIOUS
        return ReleaseRole.EARLIER

    def rollback_target(self, name: str) -> str | None:
        """Return the release a rollback from ``name`` goes to.

        ``rollback.sh`` flips ``current`` back to ``previous``, so only the
        live release has a known target, and only while ``previous`` names
        another release.
        """
        if name != self.live.name or self.previous in (None, name):
            return None
        return self.previous


class ReleaseReader(Protocol):
    """Read-only source of the releases on disk."""

    def releases(self) -> ReleaseHistory | None:
        """Return the releases, or ``None`` where there is no release directory."""
        ...


@dataclass(frozen=True, slots=True)
class MachineStats:
    """The machine hermes runs on; each value is ``None`` where it cannot be read.

    Attributes:
        cpu_load: One-minute load average per cpu (1.0 = every cpu busy).
        memory_used: Share of memory in use, 0 to 1.
        disk_used: Share of the ledger's disk in use, 0 to 1.
        disk_free_bytes: Free bytes on the ledger's disk.
        ledger_bytes: Size of the ledger file.
        last_snapshot: Name of the newest ledger snapshot.
        last_snapshot_at: When the newest ledger snapshot was written.
    """

    cpu_load: float | None = None
    memory_used: float | None = None
    disk_used: float | None = None
    disk_free_bytes: int | None = None
    ledger_bytes: int | None = None
    last_snapshot: str | None = None
    last_snapshot_at: datetime | None = None


class MachineReader(Protocol):
    """Read-only source of the machine's figures."""

    def machine(self) -> MachineStats:
        """Return what can be read on this machine."""
        ...


class BarTimingReader(Protocol):
    """Read-only source of the bar-check timing."""

    def bar_check_seconds(self, limit: int) -> tuple[Decimal, ...]:
        """Return the seconds from each bar's close to its recorded check, oldest first.

        Args:
            limit: How many of the newest bar checks to return.
        """
        ...


@dataclass(frozen=True, slots=True)
class SystemSources:
    """Everything the System screen reads; a missing source shows "not available here".

    Attributes:
        services: systemd state of the hermes units.
        releases: The releases on disk.
        machine: cpu, memory, disk, ledger size, last snapshot.
        bar_timing: Bar-check timing from the ledger.
        unit_journal: Returns the last N journal lines of a unit, or ``None``
            where the journal is not readable.
    """

    services: ServiceReader | None = None
    releases: ReleaseReader | None = None
    machine: MachineReader | None = None
    bar_timing: BarTimingReader | None = None
    unit_journal: Callable[[str, int], Sequence[LogLine] | None] | None = field(default=None)


@dataclass(frozen=True, slots=True)
class CopyCommand:
    """An action shown as a command for the operator to copy; the console never runs it.

    Attributes:
        label: What the command does.
        command: The command line, exactly as the operator types it.
        note: When or how to use it.
    """

    label: str
    command: str
    note: str = ""


VPS_ACTIONS: Final = (
    CopyCommand(
        "bootstrap",
        "gh workflow run vps-bootstrap.yml --repo ricardoafo/hermes --ref main",
        "from the PC; re-applies the VPS setup",
    ),
    CopyCommand(
        "restart",
        "ssh hermes-01 systemctl restart hermes.service",
        "not within 5 min of a 4h bar close",
    ),
    CopyCommand(
        "rollback",
        "ssh hermes-01 /opt/hermes/bin/rollback.sh",
        "back to the previous release, health gate re-run",
    ),
)
DIAGNOSIS: Final = CopyCommand("read-only diagnosis", "hermes status", "changes nothing")

DEPLOY_STEPS: Final = (
    "Preflight checks; nothing changes until all pass",
    "Install into versions/<sha12>",
    "Wait out the 4h bar close",
    "Snapshot the ledger (pre-<sha12>.sqlite3)",
    "Check the ledger's event schema",
    "Run destructive migrations, if any",
    "Install changed systemd units and timers",
    "Flip previous and current, restart hermes",
    "Health gate; roll back on failure",
    "Refresh /opt/hermes/bin and prune old releases",
)

_MINUTE: Final = 60
_HOUR: Final = 3600
_DAY: Final = 86400


def age_text(at: datetime, now: datetime) -> str:
    """Return how long ago ``at`` was: ``just now``, ``12 min ago``, ``3 h ago``, ``2 d ago``.

    Args:
        at: The moment, timezone-aware.
        now: The current time, timezone-aware.

    Returns:
        A short age; ``in the future`` when ``at`` is after ``now``.
    """
    seconds = int((now - at).total_seconds())
    if seconds < 0:
        return "in the future"
    if seconds < _MINUTE:
        return "just now"
    if seconds < _HOUR:
        return f"{seconds // _MINUTE} min ago"
    if seconds < _DAY:
        return f"{seconds // _HOUR} h ago"
    return f"{seconds // _DAY} d ago"
