"""The "Winged H" logo widget for the console.

Shows the logo as a picture where the terminal can draw images and falls
back to a block-letter HERMES wordmark everywhere else (plain terminals,
multiplexers, ssh, and tests). The choice is made from the environment
only, with no terminal probing, so it is cheap and deterministic;
``use_image`` overrides it.

``textual_image`` queries the terminal when it is imported, so it is
imported lazily and only on the image path.

Reference: openspec/changes/733-image/change.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

LOGO_PATH: Final = Path(__file__).resolve().parent / "assets" / "winged_h.png"

_IMAGE_TERM_PROGRAMS: Final = frozenset({"WezTerm", "iTerm.app", "ghostty"})
_IMAGE_TERMS: Final = frozenset({"xterm-kitty", "xterm-ghostty"})
_SSH_VARIABLES: Final = ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT")
_LETTER_ROWS: Final = 5
_GLYPHS: Final[dict[str, tuple[str, ...]]] = {
    "H": ("█   █", "█   █", "█████", "█   █", "█   █"),
    "E": ("█████", "█    ", "████ ", "█    ", "█████"),
    "R": ("████ ", "█   █", "████ ", "█  █ ", "█   █"),
    "M": ("█   █", "██ ██", "█ █ █", "█   █", "█   █"),
    "S": (" ████", "█    ", " ███ ", "    █", "████ "),
}


def wordmark(text: str = "HERMES") -> str:
    """Render ``text`` in five-row block letters, one blank column between letters."""
    rows = [" ".join(_GLYPHS[letter][row] for letter in text) for row in range(_LETTER_ROWS)]
    return "\n".join(rows)


def terminal_shows_images(env: Mapping[str, str], *, is_tty: bool) -> bool:
    """Whether the terminal is known to draw images, judged from ``env`` alone.

    Conservative on purpose: unknown terminals, non-terminals and any ssh
    session get the wordmark, because a wrong guess prints escape garbage.
    """
    if not is_tty or any(env.get(name) for name in _SSH_VARIABLES):
        return False
    return (
        bool(env.get("KITTY_WINDOW_ID"))
        or env.get("TERM_PROGRAM", "") in _IMAGE_TERM_PROGRAMS
        or env.get("TERM", "") in _IMAGE_TERMS
    )


class Logo(Widget):
    """The Winged H picture, or the HERMES wordmark where images are not shown."""

    DEFAULT_CSS = """
    Logo {
        height: auto;
        width: auto;
    }
    """

    def __init__(
        self,
        *,
        use_image: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Create the logo.

        Args:
            use_image: Draw the picture. Callers decide with
                :func:`terminal_shows_images`; the default is the wordmark.
            name: Widget name.
            id: Widget id.
            classes: CSS classes.
        """
        super().__init__(name=name, id=id, classes=classes)
        self.use_image = use_image

    def compose(self) -> ComposeResult:
        """Yield the picture, or the wordmark if images are off or cannot load."""
        if self.use_image:
            try:
                from textual_image.widget import Image  # noqa: PLC0415

                yield Image(LOGO_PATH, id="logo-image")
                return
            except ImportError, OSError:
                self.use_image = False
        yield Static(wordmark(), id="logo-wordmark")
