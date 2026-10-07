"""The Briefs screen of the Cockpit console (issue #720, design spec screen 6).

A list of the supervisor desk's briefs beside the selected one. The text is
the desk's published brief, unchanged: this module only lays it out, so the
screen can show nothing the output gate did not pass (ADR-012 D-06, D-08).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from hermes.application.console.render import RenderContext
from hermes.application.console.views import ConsoleBrief
from hermes.application.supervisor.briefing import BriefKind
from hermes.entrypoints.tui_theme import Tokens

__all__ = [
    "BESIDE_FROM",
    "BRIEFS_FOOTER",
    "DIGEST_NOTE",
    "EARLIER_DAYS_NOTE",
    "NO_BRIEF",
    "briefs_screen",
]

BRIEFS_FOOTER: Final = "written by the AI supervisor desk · it never sees profit, equity or prices"
"""The line under every brief (design spec, screen 6)."""
EARLIER_DAYS_NOTE: Final = "earlier days are not kept yet"
"""Why the list holds only today: the desk does not keep its briefs yet (issue #897)."""
DIGEST_NOTE: Final = (
    "Shown as the digest of states and counts: the full brief did not pass the output gate."
)

BESIDE_FROM: Final = 100
"""Screen width from which the list sits beside the brief instead of above it."""
NO_BRIEF: Final = "No brief yet: the supervisor desk has nothing to read."
_LIST_WIDTH: Final = 24
_MARK: Final = "✦ "


def _row_label(brief: ConsoleBrief, ctx: RenderContext) -> str:
    local = brief.as_of.astimezone(ctx.tz)
    if brief.kind == BriefKind.WEEKLY:
        return f"Week {local.isocalendar().week}"
    if local.date() == ctx.now.astimezone(ctx.tz).date():
        return "Today"
    return local.strftime("%a %d %b")


def _listing(briefs: Sequence[ConsoleBrief], ctx: RenderContext, tokens: Tokens) -> Group:
    rows: list[RenderableType] = []
    for index, brief in enumerate(briefs):
        selected = index == 0
        row = Text("▸ " if selected else "  ", style=tokens.live)
        row.append(f"{_row_label(brief, ctx):<12}", style=f"bold {tokens.text}" if selected else "")
        row.append(brief.kind, style=tokens.muted)
        rows.append(row)
    rows.extend((Text(""), Text(EARLIER_DAYS_NOTE, style=tokens.dim)))
    return Group(*rows)


def _body(brief: ConsoleBrief, ctx: RenderContext, tokens: Tokens) -> Group:
    local = brief.as_of.astimezone(ctx.tz)
    title = f"{brief.kind.capitalize()} brief · {local:%a %d %b %H:%M}"
    parts: list[RenderableType] = [Text(title, style=f"bold {tokens.text}"), Text("")]
    for section in brief.sections:
        head = Text(_MARK, style=tokens.ai)
        head.append(section.heading, style=f"bold {tokens.text}")
        body = Text(section.text)
        if section.sources:
            body.append("  " + " ".join(f"[{s}]" for s in section.sources), style=tokens.dim)
        parts.extend((head, body, Text("")))
    if brief.digest:
        parts.extend((Text(DIGEST_NOTE, style=tokens.warn), Text("")))
    parts.append(Text(BRIEFS_FOOTER, style=tokens.dim))
    return Group(*parts)


def briefs_screen(
    briefs: Sequence[ConsoleBrief], ctx: RenderContext, tokens: Tokens, *, width: int
) -> RenderableType:
    """Return the Briefs screen: the list of briefs and the newest one, selected.

    Args:
        briefs: The desk's briefs, newest first.
        ctx: Read time and display timezone.
        tokens: The data token sheet of the current theme.
        width: Columns available; from :data:`BESIDE_FROM` the list sits beside
            the brief, below that above it.

    Returns:
        The screen body.
    """
    if not briefs:
        return Group(
            Text(NO_BRIEF, style=tokens.muted), Text(""), Text(BRIEFS_FOOTER, style=tokens.dim)
        )
    listing, body = _listing(briefs, ctx, tokens), _body(briefs[0], ctx, tokens)
    if width < BESIDE_FROM:
        return Group(listing, Text(""), body)
    grid = Table.grid(padding=(0, 3), expand=True)
    grid.add_column(width=_LIST_WIDTH)
    grid.add_column(ratio=1)
    grid.add_row(listing, body)
    return grid
