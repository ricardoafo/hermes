"""The console top bar: mainnet badge, version line and countdown meter (issues #833, #815).

Invariants:

- a live console's mainnet badge follows the config's ``allow_mainnet``
  (off, on, unread) and is never hardcoded (a demo scenario cannot trade and
  always reads off): ``SHADOW · mainnet off``, ``LIVE · mainnet ON`` in
  the down colour reversed, or ``mainnet ?`` in amber;
- the badge leads the status row in every theme (Neon, Control room, night);
  the window bar above it carries no text (issue #1097);
- the version line reads ``v1.0.0-shadow.1 · <release>``, the release form of
  the package version, and shortens before the heartbeat is cut;
- the countdown meter fills with the share of the time from the last bar check
  to the next one, and drops at compact width while the countdown text stays.

Every app here uses synthetic demo data, a frozen clock, motion off and no
settings file; the config reads use the repository's own ``config/`` with an
explicit environment, never the ambient shell.
"""

import asyncio
import io
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rich.style import Style
from rich.text import Text
from textual.pilot import Pilot

import hermes
import hermes.entrypoints.tui as tui_module
from hermes.adapters.console import FixtureReadModel
from hermes.application.console import Heartbeat, HeartbeatState
from hermes.entrypoints.commands.tui import run_tui
from hermes.entrypoints.console import config_allow_mainnet
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import (
    CONTROL_ROOM,
    METER_CAP_LEFT,
    METER_CAP_RIGHT,
    METER_CELLS,
    METER_EMPTY,
    METER_FULL,
    NEON,
)
from hermes.entrypoints.tui_topbar import (
    MAINNET_OFF,
    MAINNET_ON,
    MAINNET_UNKNOWN,
    bar_elapsed_share,
    mainnet_text,
    version_text,
)
from hermes.entrypoints.tui_widgets import Meter
from hermes.release_version import release_label
from tests.support.paths import REPO_ROOT

# The fixture's read time: 14:12 Madrid; last check 14:00, next check 18:00 Madrid.
_DAY = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_LAST = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_NEXT = _LAST + timedelta(hours=4)
_REPO_CONFIG = REPO_ROOT / "config"
_LABEL = "v1.0.0-shadow.1"
_SHA = "679b676"
# 23:30 Madrid: Auto shows the night theme.
_NIGHT = datetime(2026, 10, 3, 21, 30, tzinfo=UTC)
_THEMES = (
    (ThemeChoice.NEON, _DAY),
    (ThemeChoice.CONTROL, _DAY),
    (ThemeChoice.AUTO, _NIGHT),
)
_THEME_IDS = ("neon", "control", "night")


def _ok() -> Heartbeat:
    return Heartbeat(HeartbeatState.OK, _LAST, _LAST, _NEXT)


