"""The Signals screen of the Cockpit console (issue #714, design spec screen 2).

What the engine checked at each bar and which frozen entry rules held. Every
rule shows as yes or no only: never a price, an indicator value or how close
a rule came to holding (ADR-012 D-06), so the screen takes nothing but
:class:`RuleCheck` rows and the coins held.
"""

from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from typing import Final

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from hermes.application.console.render import RenderContext
from hermes.application.console.views import RULE_COUNT, RuleCheck
from hermes.entrypoints.tui_theme import Tokens

__all__ = [
    "BESIDE_FROM",
    "FROZEN_RULES",
    "NO_CHECKS",
    "RULES_NOTE",
    "WEEK",
    "signals_screen",
]

WEEK: Final = timedelta(days=7)
"""How far back the heat grid and the week's counts reach."""
BESIDE_FROM: Final = 120
"""Screen width from which the week and the rules sit beside the checks."""
RULES_NOTE: Final = "yes or no only, never how close a price came"
"""Why no rule shows a distance (ADR-012 D-06, design spec screen 2)."""
NO_CHECKS: Final = "No bar checks in the last 7 days yet."
FROZEN_RULES: Final = (
    ("1 breakout", "close above the 20-bar high (4h)"),
    ("2 trend", "close above the 200-day average (1D)"),
    ("3 momentum", "ADX(14) at 25 or more (4h)"),
)
"""The entry rules of pre-registration §5.1, in words."""
_BEFORE_BUYING: Final = (
    "max 3 positions · exposure cap",
    "correlation lock above 0.80",
    "skip if the order book is stale",
)
"""What pre-registration §5.5 checks before a signal becomes an order."""
_RULE_COLUMNS: Final = (
    ("breakout 4h", "breakout"),
    ("trend 1D", "regime"),
    ("momentum 4h", "momentum"),
)
_PER_DAY: Final = 6
_INTERVAL: Final = "4h"
_CELL: Final = "■"
_SIGNAL: Final = "◆"
_METER: Final = 20


def _cell_styles(tokens: Tokens) -> tuple[str, ...]:
    return (tokens.line, tokens.gain[0], tokens.up, f"bold {tokens.live}")


def _slots(checks: Sequence[RuleCheck], ctx: RenderContext, room: int) -> list[datetime | None]:
    """Return the newest checked candles that fit ``room`` columns, ``None`` between days."""
    slots: list[datetime | None] = []
    used = 0
    day = None
    for at in sorted({check.at for check in checks}, reverse=True):
        local = at.astimezone(ctx.tz).date()
        gap = day is not None and local != day
        if used + 1 + gap > room:
            break
        if gap:
            slots.append(None)
        slots.append(at)
        used += 1 + gap
        day = local
    return slots[::-1]


def _grid(checks: Sequence[RuleCheck], ctx: RenderContext, tokens: Tokens, *, width: int) -> Group:
    styles = _cell_styles(tokens)
    coins = sorted({check.coin for check in checks})
    name_width = max(len(coin) for coin in coins) + 2
    slots = _slots(checks, ctx, max(_PER_DAY, width - name_width))
    by_key = {(check.coin, check.at): check for check in checks}
    rows: list[RenderableType] = []
    for coin in coins:
        row = Text(f"{coin:<{name_width}}", style=f"bold {tokens.text}")
        for at in slots:
            check = None if at is None else by_key.get((coin, at))
            if check is None:
                row.append(" ")
            else:
                row.append(_SIGNAL if check.signal else _CELL, style=styles[check.met])
        rows.append(row)
    first = next(at for at in slots if at is not None).astimezone(ctx.tz)
    legend = Text()
    for met, label in enumerate(("no rule met", "1 of 3", "2 of 3", "all 3 · signal")):
        legend.append(_SIGNAL if met == RULE_COUNT else _CELL, style=styles[met])
        legend.append(f" {label}  ", style=tokens.muted)
    span = Text(f"one square per 4-hour bar · {first:%a %d %b} → today", style=tokens.dim)
    return Group(*rows, Text(""), legend, span)


def _yes_no(held: bool, tokens: Tokens) -> Text:
    return Text("yes", style=f"bold {tokens.up}") if held else Text("no", style=tokens.dim)


