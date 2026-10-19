"""The Cockpit console's System screen, view only in MVP1 (design spec screen 7, issue #717).

Five panels, each a :class:`~hermes.entrypoints.tui_widgets.HudPanel` on tab 7:

- **Services**: hermes, the bot, health, daily, backup, archive check and tax
  pack, each with its systemd state and next timer run. Enter opens a unit's
  status and recent journal lines in a
  :class:`~hermes.entrypoints.tui_drilldown.DetailPane` (a
  :class:`~hermes.entrypoints.tui_drilldown.DetailScreen` on compact terminals).
- **Releases**: the live release and the earlier ones on disk. Enter opens a
  ``DetailScreen`` under ``◂ System › Releases › <sha>`` with the deploy steps
  and the rollback target where known.
- **Data and timing**: market data, order book and EUR rate freshness from the
  read model, and the bar-check time sparkline.
- **Machine**: cpu, memory and disk meters, ledger size, last ledger snapshot.
- **Actions**: each VPS action (bootstrap, restart, rollback) and the
  read-only diagnosis as a command to copy. NOTHING runs from this screen and
  no key triggers an action; the keyed actions wait for MVP2.

Every builder here is a pure function of view objects, the time and the token
sheet, so tests check the text without running an app. A source that cannot
answer on this machine shows "not available here"; a figure hermes does not
record yet shows "not available yet".

Reference: openspec/archive/717-system-screen/change.md.
"""

# ruff: noqa: RUF002 - the single right angle quote is the design spec's breadcrumb separator

from collections.abc import Sequence
from datetime import date, datetime, tzinfo
from decimal import Decimal
from typing import Final

from rich.console import Group, RenderableType
from rich.text import Text

from hermes.application.console import Heartbeat
from hermes.application.console.format import sparkline
from hermes.application.console.logs import LogLine, render_log_line
from hermes.application.console.system import (
    DEPLOY_STEPS,
    DIAGNOSIS,
    VPS_ACTIONS,
    MachineStats,
    ReleaseHistory,
    ReleaseRole,
    ServiceStatus,
    age_text,
)
from hermes.entrypoints.tui_theme import Tokens
from hermes.entrypoints.tui_widgets import Meter

__all__ = [
    "BAR_CHECKS",
    "NOT_HERE",
    "NOT_YET",
    "RELEASES_TRAIL",
    "SERVICES_TRAIL",
    "actions_text",
    "machine_text",
    "release_detail",
    "releases_text",
    "service_detail",
    "services_text",
    "timing_text",
]

NOT_HERE: Final = "not available here"
NOT_YET: Final = "not available yet"
RELEASES_TRAIL: Final = ("System", "Releases")
SERVICES_TRAIL: Final = ("System", "Services")
BAR_CHECKS: Final = 30
SELECTED: Final = "▸"
_NAME_WIDTH: Final = 14
_STATE_WIDTH: Final = 19
_LABEL_WIDTH: Final = 15
_SPARK_WIDTH: Final = 24
_DISK_WARN: Final = 0.85
_LOAD_WARN: Final = 1.0
_MEMORY_WARN: Final = 0.9
_MB: Final = 1_000_000
_GB: Final = 1_000_000_000
_NOT_HERE_HINT: Final = "; it reads the VPS (hermes --vps tui)"


def _when(at: datetime, tz: tzinfo) -> str:
    return at.astimezone(tz).strftime("%m-%d %H:%M")


def _marker(key: str, selected: str | None) -> str:
    return f"{SELECTED} " if key == selected else "  "


def _not_here(what: str, tokens: Tokens) -> Text:
    return Text(f"{what} · {NOT_HERE}{_NOT_HERE_HINT}", style=tokens.dim)


def _state_style(status: ServiceStatus, tokens: Tokens) -> str:
    if not status.installed:
        return tokens.warn
    if status.active == "failed":
        return tokens.down
    if status.active == "active":
        return tokens.up
    return tokens.muted


