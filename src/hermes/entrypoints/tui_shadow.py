"""What the Shadow test screen draws (design spec screen 5, issue #718).

Pure renderers over a :class:`~hermes.application.console.shadow_screen.ShadowScreen`:
the hero (the 90-day run bar shared with the bottom line, month ticks and the
day-0 line), the five certification checks (pass, fail or pending only, never a
number), the day-0 record with its Ready-rule verdicts, the frozen settings and
the lab notebook entries. Nothing here draws money, a price or a distance to a
threshold. Colours come from the data token sheet: pass blue (``up``), fail
orange (``down``), pending muted.

Reference: openspec/changes/718-shadow-screen/change.md (D-4 to D-6).
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta, tzinfo
from typing import Final

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from hermes.application.console.shadow_screen import (
    MECHANICAL_NOTE,
    CertificationCheck,
    CheckState,
    DayZeroSource,
    NotebookEntry,
    ReadyVerdict,
    ShadowScreen,
)
from hermes.entrypoints.tui_home import HeightClass, run_bar
from hermes.entrypoints.tui_theme import Tokens

__all__ = [
    "CHECK_GLYPHS",
    "WARMUP_HEADLINE",
    "checks_table",
    "day_figure",
    "hero_text",
    "month_ticks",
    "notebook_rows",
    "notebook_text",
    "record_text",
    "settings_text",
]

WARMUP_HEADLINE: Final = "WARM-UP · NOT COUNTING"
CHECK_GLYPHS: Final[dict[CheckState, str]] = {
    CheckState.PASS: "✓",
    CheckState.FAIL: "✗",
    CheckState.PENDING: "○",
}
_VERDICT_GLYPHS: Final[dict[ReadyVerdict, str]] = {
    ReadyVerdict.PASS: "✓",
    ReadyVerdict.FAIL: "✗",
    ReadyVerdict.BY_HAND: "?",
}
_TICK: Final = "╵"
_SHARE_TAIL: Final = 5
_MIN_BAR: Final = 10
_START_HINT: Final = (
    "Day 0 starts with hermes shadow-start (issue #797) once every Ready rule holds."
)
_NOTEBOOK_ROWS: Final[dict[HeightClass, int]] = {
    HeightClass.SHORT: 3,
    HeightClass.TALL: 5,
    HeightClass.TOWERING: 10,
}


def _state_colour(state: CheckState, tokens: Tokens) -> str:
    if state is CheckState.PASS:
        return tokens.up
    if state is CheckState.FAIL:
        return tokens.down
    return tokens.muted


def day_figure(screen: ShadowScreen) -> str | None:
    """Return the hero figure, ``12 / 90`` (the day capped at the run's length).

    Args:
        screen: The Shadow test screen.

    Returns:
        The figure while counting; ``None`` during the warm-up, whose days never
        get the big number.
    """
    if screen.day is None:
        return None
    return f"{min(screen.day, screen.length_days)} / {screen.length_days}"


def month_ticks(start: date | None, length_days: int, width: int, tokens: Tokens) -> Text:
    """Return the tick row under the run bar.

    With a day 0 each first of a month inside the run gets a tick and its
    month (``╵Nov``); without one (the warm-up) the ticks mark days 30, 60
    and 90. A label that would overlap the one before it is left out.

    Args:
        start: Day 0's local date, or ``None``.
        length_days: The run's length.
        width: Cells of the bar above.
        tokens: Token sheet.

    Returns:
        At most ``width`` cells.
    """
    if width < 1 or length_days <= 0:
        return Text("")
    marks: list[tuple[int, str]] = []
    if start is None:
        marks = [(day, f"{day}d") for day in range(30, length_days + 1, 30)]
    else:
        first = date(start.year + start.month // 12, start.month % 12 + 1, 1)
        while (first - start).days <= length_days:
            marks.append(((first - start).days, f"{first:%b}"))
            first = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    cells = [" "] * width
    styles: list[str] = [tokens.dim] * width
    free_from = 0
    for day, label in marks:
        at = min(round(day / length_days * (width - 1)), width - 1)
        if at < free_from:
            continue
        # A label with no room after its tick goes before it ("90d╵").
        after = at + 1 + len(label) <= width
        left = at if after else max(at - len(label), free_from)
        piece = f"{_TICK}{label}" if after else f"{label[: at - left]}{_TICK}"
        tick = 0 if after else len(piece) - 1
        for offset, char in enumerate(piece):
            cells[left + offset] = char
            styles[left + offset] = tokens.frame_text if offset == tick else tokens.muted
        free_from = left + len(piece) + 1
    out = Text()
    for char, style in zip(cells, styles, strict=True):
        out.append(char, style=style)
    out.rstrip()
    return out


def _local(at: datetime, tz: tzinfo) -> datetime:
    return at.astimezone(tz)


def hero_text(
    screen: ShadowScreen, tokens: Tokens, *, width: int, tz: tzinfo, big: bool
) -> RenderableType:
    """Return the hero lines under (or, without the big number, instead of) the digits.

    Args:
        screen: The Shadow test screen.
        tokens: Token sheet.
        width: Cells inside the panel.
        tz: Display timezone.
        big: The big number is shown above; otherwise a bold day line leads.

    Returns:
        Counting: the day line (without the big number), the run bar with its
        share, the month ticks and the day-0 line. Warm-up: the headline, the
        warm-up days in dim, an empty bar with day ticks and the day-0 hint.
    """
    cells = max(width - _SHARE_TAIL, _MIN_BAR)
    lines: list[RenderableType] = []
    length = screen.length_days
    if screen.day is None or screen.day_zero_at is None:
        lines.append(Text(WARMUP_HEADLINE, style=f"bold {tokens.text}"))
        if screen.seeded_at is not None and screen.warmup_day is not None:
            seeded = _local(screen.seeded_at, tz)
            plural = "" if screen.warmup_day == 1 else "s"
            words = (
                f"{screen.warmup_day} warm-up day{plural} since the seed on {seeded:%d %b}"
                " · none of them count (ADR-021 §3)"
            )
        else:
            words = "no capital seed yet · the warm-up has not begun"
        lines.append(Text(words, style=tokens.dim))
        bar = run_bar(0, length, tokens, width=cells)
        bar.append(f" {0:>3}%", style=tokens.dim)
        lines.extend((bar, month_ticks(None, length, cells, tokens)))
        lines.append(Text(_START_HINT, style=tokens.muted))
        return Group(*lines)
    day = min(screen.day, length)
    start = _local(screen.day_zero_at, tz)
    if not big:
        lines.append(Text(f"day {day} / {length}", style=f"bold {tokens.hero_digits}"))
    bar = run_bar(day, length, tokens, width=cells)
    bar.append(f" {round(screen.progress * 100):>3}%", style=tokens.muted)
    lines.extend((bar, month_ticks(start.date(), length, cells, tokens)))
    end = start + timedelta(days=length)
    tail = (
        "run complete · certification waits on the checks"
        if screen.day >= length
        else f"day {length} on {end:%d %b %Y}"
    )
    source = "" if screen.source is DayZeroSource.SHADOW_START_RECORD else f" ({screen.source})"
    lines.append(Text(f"day 0 {start:%d %b %Y %H:%M}{source} · {tail}", style=tokens.muted))
    return Group(*lines)


def checks_table(checks: Sequence[CertificationCheck], tokens: Tokens) -> RenderableType:
    """Return the certification checks and the mechanical note.

    Args:
        checks: The five checks.
        tokens: Data token sheet (pass blue, fail orange).

    Returns:
        One row per check: glyph, name, state word, detail; then the note.
    """
    table = Table.grid(padding=(0, 1))
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(ratio=1)
    for check in checks:
        colour = _state_colour(check.state, tokens)
        table.add_row(
            Text(CHECK_GLYPHS[check.state], style=f"bold {colour}"),
            Text(check.name, style=tokens.text),
            Text(check.state.value, style=f"bold {colour}"),
            Text(check.detail, style=tokens.muted),
        )
    return Group(table, Text(""), Text(MECHANICAL_NOTE, style=tokens.dim))


def record_text(screen: ShadowScreen, tokens: Tokens, *, tz: tzinfo) -> RenderableType:
    """Return the day-0 record: run id, day 0, release and the Ready-rule verdicts.

    Args:
        screen: The Shadow test screen.
        tokens: Data token sheet.
        tz: Display timezone.

    Returns:
        The record's lines, or the line saying there is none yet.
    """
    record = screen.record
    if record is None:
        return Group(
            Text("No day-0 record yet.", style=tokens.text),
            Text(_START_HINT, style=tokens.muted),
        )
    facts = Table.grid(padding=(0, 1))
    facts.add_column(no_wrap=True)
    facts.add_column()
    for label, value in (
        ("run", record.run_id),
        ("day 0", f"{_local(record.day_zero_at, tz):%d %b %Y %H:%M}"),
        ("release", record.release),
    ):
        facts.add_row(Text(label, style=tokens.muted), Text(value, style=tokens.text))
    rules = Table.grid(padding=(0, 1))
    rules.add_column(no_wrap=True)
    rules.add_column(ratio=1)
    rules.add_column(no_wrap=True)
    for rule in record.ready_rules:
        colour = {
            ReadyVerdict.PASS: tokens.up,
            ReadyVerdict.FAIL: tokens.down,
            ReadyVerdict.BY_HAND: tokens.warn,
        }[rule.verdict]
        rules.add_row(
            Text(_VERDICT_GLYPHS[rule.verdict], style=f"bold {colour}"),
            Text(rule.rule, style=tokens.text),
            Text(rule.verdict.value, style=colour),
        )
    heading = Text("Ready rules at day 0", style=f"bold {tokens.muted}")
    if not record.ready_rules:
        return Group(facts, Text(""), heading, Text("none recorded", style=tokens.dim))
    return Group(facts, Text(""), heading, rules)


def settings_text(settings: Sequence[tuple[str, str]], tokens: Tokens) -> RenderableType:
    """Return the frozen pre-registered parameters, shown never edited.

    Args:
        settings: ``(name, value)`` pairs in the order serve reads them.
        tokens: Token sheet.

    Returns:
        A name / value grid and the line naming the source.
    """
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True, justify="right")
    for name, value in settings:
        grid.add_row(Text(name, style=tokens.muted), Text(value, style=f"bold {tokens.text}"))
    note = Text("pre-registration v1 §5 · as hermes serve trades · read-only", style=tokens.dim)
    return Group(grid, Text(""), note)


def notebook_rows(height_class: HeightClass) -> int:
    """Return how many notebook entries fit at a terminal height class."""
    return _NOTEBOOK_ROWS[height_class]


def notebook_text(
    entries: Sequence[NotebookEntry] | None, tokens: Tokens, *, tz: tzinfo, rows: int
) -> RenderableType:
    """Return the newest lab notebook entries, one line each, cut with an ellipsis.

    Args:
        entries: The entries oldest first, or ``None`` when the notebook is not here.
        tokens: Token sheet.
        tz: Display timezone.
        rows: Most entries to show.

    Returns:
        ``06 Oct 2026  shadow · operator  shadow clock reset …``, newest first,
        with ``+N older`` when entries are left out.
    """
    if entries is None:
        return Text("not available here (the notebook ships with the release)", style=tokens.dim)
    if not entries:
        return Text("no entries yet", style=tokens.dim)
    shown = list(reversed(entries))[: max(rows, 1)]
    lines: list[Text] = []
    for entry in shown:
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(f"{_local(entry.at, tz):%d %b %Y}  ", style=tokens.muted)
        line.append(f"{entry.phase} · {entry.actor}  ", style=tokens.frame_text)
        line.append(entry.action, style=tokens.text)
        lines.append(line)
    hidden = len(entries) - len(shown)
    if hidden > 0:
        lines.append(Text(f"+{hidden} older", style=tokens.dim))
    return Group(*lines)