def _last_check(
    checks: Sequence[RuleCheck], ctx: RenderContext, tokens: Tokens, held: Collection[str]
) -> Group:
    newest = max(check.at for check in checks)
    title = Text(f"Last check · {newest.astimezone(ctx.tz):%H:%M} bar", style=f"bold {tokens.text}")
    table = Table.grid(padding=(0, 3))
    for header in ("coin", *(name for name, _ in _RULE_COLUMNS), "result"):
        table.add_column(header)
    table.add_row(
        *(Text(h, style=tokens.muted) for h in ("coin", *(n for n, _ in _RULE_COLUMNS), "result"))
    )
    latest = sorted((c for c in checks if c.at == newest), key=lambda c: c.coin)
    for check in latest:
        result = (
            Text(f"{_SIGNAL} signal", style=f"bold {tokens.live}")
            if check.signal
            else Text(f"{check.met} of {RULE_COUNT}", style=tokens.muted)
        )
        table.add_row(
            Text(check.coin, style=f"bold {tokens.text}"),
            *(_yes_no(getattr(check, field), tokens) for _, field in _RULE_COLUMNS),
            result,
        )
    checked = {check.coin.upper() for check in latest}
    for coin in sorted(c for c in held if c.upper() not in checked):
        table.add_row(
            Text(coin, style=f"bold {tokens.text}"),
            *(Text("—", style=tokens.dim) for _ in _RULE_COLUMNS),
            Text("holding", style=tokens.muted),
        )
    return Group(title, table)


def _meter(share: float, tokens: Tokens) -> Text:
    filled = round(share * _METER)
    meter = Text("█" * filled, style=tokens.up)
    meter.append("░" * (_METER - filled), style=tokens.line)
    meter.append(f" {share:>4.0%}", style=tokens.muted)
    return meter


def _week(checks: Sequence[RuleCheck], ctx: RenderContext, tokens: Tokens) -> Group:
    signals = sorted((c for c in checks if c.signal), key=lambda c: c.at)
    total = Text(f"{len(checks)}", style=f"bold {tokens.text}")
    total.append("  bar checks", style=tokens.muted)
    found = Text(f"{'signals':<16}", style=tokens.muted)
    found.append(f"{len(signals)}", style=f"bold {tokens.live}")
    if signals:
        last = signals[-1]
        found.append(f"  last {last.coin}, {last.at.astimezone(ctx.tz):%a %H:%M}", style=tokens.dim)
    parts: list[RenderableType] = [
        Text("This week", style=f"bold {tokens.text}"),
        total,
        found,
        Text(""),
        Text("how often each rule was met", style=tokens.muted),
    ]
    for name, field in _RULE_COLUMNS:
        share = sum(getattr(c, field) for c in checks) / len(checks)
        line = Text(f"{name:<14}", style=tokens.text)
        line.append_text(_meter(share, tokens))
        parts.append(line)
    return Group(*parts)


def _rules(tokens: Tokens) -> Group:
    parts: list[RenderableType] = [
        Text("The rules · frozen", style=f"bold {tokens.text}"),
        Text("buy at the bar close when all three are yes", style=tokens.muted),
    ]
    parts.extend(
        Text.assemble((f"{n:<13}", tokens.text), (w, tokens.text)) for n, w in FROZEN_RULES
    )
    parts.extend((Text(""), Text("checked before buying", style=tokens.muted)))
    parts.extend(Text(line, style=tokens.text) for line in _BEFORE_BUYING)
    parts.extend((Text(""), Text(RULES_NOTE, style=tokens.dim)))
    return Group(*parts)


def signals_screen(
    checks: Sequence[RuleCheck],
    ctx: RenderContext,
    tokens: Tokens,
    *,
    width: int,
    held: Collection[str] = (),
) -> RenderableType:
    """Return the Signals screen: the week's bar checks, the last one, and the rules.

    Args:
        checks: Yes/no entry rules per coin and check, in any order; only 4h
            checks of the last :data:`WEEK` are shown.
        ctx: Read time and display timezone.
        tokens: The data token sheet of the current theme.
        width: Columns available; from :data:`BESIDE_FROM` the week's counts
            and the rules sit beside the checks, below that under them.
        held: Coins with an open position, which serve does not check for entry.

    Returns:
        The screen body.
    """
    week = [c for c in checks if c.interval == _INTERVAL and c.at > ctx.now - WEEK]
    if not week:
        return Group(Text(NO_CHECKS, style=tokens.muted), Text(""), _rules(tokens))
    side_width = 52
    beside = width >= BESIDE_FROM
    main_width = width - side_width - 3 if beside else width
    title = Text("Bar checks · last 7 days", style=f"bold {tokens.text}")
    main = Group(
        title,
        _grid(week, ctx, tokens, width=main_width),
        Text(""),
        _last_check(week, ctx, tokens, held),
    )
    side = Group(_week(week, ctx, tokens), Text(""), _rules(tokens))
    if not beside:
        return Group(main, Text(""), side)
    grid = Table.grid(padding=(0, 3), expand=True)
    grid.add_column(ratio=1)
    grid.add_column(width=side_width)
    grid.add_row(main, side)
    return grid