class _Live:
    """The demo fixture claiming to be live data (no demo scenario), as a ledger model is."""

    def __init__(self) -> None:
        self._inner = FixtureReadModel.load("ok-two-positions")

    @property
    def demo_scenario(self) -> None:
        return None

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _run[T](
    probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]],
    *,
    allow_mainnet: bool | None = False,
    live: bool = False,
    theme: ThemeChoice = ThemeChoice.NEON,
    size: tuple[int, int] = (120, 36),
    clock: datetime = _DAY,
) -> T:
    async def drive() -> T:
        model = _Live() if live else FixtureReadModel.load("ok-two-positions")
        app = HomeApp(
            model,  # type: ignore[arg-type]
            clock=lambda: clock,
            motion=False,
            settings_path=None,
            theme=theme,
            refresh_seconds=3600,
            release=lambda: _SHA,
            version=_LABEL,
            allow_mainnet=allow_mainnet,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return await probe(app, pilot)

    return asyncio.run(drive())


def _row(app: HomeApp, index: int) -> str:
    return app.screen._compositor.render_strips()[index].text.rstrip()


# ----------------------------------------------------------------------
# Mainnet badge
# ----------------------------------------------------------------------


def test_mainnet_off_reads_shadow_in_the_muted_label_colour() -> None:
    """``allow_mainnet`` false: the badge reads ``SHADOW · mainnet off`` in ``muted``."""
    badge = mainnet_text(False, NEON)
    assert (badge.plain, str(badge.style)) == ("SHADOW · mainnet off", NEON.muted)


def test_mainnet_on_reads_live_reversed_on_the_down_colour() -> None:
    """``allow_mainnet`` true: `` LIVE · mainnet ON `` bold, void text on the down colour."""
    badge = mainnet_text(True, NEON)
    assert (badge.plain, str(badge.style)) == (
        " LIVE · mainnet ON ",
        f"bold {NEON.void} on {NEON.down}",
    )


def test_mainnet_unknown_reads_a_question_mark_in_amber() -> None:
    """An unread config: the badge reads ``mainnet ?`` in the bold ``warn`` colour."""
    badge = mainnet_text(None, CONTROL_ROOM)
    assert (badge.plain, str(badge.style)) == ("mainnet ?", f"bold {CONTROL_ROOM.warn}")


def test_the_badge_constants_are_the_design_spec_words() -> None:
    """The three badge texts are the ones the design spec and issue #833 name."""
    assert (MAINNET_OFF, MAINNET_ON, MAINNET_UNKNOWN) == (
        "SHADOW · mainnet off",
        "LIVE · mainnet ON",
        "mainnet ?",
    )


def test_config_with_mainnet_unset_reads_off() -> None:
    """The repository config without ``HERMES_ALLOW_MAINNET`` loads with mainnet off."""
    assert config_allow_mainnet({"HERMES_CONFIG_DIR": str(_REPO_CONFIG)}) is False


def test_config_with_mainnet_set_in_prod_reads_on() -> None:
    """``HERMES_ENV=prod`` and ``HERMES_ALLOW_MAINNET=true``: the config says mainnet is on."""
    env = {
        "HERMES_CONFIG_DIR": str(_REPO_CONFIG),
        "HERMES_ENV": "prod",
        "HERMES_ALLOW_MAINNET": "true",
    }
    assert config_allow_mainnet(env) is True


def test_config_that_does_not_load_reads_unknown() -> None:
    """A missing config dir, or mainnet set outside prod (serve refuses it), reads ``None``."""
    outside_prod = {"HERMES_CONFIG_DIR": str(_REPO_CONFIG), "HERMES_ALLOW_MAINNET": "true"}
    missing = {"HERMES_CONFIG_DIR": str(_REPO_CONFIG / "does-not-exist")}
    assert (config_allow_mainnet(outside_prod), config_allow_mainnet(missing)) == (None, None)


@pytest.mark.parametrize(("theme", "now"), _THEMES, ids=_THEME_IDS)
def test_every_theme_status_row_starts_with_the_badge(theme: ThemeChoice, now: datetime) -> None:
    """Neon, Control room and night: the status row starts ``SHADOW · mainnet off`` (#1097)."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str]:
        del pilot
        return app.query_one("#mainnet").display, _row(app, 2)

    shown, row = _run(probe, theme=theme, clock=now)
    assert (shown, row.startswith("SHADOW · mainnet off  DEMO DATA")) == (True, True)


def test_control_room_status_row_shows_mainnet_on() -> None:
    """Config says mainnet is on: the Control room row starts `` LIVE · mainnet ON ``."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.plain_text("mainnet").strip()

    assert (
        _run(probe, theme=ThemeChoice.CONTROL, allow_mainnet=True, live=True) == "LIVE · mainnet ON"
    )


def test_neon_status_row_shows_mainnet_unknown() -> None:
    """Config unread: the Neon status row starts ``mainnet ?``."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return _row(app, 2)

    assert _run(probe, allow_mainnet=None, live=True).startswith("mainnet ? ")


def test_demo_scenario_reads_mainnet_off_whatever_the_config_says() -> None:
    """A demo cannot trade: unread or "on" config, the demo badge still reads mainnet off."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool | None, str]:
        del pilot
        return app.mainnet_state, _row(app, 2)

    unread = _run(probe, theme=ThemeChoice.CONTROL, allow_mainnet=None)
    on = _run(probe, theme=ThemeChoice.CONTROL, allow_mainnet=True)
    assert (unread[0], on[0], unread[1].startswith("SHADOW · mainnet off")) == (False, False, True)


def test_live_console_shows_the_config_state() -> None:
    """A live console (no demo scenario) shows exactly what the config said: on, off or unread."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> bool | None:
        del pilot
        return app.mainnet_state

    assert [_run(probe, allow_mainnet=state, live=True) for state in (True, False, None)] == [
        True,
        False,
        None,
    ]


# ----------------------------------------------------------------------
# Version line
# ----------------------------------------------------------------------


def test_version_line_names_the_release_form_and_the_release() -> None:
    """Label and release: ``v1.0.0-shadow.1 · 679b676``; each alone; legacy without a label."""
    assert [
        version_text(_LABEL, _SHA),
        version_text(_LABEL, None),
        version_text(None, _SHA),
        version_text(None, None),
    ] == ["v1.0.0-shadow.1 · 679b676", "v1.0.0-shadow.1", "release 679b676", ""]


def test_package_version_shows_in_its_release_form() -> None:
    """Package ``1.0.0a1`` is labelled ``v1.0.0-shadow.1`` (ADR-022), never the raw version."""
    assert release_label("1.0.0a1") == "v1.0.0-shadow.1"


def test_status_row_shows_the_version_beside_the_release() -> None:
    """At 160 columns the status row reads ``v1.0.0-shadow.1 · 679b676`` after the demo tag."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.plain_text("release").strip()

    assert _run(probe, size=(160, 45)) == "v1.0.0-shadow.1 · 679b676"


@pytest.mark.parametrize(("theme", "now"), _THEMES, ids=_THEME_IDS)
def test_compact_row_drops_the_scenario_name_before_the_heartbeat(
    theme: ThemeChoice, now: datetime
) -> None:
    """At 80 columns in every theme: the badge stays, the demo tag loses its scenario name."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return _row(app, 2)

    row = _run(probe, size=(80, 24), theme=theme, clock=now)
    assert row == "SHADOW · mainnet off  DEMO DATA          ● live · checked 14:00 · next 18:00  ◑"


def test_status_row_drops_the_release_before_the_version() -> None:
    """Control room at 120 columns: ``v1.0.0-shadow.1`` stays, `` · 679b676`` goes."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.plain_text("release").strip()

    assert _run(probe, theme=ThemeChoice.CONTROL) == "v1.0.0-shadow.1"


def test_tui_command_hands_the_app_the_release_label_and_the_config_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``hermes tui`` passes ``release_label(__version__)`` and the config's mainnet flag."""
    seen: dict[str, object] = {}

    def fake_run_home(model: object, **kwargs: object) -> None:
        del model
        seen.update(kwargs)

    monkeypatch.setattr(tui_module, "run_home", fake_run_home)
    env = {"HERMES_CONFIG_DIR": str(_REPO_CONFIG)}
    code = run_tui([], io.StringIO(), demo_flag="ok-two-positions", env=env, settings_path=Path())
    assert (code, seen["version"], seen["allow_mainnet"]) == (
        0,
        release_label(hermes.__version__),
        False,
    )


# ----------------------------------------------------------------------
# Countdown meter
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("after_last", "share", "cells"),
    [
        (timedelta(0), 0.0, 0),
        (timedelta(hours=1, minutes=12), 0.3, 3),
        (timedelta(hours=2), 0.5, 5),
        (timedelta(hours=3, minutes=36), 0.9, 9),
        (timedelta(hours=4), 1.0, 10),
        (timedelta(hours=5), 1.0, 10),
    ],
    ids=["at-the-check", "18-percent-in", "half-way", "90-percent", "due", "past-due"],
)
def test_meter_fill_is_the_share_of_the_bar_passed(
    after_last: timedelta, share: float, cells: int
) -> None:
    """The share is time since the last check over last-to-next; ten cells fill that share."""
    got = bar_elapsed_share(_ok(), _LAST + after_last)
    assert got is not None
    assert (round(got, 6), Meter(got, width=METER_CELLS).filled) == (share, cells)


