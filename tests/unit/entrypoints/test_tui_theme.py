"""The Cockpit console's design tokens (design spec, issue #711).

Invariants:

- data colours are semantic in every theme: up blue (70,140,230), down orange
  (240,120,20), warning amber; Neon restyles only the chrome;
- night keeps the hues of gains and losses in darker shades and warnings fully
  bright;
- every colour that draws text reads at 4.5:1 or more on the void, panel,
  focused panel and overlay of every theme, tab numbers and the theme chip
  included, and labels stay brighter than hints;
- night mode runs from 23:00 up to 08:00 local, and only Auto follows it;
- the breakpoints are compact < 100, standard 100-159, wide 160-219, ultra >= 220
  columns, with height classes at 30 and 45 rows;
- ``HERMES_CONSOLE_MOTION=off`` turns animations off;
- the Neon tokens are the values the Code Owner agreed.
"""

from datetime import time

import pytest
from hypothesis import given
from hypothesis import strategies as st
from rich.color import Color
from rich.style import Style

from hermes.entrypoints import tui_theme as theme
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import (
    CONTROL_ROOM,
    HERO_MIN_CONTRAST,
    NEON,
    NIGHT,
    TEXT_MIN_CONTRAST,
    Tokens,
    contrast_ratio,
)
from hermes.entrypoints.tui_widgets import Tab, tab_text, theme_chip_text, top_edge

_HEX = st.from_regex(r"#[0-9A-F]{6}", fullmatch=True)


def _luminance(hex_colour: str) -> float:
    red, green, blue = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def test_data_colours_are_the_colour_blind_palette_in_neon_and_control_room() -> None:
    """Up, down and warning are the same semantic colours in both day themes."""
    picked = {
        (sheet.up, sheet.down, sheet.warn) for sheet in (theme.NEON, theme.CONTROL_ROOM, theme.BASE)
    }
    assert picked == {("#468CE6", "#F07814", "#E6BE28")}


def test_ansi_palette_maps_red_to_orange_and_green_to_blue() -> None:
    """Rich content's red reads orange (240,120,20) and its green reads blue (70,140,230)."""
    palette = theme.terminal_theme(theme.BASE).ansi_colors
    assert (tuple(palette[1]), tuple(palette[2]), tuple(palette[3])) == (
        (240, 120, 20),
        (70, 140, 230),
        (230, 190, 40),
    )


def test_data_is_coloured_from_the_base_sheet_under_neon() -> None:
    """Neon never colours the data: its data sheet is the base sheet, not the Neon one."""
    assert (
        theme.data_tokens(theme.NEON_THEME),
        theme.data_tokens(theme.CONTROL_THEME),
        theme.data_tokens(theme.NIGHT_THEME),
    ) == (theme.BASE, theme.BASE, theme.NIGHT)


def test_night_darkens_gains_and_losses_but_keeps_warnings_bright() -> None:
    """At night up and down are darker than by day; warn is unchanged."""
    assert (
        _luminance(theme.NIGHT.up) < _luminance(theme.BASE.up),
        _luminance(theme.NIGHT.down) < _luminance(theme.BASE.down),
        theme.NIGHT.warn,
    ) == (True, True, theme.BASE.warn)


def test_neon_overrides_are_the_agreed_values() -> None:
    """The Neon sheet carries the round 3 overrides from the design spec."""
    neon = theme.NEON
    assert (
        neon.void,
        neon.panel,
        neon.panel_focus,
        neon.line,
        neon.live,
        neon.ai,
        neon.frame,
        neon.text,
        neon.muted,
        neon.dim,
        neon.chrome,
        neon.brand,
    ) == (
        "#0B0420",
        "#120731",
        "#170A3D",
        "#4A2399",
        "#00F0FF",
        "#FF3DF0",
        "#8A5CFF",
        "#C8FBFF",
        "#8FA8D8",
        "#798BB6",
        ("#FF3DF0", "#8A5CFF", "#00F0FF"),
        ("#00F0FF", "#8A5CFF", "#FF3DF0"),
    )


def test_three_textual_themes_are_built_from_the_sheets() -> None:
    """The themes are named hermes-neon, hermes-control-room and hermes-night."""
    built = {item.name: (item.background, item.primary, item.success) for item in theme.THEMES}
    assert built == {
        "hermes-neon": ("#0B0420", "#00F0FF", "#468CE6"),
        "hermes-control-room": ("#070B14", "#58D1EB", "#468CE6"),
        "hermes-night": (theme.NIGHT.void, theme.NIGHT.live, theme.NIGHT.up),
    }


def test_theme_variables_expose_every_hud_token() -> None:
    """TCSS sees the tokens as $hud-* variables with the sheet's values.

    Issue #712 added ``hud-hero-digits`` (the big numbers' digit colour), so 16.
    """
    variables = theme.theme_variables(theme.NEON)
    assert (
        variables["hud-live"],
        variables["hud-ai"],
        variables["hud-line"],
        variables["hud-hero-digits"],
        len(variables),
    ) == ("#00F0FF", "#FF3DF0", "#4A2399", "#C8FBFF", 16)


