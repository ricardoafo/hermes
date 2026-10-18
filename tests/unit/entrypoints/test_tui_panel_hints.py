"""Every console panel's key hint lines up with its body and reads in every theme (issue #1096).

Invariants:

- on every screen and in every theme, the first character of a panel's key
  hint sits in the panel's body column (one cell in from the panel edge),
  not one column to the right of the text above it;
- the hint is drawn in ``dim``, which reads at 4.5:1 or more on the panel
  and the focused panel of that theme.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from textual.widgets import Static

from hermes.adapters.console import FixtureReadModel
from hermes.entrypoints.tui import TABS, HomeApp
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import (
    CONTROL_THEME,
    NEON_THEME,
    NIGHT_THEME,
    TEXT_MIN_CONTRAST,
    contrast_ratio,
    tokens_for,
)
from hermes.entrypoints.tui_widgets import HudPanel

_DAY = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
# 00:30 Madrid: Auto shows the night theme.
_NIGHT = datetime(2026, 10, 3, 22, 30, tzinfo=UTC)
_THEMES = {
    NEON_THEME: (ThemeChoice.NEON, _DAY),
    CONTROL_THEME: (ThemeChoice.CONTROL, _DAY),
    NIGHT_THEME: (ThemeChoice.AUTO, _NIGHT),
}
_SIZE = (160, 50)


@dataclass(frozen=True)
class Hint:
    """Where one panel's hint and body start, relative to the panel's left edge."""

    screen: str
    panel: str
    hint_column: int
    body_column: int
    hint_colour: str


async def _hints(theme: str) -> list[Hint]:
    choice, now = _THEMES[theme]
    app = HomeApp(
        FixtureReadModel.load("ok-two-positions"), clock=lambda: now, motion=False, theme=choice
    )
    found: list[Hint] = []
    async with app.run_test(size=_SIZE) as pilot:
        await pilot.pause()
        for tab in TABS:
            await pilot.press(tab.key)
            await pilot.pause()
            assert app.theme == theme
            strips = list(app.screen._compositor.render_strips())
            for panel in app.screen.query(HudPanel).results():
                if not panel.subtitle or not panel.region.width:
                    continue
                edge = panel.query_one(".hud-bottom", Static)
                body = next(child for child in panel.children if child.has_class("hud-body"))
                strip = strips[edge.region.y]
                hint = next(segment for segment in strip if panel.subtitle in segment.text)
                colour = hint.style.color if hint.style is not None else None
                found.append(
                    Hint(
                        screen=tab.name,
                        panel=panel.panel_title,
                        hint_column=strip.text.index(panel.subtitle, panel.region.x)
                        - panel.region.x,
                        body_column=body.content_region.x - panel.region.x,
                        hint_colour=colour.triplet.hex.upper() if colour and colour.triplet else "",
                    )
                )
    return found


@pytest.mark.parametrize("theme", list(_THEMES))
def test_every_panel_hint_starts_in_the_body_column(theme: str) -> None:
    """A hint's first letter sits under the body text's first letter, on every screen."""
    hints = asyncio.run(_hints(theme))
    assert len({hint.screen for hint in hints}) >= 3, hints
    assert [(hint.screen, hint.panel, hint.hint_column) for hint in hints] == [
        (hint.screen, hint.panel, 1) for hint in hints
    ]
    assert {hint.body_column for hint in hints} == {1}


@pytest.mark.parametrize("theme", list(_THEMES))
def test_every_panel_hint_reads_at_4_5_to_1(theme: str) -> None:
    """Every hint is drawn in the theme's ``dim``, at 4.5:1 or more on both panel surfaces."""
    tokens = tokens_for(theme)
    hints = asyncio.run(_hints(theme))
    assert {hint.hint_colour for hint in hints} == {tokens.dim}
    assert (
        contrast_ratio(tokens.dim, tokens.panel) >= TEXT_MIN_CONTRAST,
        contrast_ratio(tokens.dim, tokens.panel_focus) >= TEXT_MIN_CONTRAST,
    ) == (True, True)