def _timer_cell(status: ServiceStatus, tz: tzinfo) -> str:
    if status.unit.timer is None:
        return "no timer"
    if not status.timer_active:
        return "timer off"
    if status.next_run is None:
        return "next unknown"
    return f"next {_when(status.next_run, tz)}"


def services_text(
    statuses: Sequence[ServiceStatus] | None,
    selected: str | None,
    tz: tzinfo,
    tokens: Tokens,
) -> Text:
    """Return the Services panel: one row per unit with its state and next timer run.

    Args:
        statuses: The units, or ``None`` where systemd cannot be asked.
        selected: Name of the unit under the cursor, if any.
        tz: Display timezone.
        tokens: Token sheet.

    Returns:
        ``▸ daily          inactive (dead)    next 10-07 07:00`` rows, or one
        "not available here" line.
    """
    if statuses is None:
        return _not_here("Services", tokens)
    out = Text()
    for index, status in enumerate(statuses):
        if index:
            out.append("\n")
        name = status.unit.name
        out.append(_marker(name, selected), style=f"bold {tokens.live}")
        out.append(f"{name:<{_NAME_WIDTH}}", style=tokens.text)
        out.append(f"{status.state:<{_STATE_WIDTH}}", style=_state_style(status, tokens))
        out.append(_timer_cell(status, tz), style=tokens.muted)
    return out


def service_detail(
    statuses: Sequence[ServiceStatus] | None,
    name: str,
    *,
    journal: Sequence[LogLine] | None,
    now: datetime,
    tz: tzinfo,
    tokens: Tokens,
) -> RenderableType:
    """Return one unit's status and its recent journal lines.

    Args:
        statuses: The units, or ``None`` where systemd cannot be asked.
        name: The unit's short name.
        journal: Its last journal lines, or ``None`` where the journal is not readable.
        now: The current time.
        tz: Display timezone.
        tokens: Token sheet.

    Returns:
        The unit, state, since, timer, then the journal lines.
    """
    status = next((s for s in statuses or () if s.unit.name == name), None)
    if status is None:
        return _not_here(f"Service {name}", tokens)
    unit = status.unit
    rows = [
        Text.assemble(("Unit           ", tokens.muted), (unit.unit, tokens.text)),
        Text.assemble(
            ("State          ", tokens.muted), (status.state, _state_style(status, tokens))
        ),
    ]
    if status.since is not None:
        rows.append(
            Text.assemble(
                ("Since          ", tokens.muted),
                f"{_when(status.since, tz)} · {age_text(status.since, now)}",
            )
        )
    if unit.timer is not None:
        rows.append(
            Text.assemble(
                ("Timer          ", tokens.muted),
                f"{unit.timer} · {'armed' if status.timer_active else 'off'}",
            )
        )
        rows.append(
            Text.assemble(
                ("Next run       ", tokens.muted), _timer_cell(status, tz).removeprefix("next ")
            )
        )
        last = "never" if status.last_run is None else _when(status.last_run, tz)
        rows.append(Text.assemble(("Last run       ", tokens.muted), last))
    rows.append(Text(""))
    rows.append(Text("Recent journal lines", style=f"bold {tokens.text}"))
    if journal is None:
        rows.append(Text(f"The journal is {NOT_HERE}.", style=tokens.dim))
    elif not journal:
        rows.append(Text("No journal lines yet.", style=tokens.dim))
    else:
        rows.extend(render_log_line(line, tz) for line in journal)
    return Group(*rows)


def _release_row(history: ReleaseHistory, name: str, tz: tzinfo) -> tuple[str, str]:
    role = history.role(name)
    entry = history.entry(name)
    if role is ReleaseRole.LIVE and history.live.live_since is not None:
        return role.value, f"live since {_when(history.live.live_since, tz)}"
    if entry is None or entry.installed_at is None:
        return role.value, "install unfinished"
    return role.value, f"installed {_when(entry.installed_at, tz)}"


