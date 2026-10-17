"""The Winged H logo falls back to a text wordmark and ships its picture (#733).

Invariants:

- the wordmark is five rows of block letters spelling HERMES, and the
  same input always gives the same text;
- a terminal is only taken to draw images when it is a tty, not an ssh
  session, and named as image-capable in the environment;
- the Logo widget shows the wordmark and never imports ``textual_image``
  (which would probe the terminal) unless the caller asks for the picture;
- the wordmark is coloured from theme tokens only: the brand gradient in Neon
  (first cell the first stop, last cell the last), plain ``live`` in Control room;
- the packaged PNG loads as a transparent RGBA picture of the agreed size
  that uses cyan, violet and magenta tones.

No test asks a real terminal what it supports.
"""

import asyncio
import sys
from collections.abc import Sequence
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from PIL import Image
from textual.app import App, ComposeResult
from textual.widgets import Static

from hermes.entrypoints.tui_logo import (
    LOGO_PATH,
    Logo,
    terminal_shows_images,
    wordmark,
    wordmark_text,
)
from hermes.entrypoints.tui_theme import BASE, NEON

_EXPECTED_WORDMARK = "\n".join(
    (
        "█   █ █████ ████  █   █ █████  ████",
        "█   █ █     █   █ ██ ██ █     █    ",
        "█████ ████  ████  █ █ █ ████   ███ ",
        "█   █ █     █  █  █   █ █         █",
        "█   █ █████ █   █ █   █ █████ ████ ",
    )
)


class _LogoApp(App[None]):
    def __init__(self, *, use_image: bool = False) -> None:
        super().__init__()
        self._use_image = use_image

    def compose(self) -> ComposeResult:
        yield Logo(use_image=self._use_image)


def test_wordmark_spells_hermes_in_five_block_rows() -> None:
    """Invariant: the fallback is the exact five-row HERMES block wordmark."""
    assert wordmark() == _EXPECTED_WORDMARK


@given(st.sampled_from("HERMS"), st.integers(min_value=1, max_value=6))
def test_wordmark_has_five_rows_of_equal_width_for_any_word(letter: str, count: int) -> None:
    """Invariant: any word of known letters renders five rows of equal width."""
    rows = wordmark(letter * count).split("\n")
    assert len(rows) == 5
    assert {len(row) for row in rows} == {count * 5 + (count - 1)}


@pytest.mark.parametrize(
    ("env", "is_tty", "expected"),
    [
        ({"KITTY_WINDOW_ID": "3"}, True, True),
        ({"TERM_PROGRAM": "WezTerm"}, True, True),
        ({"TERM_PROGRAM": "iTerm.app"}, True, True),
        ({"TERM": "xterm-kitty"}, True, True),
        ({"TERM": "xterm-ghostty"}, True, True),
        ({"TERM": "xterm-256color"}, True, False),
        ({}, True, False),
        ({"KITTY_WINDOW_ID": "3"}, False, False),
        ({"KITTY_WINDOW_ID": "3", "SSH_CONNECTION": "10.0.0.1 22 10.0.0.2 22"}, True, False),
        ({"TERM_PROGRAM": "WezTerm", "SSH_TTY": "/dev/pts/0"}, True, False),
    ],
)
def test_terminal_shows_images_only_for_known_local_tty(
    env: dict[str, str], is_tty: bool, expected: bool
) -> None:
    """Invariant: images need a tty, no ssh, and a named image-capable terminal."""
    assert terminal_shows_images(env, is_tty=is_tty) is expected


def test_logo_defaults_to_the_wordmark_without_loading_textual_image() -> None:
    """Invariant: the default Logo shows the wordmark and never probes the terminal."""
    sys.modules.pop("textual_image.widget", None)

    async def scenario() -> tuple[str, bool]:
        app = _LogoApp()
        async with app.run_test():
            shown = str(app.query_one("#logo-wordmark", Static).render())
            return shown, "textual_image.widget" in sys.modules

    shown, imported = asyncio.run(scenario())
    assert shown == _EXPECTED_WORDMARK
    assert imported is False


def test_logo_falls_back_to_the_wordmark_when_the_image_library_cannot_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: a picture request without textual_image still shows the wordmark."""
    monkeypatch.setitem(sys.modules, "textual_image.widget", None)

    async def scenario() -> tuple[int, int]:
        app = _LogoApp(use_image=True)
        async with app.run_test():
            return (
                len(app.query("#logo-wordmark")),
                len(app.query("#logo-image")),
            )

    assert asyncio.run(scenario()) == (1, 0)


def test_packaged_logo_png_is_a_transparent_cyan_violet_magenta_picture() -> None:
    """Invariant: the asset is 192x96 RGBA, transparent at the corner, with all three hues."""
    with Image.open(LOGO_PATH) as picture:
        rgba = picture.convert("RGBA")
    assert rgba.size == (192, 96)
    pixels = cast("Sequence[tuple[int, int, int, int]]", rgba.get_flattened_data())
    assert pixels[95 * 192][3] == 0
    opaque = [p[:3] for p in pixels if p[3] == 255]
    assert opaque, "the logo must have solid pixels"
    assert any(b > 200 and r < 100 for r, _, b in opaque), "cyan end of the gradient"
    assert any(r > 200 and g < 110 and b > 150 for r, g, b in opaque), "magenta end"
    assert any(100 < r < 180 and g < 120 and b > 220 for r, g, b in opaque), "violet middle"


def test_neon_wordmark_runs_the_brand_gradient_left_to_right() -> None:
    """Invariant: Neon draws the brand gradient; the text itself is unchanged."""
    text = wordmark_text(NEON, neon=True)
    first = next(span for span in text.spans if span.start == 0)
    last = max(text.spans, key=lambda span: span.end)
    assert (text.plain, str(first.style), str(last.style)) == (
        wordmark(),
        f"bold {NEON.brand[0]}",
        f"bold {NEON.brand[-1]}",
    )


def test_control_room_wordmark_is_one_plain_live_colour() -> None:
    """Invariant: Control room paints every row in the single ``live`` cyan."""
    text = wordmark_text(BASE, neon=False)
    assert (text.plain, {str(span.style) for span in text.spans}) == (
        wordmark(),
        {f"bold {BASE.live}"},
    )