@pytest.mark.parametrize(
    ("local", "night"),
    [
        (time(22, 59, 59), False),
        (time(23, 0), True),
        (time(3, 0), True),
        (time(7, 59, 59), True),
        (time(8, 0), False),
        (time(12, 0), False),
    ],
)
def test_night_runs_from_23_to_8(local: time, night: bool) -> None:
    """Night mode starts at 23:00 and ends at 08:00 local."""
    assert theme.is_night(local) is night


def test_only_auto_follows_the_night() -> None:
    """At 23:30 Auto shows night; Neon and Control room stay what the operator picked."""
    late = time(23, 30)
    assert (
        theme.resolve_theme(ThemeChoice.AUTO, late),
        theme.resolve_theme(ThemeChoice.AUTO, time(12, 0)),
        theme.resolve_theme(ThemeChoice.NEON, late),
        theme.resolve_theme(ThemeChoice.CONTROL, late),
    ) == ("hermes-night", "hermes-neon", "hermes-neon", "hermes-control-room")


def test_breakpoints_are_the_agreed_widths_and_heights() -> None:
    """Width classes at 0/100/160/220 and height classes at 0/30/45."""
    assert (theme.HORIZONTAL_BREAKPOINTS, theme.VERTICAL_BREAKPOINTS) == (
        [(0, "-compact"), (100, "-standard"), (160, "-wide"), (220, "-ultra")],
        [(0, "-short"), (30, "-tall"), (45, "-towering")],
    )


@pytest.mark.parametrize(
    ("value", "enabled"),
    [("off", False), ("OFF", False), (" 0 ", False), ("false", False), ("no", False), ("on", True)],
)
def test_motion_env_turns_animations_off(value: str, enabled: bool) -> None:
    """HERMES_CONSOLE_MOTION=off (or 0/false/no) stops the animations."""
    assert theme.motion_enabled({"HERMES_CONSOLE_MOTION": value}) is enabled


def test_motion_is_on_without_the_variable() -> None:
    """Without the variable the console animates."""
    assert theme.motion_enabled({}) is True


def test_pulse_is_four_steps_over_one_point_six_seconds() -> None:
    """The live dot cycles bright, fading, faint, fading over a 1.6 s period."""
    steps = theme.pulse_steps(theme.NEON)
    assert (steps[0], steps[1] == steps[3], len(steps), theme.MOTION.pulse_period) == (
        "#00F0FF",
        True,
        4,
        1.6,
    )


def test_night_pulse_is_slower() -> None:
    """The night pulse period is longer than the day one."""
    assert theme.MOTION.pulse_period_night > theme.MOTION.pulse_period


def test_clock_ticks_every_second_and_the_model_every_five() -> None:
    """The clock ticks at 1 s locally; the read model is polled every 5 s."""
    assert (theme.MOTION.clock_tick, theme.MOTION.poll) == (1.0, 5.0)


@given(start=_HEX, end=_HEX)
def test_blend_ends_at_its_stops(start: str, end: str) -> None:
    """Blending 0 of the way gives the start and all the way gives the end."""
    assert (theme.blend(start, end, 0.0), theme.blend(start, end, 1.0)) == (start, end)


@given(start=_HEX, end=_HEX, amount=st.floats(0, 1))
def test_blend_stays_between_its_stops(start: str, end: str, amount: float) -> None:
    """Every channel of a blend lies between the two stops' channels."""
    mixed = theme.blend(start, end, amount)
    channels = [
        (int(start[i : i + 2], 16), int(mixed[i : i + 2], 16), int(end[i : i + 2], 16))
        for i in (1, 3, 5)
    ]
    outside = [(a, m, b) for a, m, b in channels if not min(a, b) <= m <= max(a, b)]
    assert outside == []


def test_gradient_passes_through_its_middle_stop() -> None:
    """Halfway along a three-stop gradient is the middle stop."""
    assert theme.gradient(theme.NEON.chrome, 0.5) == "#8A5CFF"


def test_unknown_theme_draws_with_neon_tokens() -> None:
    """A theme picked outside hermes falls back to the Neon sheet."""
    assert theme.tokens_for("textual-dark") is theme.NEON


def test_wordmark_stands_in_until_the_logo_is_chosen() -> None:
    """With no logo chosen, the start-up slot shows the block-letter HERMES wordmark."""
    assert (theme.LOGO, theme.startup_logo()) == (None, theme.WORDMARK)


def test_ai_glyph_is_the_four_pointed_star() -> None:
    """Anything the AI supervisor wrote is marked with ✦: the AI panel title starts with it."""
    assert top_edge("Brief", "", 20, theme.NEON, ai=True).plain.startswith("┏━ ✦ BRIEF ")


