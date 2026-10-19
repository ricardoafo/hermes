"""The System tab's state, cursor and painting, split out of ``HomeApp`` (issue #1184).

:class:`~hermes.entrypoints.tui.HomeApp` stays the router and the timer owner; this
controller keeps the last System reads, the service / release cursor and the
service pane, and draws the five panels with the builders in
:mod:`hermes.entrypoints.tui_system`. The app hands it only what it needs to
draw: a :class:`SystemFrame` per paint and four narrow callables.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from decimal import Decimal
from functools import partial
from typing import Final

from rich.console import RenderableType
from textual.screen import Screen

from hermes.application.console import Heartbeat
from hermes.application.console.live_rate import LiveRate
from hermes.application.console.logs import LogLine
from hermes.application.console.system import MachineStats, ReleaseHistory, ServiceStatus
from hermes.entrypoints.tui_drilldown import (
    DetailMode,
    DetailPane,
    DetailScreen,
    ListState,
    detail_mode,
)
from hermes.entrypoints.tui_reads import SystemReads
from hermes.entrypoints.tui_system import (
    RELEASES_TRAIL,
    SERVICES_TRAIL,
    actions_text,
    machine_text,
    release_detail,
    releases_text,
    service_detail,
    services_text,
    timing_text,
)
from hermes.entrypoints.tui_theme import Tokens
from hermes.entrypoints.tui_widgets import HudPanel

__all__ = ["SystemFrame", "SystemTabController"]

_SERVICE: Final = "service:"
_RELEASE: Final = "release:"
_SERVICE_JOURNAL_LINES: Final = 12

type UnitJournal = Callable[[str, int], Sequence[LogLine] | None]
type SetContent = Callable[[str, RenderableType], None]
type PushScreen = Callable[[Screen[ListState], Callable[[ListState | None], None]], object]


@dataclass(frozen=True, slots=True)
class SystemFrame:
    """What the app knows at one paint that the System screen draws from.

    Attributes:
        now: The console clock.
        tz: The operator's time zone.
        tokens: The token sheet of the shown theme.
        heartbeat: The last heartbeat read.
        eur_rate_date: The day of the EUR rate in use.
        wide: Whether the terminal is wide enough for a detail beside the list.
        width: The terminal width in cells.
        on_system: Whether the System tab is the shown tab.
        live_rate: Kraken's USDT/EUR quote beside the ECB rate, when the console has one.
    """

    now: datetime
    tz: tzinfo
    tokens: Tokens
    heartbeat: Heartbeat | None
    eur_rate_date: date | None
    wide: bool
    width: int
    on_system: bool
    live_rate: LiveRate | None = None


class SystemTabController:
    """Own the System tab's reads, cursor and service pane.

    Args:
        unit_journal: Returns a unit's last journal lines, or ``None``; ``None`` when
            the journal cannot be read here.
        frame: Builds the :class:`SystemFrame` of the moment.
        home: Returns the home screen the panels live on.
        set_content: Updates a ``Static`` and remembers what it shows.
        push_screen: Pushes a screen with a callback for its result.
        chips: Footer key chips of the detail screens.
    """

    def __init__(
        self,
        *,
        unit_journal: UnitJournal | None,
        frame: Callable[[], SystemFrame],
        home: Callable[[], Screen[object]],
        set_content: SetContent,
        push_screen: PushScreen,
        chips: Sequence[tuple[str, str]],
    ) -> None:
        """Start with nothing read and no cursor."""
        self._unit_journal = unit_journal
        self._frame = frame
        self._home = home
        self._set = set_content
        self._push_screen = push_screen
        self._chips = tuple(chips)
        self._cursor: str | None = None
        self._service_open = False
        self._services: tuple[ServiceStatus, ...] | None = None
        self._releases: ReleaseHistory | None = None
        self._machine: MachineStats | None = None
        self._bar_seconds: tuple[Decimal, ...] | None = None

    @property
    def service_open(self) -> bool:
        """Return whether the service pane is open."""
        return self._service_open

    def keep(self, reads: SystemReads) -> None:
        """Keep the last reads for the cursor and the detail screens."""
        self._services = reads.services
        self._releases = reads.releases
        self._machine = reads.machine
        self._bar_seconds = reads.bar_seconds

    def close_service(self) -> None:
        """Close the service pane and draw the panels again."""
        self._service_open = False
        self.paint()

    def step(self, step: int) -> None:
        """Move the cursor along services then releases, wrapping around."""
        keys = self._keys()
        if not keys:
            return
        current = keys.index(self._cursor) if self._cursor in keys else 0
        self._cursor = keys[(current + step) % len(keys)]
        self.paint()

    def paint(self) -> None:
        """Draw the System panels from the last read; moving the cursor reads nothing."""
        keys = self._keys()
        if self._cursor not in keys:
            self._cursor = keys[0] if keys else None
        frame = self._frame()
        service = self._selected(_SERVICE)
        release = self._selected(_RELEASE)
        home = self._home()
        home.query_one("#system-body").set_class(frame.wide, "-split")
        self._set("sys-services", services_text(self._services, service, frame.tz, frame.tokens))
        self._set("sys-releases", releases_text(self._releases, release, frame.tz, frame.tokens))
        self._set("sys-actions", actions_text(frame.tokens))
        self._set(
            "sys-timing",
            timing_text(
                frame.heartbeat,
                eur_rate_date=frame.eur_rate_date,
                bar_seconds=self._bar_seconds,
                now=frame.now,
                tz=frame.tz,
                tokens=frame.tokens,
                live_rate=frame.live_rate,
            ),
        )
        self._set("sys-machine", machine_text(self._machine, frame.now, frame.tz, frame.tokens))
        services_panel = home.query_one("#sys-services-panel", HudPanel)
        services_panel.active = frame.on_system and service is not None
        releases_panel = home.query_one("#sys-releases-panel", HudPanel)
        releases_panel.active = frame.on_system and release is not None
        shown = (
            self._service_open
            and service is not None
            and detail_mode(frame.width, beside=frame.wide) is DetailMode.PANE
        )
        home.query_one("#sys-service-pane", DetailPane).is_open = shown
        if shown and service is not None:
            self._set("sys-service-detail", self._service_content(service))

    def open_detail(self) -> None:
        """Open the selected service (pane, or screen when compact) or release (screen)."""
        service = self._selected(_SERVICE)
        release = self._selected(_RELEASE)
        if service is not None:
            frame = self._frame()
            if detail_mode(frame.width, beside=frame.wide) is DetailMode.PANE:
                self._service_open = True
                self.paint()
                return
            self._push_screen(
                DetailScreen(
                    trail=SERVICES_TRAIL,
                    keys=self._service_names,
                    current=service,
                    body=self._service_content,
                    title="Service detail",
                    state=ListState(cursor=service),
                    chips=self._chips,
                ),
                partial(self._restore_cursor, _SERVICE),
            )
        elif release is not None:
            self._push_screen(
                DetailScreen(
                    trail=RELEASES_TRAIL,
                    keys=self._release_names,
                    current=release,
                    body=self._release_content,
                    title="Release detail",
                    state=ListState(cursor=release),
                    chips=self._chips,
                ),
                partial(self._restore_cursor, _RELEASE),
            )

    def _service_names(self) -> list[str]:
        return [status.unit.name for status in self._services or ()]

    def _release_names(self) -> list[str]:
        return [] if self._releases is None else list(self._releases.names())

    def _keys(self) -> list[str]:
        return [f"{_SERVICE}{name}" for name in self._service_names()] + [
            f"{_RELEASE}{name}" for name in self._release_names()
        ]

    def _selected(self, prefix: str) -> str | None:
        cursor = self._cursor
        if cursor is None or not cursor.startswith(prefix):
            return None
        return cursor.removeprefix(prefix)

    def _service_content(self, name: str) -> RenderableType:
        frame = self._frame()
        unit = next((s.unit for s in self._services or () if s.unit.name == name), None)
        reader = self._unit_journal
        journal = (
            None if unit is None or reader is None else reader(unit.unit, _SERVICE_JOURNAL_LINES)
        )
        return service_detail(
            self._services, name, journal=journal, now=frame.now, tz=frame.tz, tokens=frame.tokens
        )

    def _release_content(self, name: str) -> RenderableType:
        frame = self._frame()
        return release_detail(self._releases, name, frame.now, frame.tz, frame.tokens)

    def _restore_cursor(self, prefix: str, state: ListState | None) -> None:
        if state is not None and state.cursor is not None:
            self._cursor = f"{prefix}{state.cursor}"
        self.paint()