def test_meter_has_no_share_when_hermes_is_late_down_or_unknown() -> None:
    """Late, down or no heartbeat: there is no share to draw (the countdown is empty too)."""
    late = Heartbeat(HeartbeatState.LATE, _NEXT, _LAST, _NEXT)
    down = Heartbeat(HeartbeatState.DOWN, _NEXT, _LAST, _NEXT, down_since=_NEXT)
    flat = Heartbeat(HeartbeatState.OK, _LAST, _LAST, _LAST)
    assert [bar_elapsed_share(hb, _DAY) for hb in (late, down, None, flat)] == [None] * 4


def test_wide_status_row_draws_the_meter_after_the_countdown() -> None:
    """At 240 columns, 16:00 Madrid: ``in 2h 00m 00s`` then a capped meter half full."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        del pilot
        return app.plain_text("countdown").strip(), app.plain_text("countdown-meter").strip()

    countdown, meter = _run(probe, size=(240, 60), clock=_LAST + timedelta(hours=2))
    assert (countdown, meter) == (
        "in 2h 00m 00s",
        METER_CAP_LEFT + METER_FULL * 5 + METER_EMPTY * 5 + METER_CAP_RIGHT,
    )


def test_meter_cells_use_the_gain_gradient_tokens() -> None:
    """The first full cell takes the theme's first ``gain`` stop, the empty cells ``line``."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[Style | str]:
        del pilot
        content = app._content["countdown-meter"]
        assert isinstance(content, Text)
        return [content.get_style_at_offset(app.console, offset) for offset in (2, 7)]

    first, empty = _run(probe, size=(240, 60), clock=_LAST + timedelta(hours=2))
    assert (str(first), str(empty)) == (
        str(Style.parse(NEON.gain[0])),
        str(Style.parse(NEON.line)),
    )


def test_compact_width_drops_the_meter_and_keeps_the_countdown_text() -> None:
    """At 80 columns there is no meter; the footer still reads ``next 3h 48m 00s``."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        del pilot
        return app.plain_text("countdown-meter").strip(), _row(app, 23)

    meter, footer = _run(probe, size=(80, 24))
    assert (meter, footer.endswith("next 3h 48m 00s · read-only")) == ("", True)