@pytest.mark.parametrize(
    ("name", "tokens"), [("neon", NEON), ("control room", CONTROL_ROOM), ("night", NIGHT)]
)
def test_big_total_digits_read_at_7_to_1_on_their_panel(name: str, tokens: Tokens) -> None:
    """Every big number's digits clear WCAG 7:1 on their panel in every theme.

    Checked on the panel and on the focused panel (the Code Owner, 2026-10-06:
    gradient digits were illegible).
    """
    del name
    assert (
        contrast_ratio(tokens.hero_digits, tokens.panel) >= HERO_MIN_CONTRAST,
        contrast_ratio(tokens.hero_digits, tokens.panel_focus) >= HERO_MIN_CONTRAST,
    ) == (True, True)


_SHEETS = (
    (theme.NEON_THEME, NEON),
    (theme.CONTROL_THEME, CONTROL_ROOM),
    (theme.NIGHT_THEME, NIGHT),
)
_TEXT_TOKENS = ("text", "muted", "dim", "live", "up", "down", "warn", "ai", "frame_text")
_TEXT_CASES = [
    pytest.param(name, source, token, id=f"{name}-{source}-{token}")
    for name, _ in _SHEETS
    for source in ("chrome", "data")
    for token in _TEXT_TOKENS
]


@pytest.mark.parametrize(("name", "source", "token"), _TEXT_CASES)
def test_text_reads_at_4_5_to_1_on_every_surface_in_every_theme(
    name: str, source: str, token: str
) -> None:
    """No text colour fades into a surface in any theme (issues #1046, #1096).

    Each text token of the theme's chrome sheet and of its data sheet (Neon
    colours data from the base sheet) is checked on the void, the panel, the
    focused panel and the overlay: the surfaces text is drawn on.
    """
    surfaces = theme.tokens_for(name)
    sheet = surfaces if source == "chrome" else theme.data_tokens(name)
    colour = getattr(sheet, token)
    ratios = [
        contrast_ratio(colour, surface)
        for surface in (surfaces.void, surfaces.panel, surfaces.panel_focus, surfaces.overlay)
    ]
    assert [ratio >= TEXT_MIN_CONTRAST for ratio in ratios] == [True, True, True, True], ratios


@pytest.mark.parametrize(("name", "tokens"), _SHEETS)
def test_labels_stay_brighter_than_hints_in_every_theme(name: str, tokens: Tokens) -> None:
    """Raising hint contrast keeps the hierarchy: labels (muted) outshine hints (dim)."""
    del name
    assert _luminance(tokens.muted) > _luminance(tokens.dim)


@pytest.mark.parametrize(("name", "tokens"), _SHEETS)
def test_tab_numbers_and_theme_chip_read_at_4_5_to_1(name: str, tokens: Tokens) -> None:
    """Tab numbers on the void and the chip's void text on its fill clear 4.5:1 (issue #1096).

    The tab bar draws the numbers in ``frame_text`` on the void; the theme
    chip draws void text on a ``frame_text`` fill.
    """
    del name
    tab, _ = tab_text(
        (Tab(key="1", name="Now"), Tab(key="2", name="Trades")), 0, tokens, compact=False
    )
    number = next(span for span in tab.spans if tab.plain[span.start : span.end] == " 2 ")
    number_colour = Style.parse(str(number.style)).color
    chip = Style.parse(str(theme_chip_text(ThemeChoice.NEON, tokens, night=False).style))
    assert (number_colour, chip.color, chip.bgcolor) == (
        Color.parse(tokens.frame_text),
        Color.parse(tokens.void),
        Color.parse(tokens.frame_text),
    )
    assert (
        contrast_ratio(tokens.frame_text, tokens.void) >= TEXT_MIN_CONTRAST,
        contrast_ratio(tokens.void, tokens.frame_text) >= TEXT_MIN_CONTRAST,
    ) == (True, True)


def test_contrast_ratio_matches_the_wcag_extremes() -> None:
    """Black on white is 21:1, a colour on itself 1:1, and the order does not matter."""
    assert (
        round(contrast_ratio("#000000", "#FFFFFF"), 2),
        round(contrast_ratio("#FFFFFF", "#000000"), 2),
        contrast_ratio("#468CE6", "#468CE6"),
    ) == (21.0, 21.0, 1.0)


def test_rule_gradient_is_the_brand_gradient_of_each_sheet() -> None:
    """The rule under the big total keeps the design's brand gradient in every theme."""
    assert [t.hero_underline == t.brand for t in (NEON, CONTROL_ROOM, NIGHT)] == [
        True,
        True,
        True,
    ]


def test_night_never_dims_the_big_number() -> None:
    """Night dims the text but keeps the big number's digits at the day text colour."""
    assert (NIGHT.hero_digits, NIGHT.text == NIGHT.hero_digits) == (CONTROL_ROOM.text, False)
