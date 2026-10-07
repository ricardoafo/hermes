"""``systemctl show`` as the System screen's service reader (issue #717).

One ``systemctl show --timestamp=unix`` call per read asks systemd about every
hermes service and its timer: ``ActiveState``, ``SubState``, when the unit
entered that state, and for a timer when it fires next and when it last fired.
``show`` only reads; it never starts, stops or reloads a unit, and it needs no
root, so it runs as the ``hermes`` user over ``hermes --vps``.

Off the VPS (no ``systemctl``, a failed call, or no hermes unit installed) the
reader returns ``None`` and the screen says "not available here".

Reference: openspec/changes/717-system-screen/change.md.
"""

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from hermes.application.console.system import SERVICE_UNITS, ServiceStatus, ServiceUnit

__all__ = ["SystemctlServiceReader", "parse_show", "parse_timestamp"]

_PROPERTIES: Final = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "StateChangeTimestamp",
    "NextElapseUSecRealtime",
    "LastTriggerUSec",
)
_TIMEOUT_SECONDS: Final = 5
_NOT_FOUND: Final = "not-found"
_MICROSECONDS: Final = 1_000_000


def _run(argv: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    return result.stdout if result.returncode == 0 else None


def parse_timestamp(value: str) -> datetime | None:
    """Return a ``systemctl show --timestamp=unix`` time as UTC.

    Args:
        value: ``@1759766400``, a bare microsecond count, ``0``, ``n/a`` or empty.

    Returns:
        The time, or ``None`` when systemd has none.
    """
    text = value.strip()
    try:
        if text.startswith("@"):
            return datetime.fromtimestamp(int(text[1:]), tz=UTC)
        micros = int(text)
    except ValueError:
        return None
    if micros <= 0:
        return None
    return datetime.fromtimestamp(micros / _MICROSECONDS, tz=UTC)


def parse_show(output: str) -> dict[str, dict[str, str]]:
    """Split ``systemctl show`` output for several units into one mapping per unit.

    Args:
        output: Blocks of ``Key=Value`` lines separated by blank lines.

    Returns:
        Each block's properties, keyed by its ``Id``.
    """
    units: dict[str, dict[str, str]] = {}
    for block in output.split("\n\n"):
        props: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key] = value
        if "Id" in props:
            units[props["Id"]] = props
    return units


def _status(unit: ServiceUnit, shown: dict[str, dict[str, str]]) -> ServiceStatus:
    service = shown.get(unit.unit, {})
    timer = shown.get(unit.timer, {}) if unit.timer else {}
    timer_active = timer.get("ActiveState") == "active"
    return ServiceStatus(
        unit=unit,
        installed=bool(service) and service.get("LoadState") != _NOT_FOUND,
        active=service.get("ActiveState", ""),
        sub=service.get("SubState", ""),
        since=parse_timestamp(service.get("StateChangeTimestamp", "")),
        timer_active=timer_active,
        next_run=parse_timestamp(timer.get("NextElapseUSecRealtime", "")) if timer_active else None,
        last_run=parse_timestamp(timer.get("LastTriggerUSec", "")),
    )


@dataclass(frozen=True, slots=True)
class SystemctlServiceReader:
    """Read the hermes units' state with ``systemctl show``.

    Attributes:
        units: The units to ask about.
        executable: Path to systemctl; ``None`` looks it up on ``PATH``.
        run: Runs the read-only command and returns its stdout, or ``None``
            when it failed; injectable so unit tests never start systemctl.
    """

    units: tuple[ServiceUnit, ...] = SERVICE_UNITS
    executable: str | None = None
    run: Callable[[Sequence[str]], str | None] = field(default=_run)

    def argv(self, systemctl: str) -> list[str]:
        """Return the one ``systemctl show`` command line for every unit and timer."""
        names = [unit.unit for unit in self.units]
        names += [unit.timer for unit in self.units if unit.timer]
        return [
            systemctl,
            "show",
            "--timestamp=unix",
            f"--property={','.join(_PROPERTIES)}",
            *names,
        ]

    def services(self) -> tuple[ServiceStatus, ...] | None:
        """Return one status per unit, or ``None`` where systemd has no hermes unit.

        Returns:
            The statuses in :attr:`units` order; ``None`` without systemctl,
            when the call fails, or when none of the units is installed.
        """
        systemctl = self.executable or shutil.which("systemctl")
        if systemctl is None:
            return None
        output = self.run(self.argv(systemctl))
        if output is None:
            return None
        shown = parse_show(output)
        statuses = tuple(_status(unit, shown) for unit in self.units)
        if not any(status.installed for status in statuses):
            return None
        return statuses
