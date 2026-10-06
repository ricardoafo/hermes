"""The Positions screen of the Cockpit console (issue #715, design spec screen 3).

The open positions beside the selected one's detail. The rows and the detail
are the Home cards' own renderables, live between bars when the ticker runs,
so both screens always agree; this module only lays them out.
"""

from __future__ import annotations

from typing import Final

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from hermes.entrypoints.tui_theme import Tokens

__all__ = ["BESIDE_FROM", "HINT", "list_width", "positions_screen"]

BESIDE_FROM: Final = 120
"""Screen width from which the detail sits beside the list instead of under it."""
HINT: Final = "↑↓ choose a position · nothing is traded from this screen"
_GAP: Final = 3


def list_width(width: int) -> int:
    """Return the columns the position rows get on a screen ``width`` wide.

    Beside the detail the list takes five eighths, enough for the entry and
    current prices on a wide screen; stacked, the whole width.
    """
    return (width - _GAP) * 5 // 8 if width >= BESIDE_FROM else width


def positions_screen(
    rows: RenderableType,
    detail: RenderableType | None,
    count: int,
    tokens: Tokens,
    *,
    width: int,
) -> RenderableType:
    """Return the Positions screen: the open positions and the selected one's detail.

    Args:
        rows: The position rows (or the empty state when nothing is open).
        detail: The selected position's detail, or ``None`` with nothing open.
        count: How many positions are open.
        tokens: The data token sheet of the current theme.
        width: Columns available; from :data:`BESIDE_FROM` the detail sits
            beside the list, below that under it.

    Returns:
        The screen body.
    """
    title = Text(f"Open positions · {count}", style=f"bold {tokens.text}")
    listing = Group(title, Text(""), rows, Text(""), Text(HINT, style=tokens.dim))
    if detail is None:
        return listing
    if width < BESIDE_FROM:
        return Group(listing, Text(""), detail)
    grid = Table.grid(padding=(0, _GAP), expand=True)
    grid.add_column(width=list_width(width))
    grid.add_column(ratio=1)
    grid.add_row(listing, detail)
    return grid
