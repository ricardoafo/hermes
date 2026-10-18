"""Design tokens of the ``hermes tui`` Cockpit console, in one place.

Every colour, gradient, breakpoint, glyph and timing the console draws with is
defined here once (design spec ``docs/design/console-cockpit.md``, issue
#711). Changing a token is a one-line edit; the Textual themes, the TCSS
variables and the Python renderers all read from these values.

Three Textual themes are built from the tokens:

- ``hermes-neon``: the default chrome (deep indigo, magenta to cyan title bars);
- ``hermes-control-room``: the calmer base token sheet;
- ``hermes-night``: Control room dimmed, used by the Auto choice from 23:00
  to 08:00 local. Gains and losses keep their hues in darker shades and
  warnings stay fully bright.

Data colours stay semantic in every theme: up is blue (70,140,230), down is
orange (240,120,20), warning is amber. Neon restyles only the chrome; there is
no scanline, blur or glow, and no neon colour on the data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import time
from typing import Final

from rich.terminal_theme import TerminalTheme
from textual.theme import Theme

from hermes.entrypoints.tui_settings import ThemeChoice

__all__ = [
    "AI_GLYPH",
    "BASE",
    "COMPACT_BELOW",
    "CONTROL_ROOM",
    "CONTROL_THEME",
    "CURVE_ROWS_STANDARD",
    "CURVE_ROWS_WIDE",
    "FEED_WIDTH",
    "HERO_MIN_CONTRAST",
    "HERO_RULE_GLYPH",
    "HORIZONTAL_BREAKPOINTS",
    "LEFT_COLUMN_STANDARD",
    "LEFT_COLUMN_WIDE",
    "LOG_TAIL_ROWS",
    "MARKET_COLUMN_WIDE",
    "METER_CAP_LEFT",
    "METER_CAP_RIGHT",
    "METER_CELLS",
    "METER_EMPTY",
    "METER_FULL",
    "METER_WARN_SHARE",
    "MOTION",
    "MOTION_ENV",
    "NEON",
    "NEON_THEME",
    "NIGHT",
    "NIGHT_END",
    "NIGHT_START",
    "NIGHT_THEME",
    "RADAR_FRAMES",
    "TALL_FROM",
    "TEXT_MIN_CONTRAST",
    "THEMES",
    "TOKENS_BY_THEME",
    "TOWERING_CHART_FACTOR",
    "TOWERING_FROM",
    "ULTRA_FROM",
    "VERTICAL_BREAKPOINTS",
    "WIDE_FROM",
    "WORDMARK",
    "Motion",
    "ThemeChoice",
    "Tokens",
    "blend",
    "contrast_ratio",
    "data_tokens",
    "gradient",
    "is_night",
    "motion_enabled",
    "pulse_steps",
    "resolve_theme",
    "startup_logo",
    "terminal_theme",
    "textual_theme",
    "tokens_for",
]


@dataclass(frozen=True)
class Tokens:
    """One complete token sheet; a theme is a sheet plus a name.

    Attributes:
        void: Screen background.
        panel: Panel surface.
        panel_focus: Surface of the focused panel.
        focus_border: Frame line of the focused panel.
        overlay: Toasts, dialogs and the theme picker.
        line: Idle frame lines.
        live: Live dot, focus, live tags; the top-left bracket corner.
        ai: Anything the AI supervisor wrote (✦); the bottom-right corner.
        frame: Chrome accent between ``live`` and ``ai``.
        frame_text: ``frame`` lifted to read at 4.5:1 on every surface, for
            tab numbers, the theme chip and any text drawn in the accent.
        up: Gains (colour-blind blue).
        down: Losses (colour-blind orange).
        warn: Late, stop near (amber).
        text: Figures.
        muted: Labels.
        dim: Hints.
        brand: Gradient stops for big totals and the 90-day bar.
        chrome: Gradient stops for title bars and the window bar.
        gain: Gradient stops for gain meters and charts.
        loss: Gradient stops for loss meters and charts.
        ai_gradient: Gradient stops for AI marks.
        hero_digits: Token ``hero-digits``: every big number's digits, one
            bold colour at full contrast (at least 7:1 on ``panel`` in every
            theme). Night never dims it: it keeps the day text colour.
        hero_underline: Token ``hero-underline``: gradient stops of the 1-row
            underline under a big number, which also carries the shimmer.
        highlight: The feed pulse's flash on the newest poll.
    """

    void: str
    panel: str
    panel_focus: str
    focus_border: str
    overlay: str
    line: str
    live: str
    ai: str
    frame: str
    frame_text: str
    up: str
    down: str
    warn: str
    text: str
    muted: str
    dim: str
    brand: tuple[str, ...]
    chrome: tuple[str, ...]
    gain: tuple[str, ...]
    loss: tuple[str, ...]
    ai_gradient: tuple[str, ...]
    hero_digits: str
    hero_underline: tuple[str, ...]
    highlight: str


BASE: Final = Tokens(
    void="#070B14",
    panel="#0C1424",
    panel_focus="#0F1B33",
    focus_border="#2C4F86",
    overlay="#18233A",
    line="#1A2740",
    live="#58D1EB",
    # Issue #1096: lifted from #9D65FF, which read at 4.3:1 on the overlay.
    ai="#A26EFE",
    # The spec gives `frame` and `chrome` for Neon only; Control room's values
    # are an implementer decision (change.md D-9): the focus border and the
    # live -> up -> ai stops of the base sheet.
    frame="#2C4F86",
    frame_text="#768EB2",
    up="#468CE6",
    down="#F07814",
    warn="#E6BE28",
    text="#E6ECF5",
    muted="#8A94A8",
    # Issue #1096: hints read at 2.8:1 to 3.5:1 as #5D6780.
    dim="#838CA1",
    brand=("#8BE9FF", "#468CE6", "#9D65FF"),
    chrome=("#58D1EB", "#468CE6", "#9D65FF"),
    gain=("#1B3A6B", "#468CE6", "#8BE9FF"),
    loss=("#7A3A0A", "#F07814", "#E6BE28"),
    ai_gradient=("#3A2A66", "#9D65FF", "#E0C8FF"),
    # The Code Owner, 2026-10-06: gradient digits were illegible; the digits
    # take the text colour and the gradient moves to a rule under them.
    hero_digits="#E6ECF5",
    hero_underline=("#8BE9FF", "#468CE6", "#9D65FF"),
    highlight="#FFFFFF",
)

NEON: Final = replace(
    BASE,
    void="#0B0420",
    panel="#120731",
    panel_focus="#170A3D",
    # The spec leaves Neon's focus border open; it is the Neon `frame` (D-9).
    focus_border="#8A5CFF",
    line="#4A2399",
    live="#00F0FF",
    ai="#FF3DF0",
    frame="#8A5CFF",
    frame_text="#9374FF",
    text="#C8FBFF",
    muted="#8FA8D8",
    # Issue #1096: hints read at 2.6:1 to 3.4:1 as #5A5F9A.
    dim="#798BB6",
    chrome=("#FF3DF0", "#8A5CFF", "#00F0FF"),
    brand=("#00F0FF", "#8A5CFF", "#FF3DF0"),
    hero_digits="#C8FBFF",
    hero_underline=("#00F0FF", "#8A5CFF", "#FF3DF0"),
)

CONTROL_ROOM: Final = BASE

NIGHT: Final = replace(
    BASE,
    void="#04070D",
    panel="#080E19",
    panel_focus="#0B1426",
    focus_border="#1F3961",
    overlay="#111A2B",
    line="#121C2E",
    live="#3B8FA1",
    # Night keeps day hues but lightens every text colour to at least 4.5:1 on
    # its darker panels (issue #1046: labels read at 4.0:1, hints at 2.3:1).
    ai="#9274C8",
    frame="#1F3961",
    frame_text="#75859E",
    up="#5186CA",
    down="#CE6812",
    text="#B4BCCB",
    muted="#9098AB",
    dim="#7883A0",
    chrome=("#3B8FA1", "#2F5E9A", "#6B45AD"),
    brand=("#5E9FB0", "#2F5E9A", "#6B45AD"),
    # Night dims text and data, never a big number: the digits keep the
    # day text colour (a 40% dim would drop them to about 5.9:1).
    hero_digits="#E6ECF5",
    hero_underline=("#5E9FB0", "#2F5E9A", "#6B45AD"),
)


NEON_THEME: Final = "hermes-neon"
CONTROL_THEME: Final = "hermes-control-room"
NIGHT_THEME: Final = "hermes-night"

TOKENS_BY_THEME: Final[Mapping[str, Tokens]] = {
    NEON_THEME: NEON,
    CONTROL_THEME: CONTROL_ROOM,
    NIGHT_THEME: NIGHT,
}

NIGHT_START: Final = time(23, 0)
NIGHT_END: Final = time(8, 0)

# Width classes Textual puts on the screen: compact < 100, standard 100-159,
# wide 160-219, ultra >= 220 columns.
COMPACT_BELOW: Final = 100
WIDE_FROM: Final = 160
ULTRA_FROM: Final = 220
HORIZONTAL_BREAKPOINTS: Final[list[tuple[int, str]]] = [
    (0, "-compact"),
    (COMPACT_BELOW, "-standard"),
    (WIDE_FROM, "-wide"),
    (ULTRA_FROM, "-ultra"),
]
# Height classes: < 30 rows hides the curve, >= 45 rows doubles the charts.
TALL_FROM: Final = 30
TOWERING_FROM: Final = 45
VERTICAL_BREAKPOINTS: Final[list[tuple[int, str]]] = [
    (0, "-short"),
    (TALL_FROM, "-tall"),
    (TOWERING_FROM, "-towering"),
]

# Home layout per width class (issue #712): the left column's width, the
# Market panel's width beside Activity, and the curve rows (doubled when
# towering). Compact draws a sparkline line instead of the curve.
LEFT_COLUMN_STANDARD: Final = 46
LEFT_COLUMN_WIDE: Final = 60
MARKET_COLUMN_WIDE: Final = 46
CURVE_ROWS_STANDARD: Final = 5
CURVE_ROWS_WIDE: Final = 6
TOWERING_CHART_FACTOR: Final = 2
LOG_TAIL_ROWS: Final = 10
# The empty-positions radar: one braille dot sweeping round.
RADAR_FRAMES: Final = "⠁⠂⠄⡀⢀⠠⠐⠈"
# The big total's rule: one row of this glyph under the digits.
HERO_RULE_GLYPH: Final = "▔"
# Legibility floor of the big total on its panel (WCAG 2 AAA for text).
HERO_MIN_CONTRAST: Final = 7.0
# WCAG AA for body text; night draws every label, hint and data colour above it.
TEXT_MIN_CONTRAST: Final = 4.5

# Every meter on every screen draws with these glyphs: full and empty cells,
# the optional end caps, and the default width in cells.
# A risk meter turns amber from this share of its cap.
METER_WARN_SHARE: Final = 0.8
METER_FULL: Final = "█"
METER_EMPTY: Final = "░"
METER_CAP_LEFT: Final = "▕"
METER_CAP_RIGHT: Final = "▏"
METER_CELLS: Final = 10

AI_GLYPH: Final = "✦"
LIVE_GLYPH: Final = "●"
LATE_GLYPH: Final = "◐"
DOWN_GLYPH: Final = "✖"
CORNER_TOP_LEFT: Final = "┏━"
CORNER_BOTTOM_RIGHT: Final = "━┛"
EDGE: Final = "─"
BACK_GLYPH: Final = "◂"
CRUMB_SEPARATOR: Final = " › "  # noqa: RUF001 - the design spec's breadcrumb separator
ELLIPSIS: Final = "…"

# The start-up logo is still being chosen (issue #711, theme switch comment):
# set LOGO to its rows when it lands; until then the wordmark stands in.
LOGO: Final[tuple[str, ...] | None] = None
WORDMARK: Final = (
    "█ █ █▀▀ █▀█ █▀▄▀█ █▀▀ █▀▀",
    "█▀█ █▀▀ █▀▄ █ ▀ █ █▀▀ ▀▀█",
    "▀ ▀ ▀▀▀ ▀ ▀ ▀   ▀ ▀▀▀ ▀▀▀",
)


@dataclass(frozen=True)
class Motion:
    """Timings in seconds.

    ``pulse_*`` and ``clock_tick`` drive timers since issue
    #711; ``slow``, ``shimmer``, ``shimmer_step`` and ``radar_tick`` since
    issue #712; the others are defined for the effects later issues build.

    Attributes:
        instant: Instant feedback.
        fast: Focus and border changes.
        base: Toasts and slides.
        slow: Count-up of the big total (issue #712).
        pulse_period: One live-dot pulse.
        pulse_period_night: One live-dot pulse in night mode (slower).
        pulse_steps: Colour steps per pulse.
        aurora: One drift of the aurora rule.
        shimmer: Pause between shimmer passes on big numbers (issue #712).
        shimmer_step: One step of a shimmer pass (8 fps, the ambient cap).
        radar_tick: One step of the empty-positions radar.
        flash: A live price flash, and the feed pulse's bright newest glyph (issue #713).
        clock_tick: The clock and the next-bar countdown.
        poll: Re-reading the read model.
    """

    instant: float = 0.08
    fast: float = 0.15
    base: float = 0.3
    slow: float = 0.6
    pulse_period: float = 1.6
    pulse_period_night: float = 3.2
    pulse_steps: int = 4
    aurora: float = 9.0
    shimmer: float = 6.0
    shimmer_step: float = 0.125
    radar_tick: float = 0.25
    flash: float = 0.9
    clock_tick: float = 1.0
    poll: float = 5.0


MOTION: Final = Motion()
MOTION_ENV: Final = "HERMES_CONSOLE_MOTION"
_MOTION_OFF: Final = frozenset({"off", "0", "false", "no"})
# Recent ticker polls the status row's feed pulse shows, newest on the right (issue #713).
FEED_WIDTH: Final = 16


def motion_enabled(env: Mapping[str, str]) -> bool:
    """Return whether animations run; ``HERMES_CONSOLE_MOTION=off`` stops them.

    Args:
        env: Environment snapshot.

    Returns:
        ``False`` for ``off``, ``0``, ``false`` or ``no`` (any case), else ``True``.
    """
    return env.get(MOTION_ENV, "on").strip().lower() not in _MOTION_OFF


def is_night(local: time) -> bool:
    """Return whether a local wall-clock time falls in night mode (23:00-08:00).

    Args:
        local: Local time of day.

    Returns:
        ``True`` from 23:00 up to, not including, 08:00.
    """
    return local >= NIGHT_START or local < NIGHT_END


def resolve_theme(choice: ThemeChoice, local: time) -> str:
    """Return the Textual theme name a choice shows at a local time.

    Args:
        choice: Neon, Control room, or Auto (Neon by day, night at night).
        local: Local time of day.

    Returns:
        ``hermes-neon``, ``hermes-control-room`` or ``hermes-night``.
    """
    if choice is ThemeChoice.NEON:
        return NEON_THEME
    if choice is ThemeChoice.CONTROL:
        return CONTROL_THEME
    return NIGHT_THEME if is_night(local) else NEON_THEME


def tokens_for(theme_name: str) -> Tokens:
    """Return the token sheet of a theme; any other theme falls back to Neon.

    Args:
        theme_name: A Textual theme name.

    Returns:
        The tokens to draw with.
    """
    return TOKENS_BY_THEME.get(theme_name, NEON)


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    value = hex_colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


# WCAG 2 sRGB linearisation: the threshold, the linear slope, and the curve.
_SRGB_KNEE: Final = 0.03928
_SRGB_SLOPE: Final = 12.92
_SRGB_GAMMA: Final = 2.4


def _luminance(hex_colour: str) -> float:
    """Return the WCAG 2 relative luminance of ``#RRGGBB``."""

    def channel(value: int) -> float:
        c = value / 255
        return c / _SRGB_SLOPE if c <= _SRGB_KNEE else ((c + 0.055) / 1.055) ** _SRGB_GAMMA

    r, g, b = (channel(v) for v in _rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG 2 contrast ratio of two colours, from 1 to 21.

    Args:
        foreground: ``#RRGGBB``.
        background: ``#RRGGBB``.

    Returns:
        ``(L1 + 0.05) / (L2 + 0.05)`` with ``L1`` the lighter luminance.
    """
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def blend(start: str, end: str, amount: float) -> str:
    """Return the colour ``amount`` of the way from ``start`` to ``end``.

    Args:
        start: ``#RRGGBB``.
        end: ``#RRGGBB``.
        amount: 0 gives ``start``, 1 gives ``end``; clamped to [0, 1].

    Returns:
        ``#RRGGBB`` in upper case.
    """
    amount = min(max(amount, 0.0), 1.0)
    mixed = (round(a + (b - a) * amount) for a, b in zip(_rgb(start), _rgb(end), strict=True))
    return "#" + "".join(f"{channel:02X}" for channel in mixed)


def gradient(stops: Sequence[str], position: float) -> str:
    """Return the colour at ``position`` along evenly spaced gradient stops.

    Args:
        stops: Two or more ``#RRGGBB`` stops.
        position: 0 is the first stop, 1 the last; clamped to [0, 1].

    Returns:
        ``#RRGGBB``.
    """
    position = min(max(position, 0.0), 1.0)
    spans = len(stops) - 1
    index = min(int(position * spans), spans - 1)
    return blend(stops[index], stops[index + 1], position * spans - index)


def pulse_steps(tokens: Tokens) -> tuple[str, ...]:
    """Return the live dot's colours for one pulse: bright, fading, faint, fading.

    Args:
        tokens: The current token sheet.

    Returns:
        ``MOTION.pulse_steps`` colours, starting at full ``live``.
    """
    fading = blend(tokens.live, tokens.dim, 0.35)
    faint = blend(tokens.live, tokens.dim, 0.7)
    return (tokens.live, fading, faint, fading)


def startup_logo() -> tuple[str, ...]:
    """Return the start-up logo rows: the chosen logo, else the block-letter wordmark."""
    return LOGO if LOGO is not None else WORDMARK


def textual_theme(name: str, tokens: Tokens) -> Theme:
    """Build a Textual theme from a token sheet.

    The ``hud-*`` variables are the tokens TCSS uses; the semantic slots
    (success, error, warning) carry the data colours.

    Args:
        name: Theme name.
        tokens: Token sheet.

    Returns:
        A dark Textual theme.
    """
    return Theme(
        name=name,
        primary=tokens.live,
        secondary=tokens.ai,
        accent=tokens.frame,
        warning=tokens.warn,
        error=tokens.down,
        success=tokens.up,
        foreground=tokens.text,
        background=tokens.void,
        surface=tokens.panel,
        panel=tokens.overlay,
        boost=tokens.panel_focus,
        dark=True,
        variables=theme_variables(tokens),
    )


def theme_variables(tokens: Tokens) -> dict[str, str]:
    """Return the ``hud-*`` TCSS variables of a token sheet.

    Args:
        tokens: Token sheet.

    Returns:
        Variable name to colour.
    """
    return {
        "hud-void": tokens.void,
        "hud-panel": tokens.panel,
        "hud-panel-focus": tokens.panel_focus,
        "hud-focus-border": tokens.focus_border,
        "hud-overlay": tokens.overlay,
        "hud-line": tokens.line,
        "hud-live": tokens.live,
        "hud-ai": tokens.ai,
        "hud-frame": tokens.frame,
        "hud-up": tokens.up,
        "hud-down": tokens.down,
        "hud-warn": tokens.warn,
        "hud-text": tokens.text,
        "hud-muted": tokens.muted,
        "hud-dim": tokens.dim,
        "hud-hero-digits": tokens.hero_digits,
    }


THEMES: Final[tuple[Theme, ...]] = tuple(
    textual_theme(name, tokens) for name, tokens in TOKENS_BY_THEME.items()
)


def data_tokens(theme_name: str) -> Tokens:
    """Return the sheet the data (Rich content) is coloured from under a theme.

    Neon restyles only the chrome, so Neon and Control room both colour the
    data from the base sheet; night uses its darker gains and losses.

    Args:
        theme_name: A Textual theme name.

    Returns:
        ``NIGHT`` for the night theme, else ``BASE``.
    """
    return NIGHT if theme_name == NIGHT_THEME else BASE


def terminal_theme(tokens: Tokens) -> TerminalTheme:
    """Return the ANSI palette Rich content is drawn with, from a token sheet.

    Rich renderers use ANSI names: their red reads as the ``down`` orange,
    their green as the ``up`` blue, yellow as ``warn`` amber, blue as ``ai``
    and cyan as ``live``.

    Args:
        tokens: Token sheet; pass :func:`data_tokens`, never the Neon sheet.

    Returns:
        A Rich terminal theme (normal and bright colours).
    """
    up, down, warn = _rgb(tokens.up), _rgb(tokens.down), _rgb(tokens.warn)
    ai, live = _rgb(tokens.ai), _rgb(tokens.live)
    return TerminalTheme(
        (12, 12, 12),
        (217, 217, 217),
        [(26, 26, 26), down, up, warn, ai, down, live, (196, 197, 181), (98, 94, 76)],
        [down, up, (255, 214, 90), ai, down, live, (246, 246, 239)],
    )