def releases_text(
    history: ReleaseHistory | None,
    selected: str | None,
    tz: tzinfo,
    tokens: Tokens,
) -> Text:
    """Return the Releases panel: the live release first, then the others on disk.

    Args:
        history: The releases, or ``None`` where there is no release directory.
        selected: Name of the release under the cursor, if any.
        tz: Display timezone.
        tokens: Token sheet.

    Returns:
        ``▸ 2bc2798a1f00  live      live since 10-05 14:02`` rows.
    """
    if history is None:
        return _not_here("Releases", tokens)
    if not history.entries:
        return Text(f"No releases on disk. Live: {history.live.name}.", style=tokens.dim)
    out = Text()
    for index, name in enumerate(history.names()):
        if index:
            out.append("\n")
        role, when = _release_row(history, name, tz)
        out.append(_marker(name, selected), style=f"bold {tokens.live}")
        out.append(f"{name:<14}", style=tokens.text)
        out.append(f"{role:<10}", style=tokens.up if role == ReleaseRole.LIVE else tokens.muted)
        out.append(when, style=tokens.muted)
    return out


def release_detail(
    history: ReleaseHistory | None,
    name: str,
    now: datetime,
    tz: tzinfo,
    tokens: Tokens,
) -> RenderableType:
    """Return one release: role, times, rollback target and the deploy steps.

    Args:
        history: The releases, or ``None`` where there is no release directory.
        name: The release.
        now: The current time.
        tz: Display timezone.
        tokens: Token sheet.

    Returns:
        The release detail; a plain line when the release left the disk.
    """
    entry = None if history is None else history.entry(name)
    if history is None or entry is None:
        return Text(f"Release {name} is no longer on disk.", style=tokens.dim)
    role = history.role(name)
    rows = [
        Text.assemble(("Release        ", tokens.muted), (name, f"bold {tokens.text}")),
        Text.assemble(("Role           ", tokens.muted), role.value),
    ]
    installed = (
        "unfinished"
        if entry.installed_at is None
        else f"{_when(entry.installed_at, tz)} · {age_text(entry.installed_at, now)}"
    )
    rows.append(Text.assemble(("Installed      ", tokens.muted), installed))
    if role is ReleaseRole.LIVE:
        since = history.live.live_since
        healthy = "unknown" if since is None else f"{_when(since, tz)} · {age_text(since, now)}"
        rows.append(Text.assemble(("Live since     ", tokens.muted), healthy))
    target = history.rollback_target(name)
    if target is not None:
        rollback = target
    elif role is ReleaseRole.LIVE:
        rollback = "none known (previous is not set)"
    else:
        rollback = "only the live release rolls back"
    rows.append(Text.assemble(("Rollback to    ", tokens.muted), rollback))
    rows.append(Text(""))
    rows.append(Text("Deploy steps (install_release.sh)", style=f"bold {tokens.text}"))
    rows.extend(Text(f"  {number}. {step}") for number, step in enumerate(DEPLOY_STEPS, 1))
    rows.append(Text("Step timings: not recorded yet.", style=tokens.dim))
    rows.append(
        Text(
            f"PRs and changelog: the GitHub release of {name}; not stored on the VPS.",
            style=tokens.dim,
        )
    )
    return Group(*rows)


def _row(label: str, value: Text | str, tokens: Tokens) -> Text:
    out = Text(f"{label:<{_LABEL_WIDTH}}", style=tokens.muted)
    out.append_text(value if isinstance(value, Text) else Text(value))
    return out


