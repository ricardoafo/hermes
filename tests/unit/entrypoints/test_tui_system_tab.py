"""The System tab controller keeps its own cursor and service pane (issue #1184).

Invariants:

- with services and releases read, the cursor starts on the first service and
  the services panel is the active one;
- stepping forward from the last release wraps to the first service, and
  stepping back from the first service wraps to the last release;
- with nothing read, stepping draws nothing and keeps no cursor;
- Enter on a service opens the pane beside the list when wide and closing it
  hides the pane again;
- Enter on a service on a compact terminal pushes a detail screen instead.

The whole-app behaviour (keys, breadcrumbs, snapshots) stays in
``test_tui_system.py``; here the app is replaced by recording stubs.
"""

from datetime import UTC, date, datetime
from typing import cast
from zoneinfo import ZoneInfo

from rich.console import RenderableType
from textual.screen import Screen

from hermes.application.console.system import (
    SERVICE_UNITS,
    LiveRelease,
    ReleaseEntry,
    ReleaseHistory,
    ServiceStatus,
)
from hermes.entrypoints.tui_drilldown import DetailScreen
from hermes.entrypoints.tui_reads import SystemReads
from hermes.entrypoints.tui_system_tab import SystemFrame, SystemTabController
from hermes.entrypoints.tui_theme import BASE

_NOW = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_LIVE = "2bc2798a1f00"
_PREVIOUS = "9f01a3c5d2e1"
_WIDE = 160
_COMPACT = 80


class _Widget:
    """A stand-in for the panels and the pane the controller flips."""

    def __init__(self) -> None:
        self.active = False
        self.is_open = False
        self.classes: dict[str, bool] = {}

    def set_class(self, add: bool, name: str) -> None:
        self.classes[name] = add


class _Home:
    """A stand-in home screen that hands out one stub per selector."""

    def __init__(self) -> None:
        self.widgets: dict[str, _Widget] = {}

    def query_one(self, selector: str, kind: object = None) -> _Widget:
        return self.widgets.setdefault(selector, _Widget())


class _Rig:
    """The controller wired to recording stubs."""

    def __init__(self, *, width: int = _WIDE) -> None:
        self.width = width
        self.home = _Home()
        self.set_calls: list[str] = []
        self.pushed: list[object] = []
        self.controller = SystemTabController(
            unit_journal=None,
            frame=self._frame,
            home=lambda: cast("Screen[object]", self.home),
            set_content=self._set,
            push_screen=lambda screen, _callback: self.pushed.append(screen),
            chips=(("Esc", "back"),),
        )

    def _frame(self) -> SystemFrame:
        return SystemFrame(
            now=_NOW,
            tz=ZoneInfo("Europe/Madrid"),
            tokens=BASE,
            heartbeat=None,
            eur_rate_date=date(2026, 10, 3),
            wide=self.width >= _WIDE,
            width=self.width,
            on_system=True,
        )

    def _set(self, widget_id: str, _content: RenderableType) -> None:
        self.set_calls.append(widget_id)

    def panel(self, selector: str) -> _Widget:
        return self.home.query_one(selector)


def _services() -> tuple[ServiceStatus, ...]:
    return tuple(
        ServiceStatus(unit, installed=True, active="active", sub="running", since=_NOW)
        for unit in SERVICE_UNITS
    )


def _history() -> ReleaseHistory:
    return ReleaseHistory(
        live=LiveRelease(_LIVE, _NOW),
        previous=_PREVIOUS,
        entries=(ReleaseEntry(_LIVE, _NOW), ReleaseEntry(_PREVIOUS, None)),
    )


def _read(rig: _Rig) -> None:
    rig.controller.keep(SystemReads(services=_services(), releases=_history()))


def test_the_cursor_starts_on_the_first_service_and_lights_its_panel() -> None:
    """The first paint after a read selects the first service, not a release."""
    rig = _Rig()
    _read(rig)
    rig.controller.paint()
    assert rig.panel("#sys-services-panel").active is True
    assert rig.panel("#sys-releases-panel").active is False


def test_stepping_forward_from_the_last_release_wraps_to_the_first_service() -> None:
    """Keys run services then releases; one past the last release is the first service."""
    rig = _Rig()
    _read(rig)
    rig.controller.paint()
    for _ in range(len(SERVICE_UNITS) + 1):  # services, then both releases: the last one
        rig.controller.step(1)
    assert rig.panel("#sys-releases-panel").active is True
    rig.controller.step(1)
    assert rig.panel("#sys-services-panel").active is True
    assert rig.panel("#sys-releases-panel").active is False


def test_stepping_back_from_the_first_service_wraps_to_the_last_release() -> None:
    """The list is a ring: back from the top lands on the final release."""
    rig = _Rig()
    _read(rig)
    rig.controller.paint()
    rig.controller.step(-1)
    assert rig.panel("#sys-releases-panel").active is True
    assert rig.panel("#sys-services-panel").active is False


def test_stepping_with_nothing_read_draws_nothing() -> None:
    """No services and no releases means no keys: a step is a no-op."""
    rig = _Rig()
    rig.controller.step(1)
    assert rig.set_calls == []


def test_enter_on_a_service_opens_the_pane_when_wide_and_close_hides_it() -> None:
    """Wide terminals show a service's detail beside the list; Esc hides it."""
    rig = _Rig(width=_WIDE)
    _read(rig)
    rig.controller.paint()
    rig.controller.open_detail()
    assert rig.controller.service_open is True
    assert rig.panel("#sys-service-pane").is_open is True
    rig.controller.close_service()
    assert rig.controller.service_open is False
    assert rig.panel("#sys-service-pane").is_open is False


def test_enter_on_a_service_when_compact_pushes_a_detail_screen() -> None:
    """Compact terminals have no room beside the list, so the detail is its own screen."""
    rig = _Rig(width=_COMPACT)
    _read(rig)
    rig.controller.paint()
    rig.controller.open_detail()
    assert rig.controller.service_open is False
    assert len(rig.pushed) == 1
    assert isinstance(rig.pushed[0], DetailScreen)