def timing_text(
    heartbeat: Heartbeat | None,
    *,
    eur_rate_date: date | None,
    bar_seconds: Sequence[Decimal] | None,
    now: datetime,
    tz: tzinfo,
    tokens: Tokens,
) -> Text:
    """Return the Data and timing panel.

    Args:
        heartbeat: The read model's heartbeat, or ``None`` when it cannot be read.
        eur_rate_date: Date of the ECB rate the money figures use, if known.
        bar_seconds: Seconds from each bar's close to its check, oldest first,
            or ``None`` where the ledger cannot be read here.
        now: The current time.
        tz: Display timezone.
        tokens: Token sheet.

    Returns:
        Market data, order book, EUR rate and bar-check time rows.
    """
    rows: list[Text] = []
    if heartbeat is None:
        rows.append(_row("market data", Text(NOT_YET, style=tokens.dim), tokens))
    else:
        at = heartbeat.last_check_at
        rows.append(
            _row("market data", f"last bar check {_when(at, tz)} · {age_text(at, now)}", tokens)
        )
    rows.append(
        _row(
            "order book",
            Text(f"{NOT_YET} (fetched only when a signal fills)", style=tokens.dim),
            tokens,
        )
    )
    rate = (
        Text(NOT_YET, style=tokens.dim)
        if eur_rate_date is None
        else Text(f"ECB rate of {eur_rate_date.isoformat()}")
    )
    rows.append(_row("EUR rate", rate, tokens))
    if not bar_seconds:
        rows.append(_row("bar-check time", Text(NOT_YET, style=tokens.dim), tokens))
    else:
        spark = Text(sparkline(bar_seconds, _SPARK_WIDTH), style=tokens.live)
        spark.append(
            f" last {bar_seconds[-1]} s · max {max(bar_seconds)} s",
            style=tokens.muted,
        )
        rows.append(_row("bar-check time", spark, tokens))
    return Text("\n").join(rows)


def _meter_row(
    label: str, fraction: float | None, tokens: Tokens, *, warn_from: float, extra: str = ""
) -> Text:
    if fraction is None:
        return _row(label, Text(NOT_HERE, style=tokens.dim), tokens)
    value = Meter(fraction, warn_from=warn_from, caps=True, percent=True).render(tokens)
    value.append(extra, style=tokens.text)
    return _row(label, value, tokens)


def _size(count: int) -> str:
    if count >= _GB:
        return f"{count / _GB:.1f} GB"
    return f"{count / _MB:.1f} MB"


def machine_text(stats: MachineStats | None, now: datetime, tz: tzinfo, tokens: Tokens) -> Text:
    """Return the Machine panel: cpu, memory and disk meters, ledger size, last snapshot.

    Args:
        stats: What the machine reader could read, or ``None`` without a reader.
        now: The current time.
        tz: Display timezone.
        tokens: Token sheet.

    Returns:
        One row per figure; "not available here" for a figure that cannot be read.
    """
    if stats is None:
        return _not_here("Machine", tokens)
    free = "" if stats.disk_free_bytes is None else f" · {_size(stats.disk_free_bytes)} free"
    rows = [
        _meter_row("cpu", stats.cpu_load, tokens, warn_from=_LOAD_WARN),
        _meter_row("memory", stats.memory_used, tokens, warn_from=_MEMORY_WARN),
        _meter_row("disk", stats.disk_used, tokens, warn_from=_DISK_WARN, extra=free),
        _row(
            "ledger",
            Text(NOT_HERE, style=tokens.dim)
            if stats.ledger_bytes is None
            else _size(stats.ledger_bytes),
            tokens,
        ),
    ]
    if stats.last_snapshot is None or stats.last_snapshot_at is None:
        rows.append(_row("last snapshot", Text(NOT_HERE, style=tokens.dim), tokens))
    else:
        at = stats.last_snapshot_at
        rows.append(
            _row(
                "last snapshot",
                f"{stats.last_snapshot} · {_when(at, tz)} · {age_text(at, now)}",
                tokens,
            )
        )
    return Text("\n").join(rows)


def actions_text(tokens: Tokens) -> Text:
    """Return the Actions panel: each VPS action and the diagnosis as a command to copy.

    Args:
        tokens: Token sheet.

    Returns:
        A line saying nothing runs from here, then each action's label,
        command and note.
    """
    out = Text(
        "Copy a command and run it yourself; nothing runs from this screen.",
        style=tokens.muted,
    )
    for action in (*VPS_ACTIONS, DIAGNOSIS):
        out.append("\n")
        out.append(f"{action.label:<{_LABEL_WIDTH + 5}}", style=f"bold {tokens.text}")
        out.append(action.note, style=tokens.dim)
        out.append("\n  ")
        out.append(f"$ {action.command}", style=tokens.live)
    return out
