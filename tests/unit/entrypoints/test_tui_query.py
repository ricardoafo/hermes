"""Tests for the reusable query widgets and the activity harness (#729).

Invariants protected here:

- typing a query filters the table and the bar says "N of M" with the sort;
- a bad query shows its error and leaves the last good result standing;
- Tab completes a field name instead of moving the focus;
- a header click or ``S`` sorts by that column, again flips the direction;
- the facet line counts the current result, most common first;
- the widgets read only: the module imports no adapter and no port;
- the widgets draw only from the console's theme tokens; the reviewed pictures
  live in ``test_tui_snapshots.py`` (``query_activity_*``).
"""

import ast
import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hermes.application.console.query import Row, SortKey
from hermes.application.console.render import MADRID
from hermes.application.console.views import ActivityItem, ActivityKind
from hermes.entrypoints import tui_query as tui_query_module
from hermes.entrypoints.tui_query import (
    FacetFilter,
    QueryBar,
    QueryInput,
    QueryList,
    SortableTable,
    activity_query_app,
    facet_lines,
    status_line,
)
from hermes.entrypoints.tui_theme import CONTROL_THEME, NEON_THEME

_NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def _at(day: int, hour: int) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


_ITEMS = (
    ActivityItem(at=_at(5, 9), kind=ActivityKind.OPENED, coin="BTC"),
    ActivityItem(at=_at(4, 16), kind=ActivityKind.CLOSED, coin="ETH"),
    ActivityItem(at=_at(4, 8), kind=ActivityKind.WARNING, text="exchange slow, retried"),
    ActivityItem(at=_at(2, 14), kind=ActivityKind.CLOSED, coin="BTC"),
    ActivityItem(at=_at(1, 10), kind=ActivityKind.OPENED, coin="ETH"),
    ActivityItem(at=datetime(2025, 12, 30, 10, tzinfo=UTC), kind=ActivityKind.CLOSED, coin="SOL"),
)


@dataclass(frozen=True)
class Shot:
    """What the harness showed after a key sequence."""

    status: str
    error: bool
    value: str
    shown: tuple[Row, ...]
    facets: str
    sort: tuple[SortKey, ...]
    headers: tuple[str, ...]
    focused: str | None
    svg: str


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


async def _drive(
    keys: Sequence[str] = (),
    size: tuple[int, int] = (80, 24),
    *,
    header: int | None = None,
    values: Sequence[str] = (),
) -> Shot:
    app = activity_query_app(_ITEMS, MADRID, lambda: _NOW)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for value in values:
            app.query_one(QueryInput).value = value
            await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        table = app.query_one(SortableTable)
        if header is not None:
            await pilot.click(SortableTable, offset=(_header_x(table, header), 0))
            await pilot.pause()
        bar = app.query_one(QueryBar)
        listing = app.query_one(QueryList)
        status = bar.query_one("#query-status")
        focused = app.focused
        return Shot(
            status=bar.status_text,
            error=status.has_class("-error"),
            value=app.query_one(QueryInput).value,
            shown=listing.shown,
            facets=app.query_one(FacetFilter).counts_text,
            sort=table.sort_keys,
            headers=tuple(str(c.label) for c in table.columns.values()),
            focused=None if focused is None else focused.id,
            svg=_repo_clean(app.export_screenshot()),
        )


def _header_x(table: SortableTable, index: int) -> int:
    widths = [c.get_render_width(table) for c in table.columns.values()]
    return sum(widths[:index]) + 1


def _shoot(
    keys: Sequence[str] = (),
    size: tuple[int, int] = (80, 24),
    header: int | None = None,
    values: Sequence[str] = (),
) -> Shot:
    return asyncio.run(_drive(keys, size, header=header, values=values))


def _kinds(shot: Shot) -> list[object]:
    return [row["kind"] for row in shot.shown]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_status_line_shows_count_and_sort() -> None:
    """The count always; the sort indicator only when sorted."""
    assert (status_line(3, 12, (SortKey("when", True),)), status_line(0, 0, ())) == (
        "3 of 12 · sort when ▼",
        "0 of 0",
    )


def test_facet_lines_count_values_and_fold_the_rest() -> None:
    """Most common first, at most ``limit`` values, then "+N more"; empty facets dropped."""
    rows: list[Row] = [{"coin": c, "kind": "opened"} for c in ("A", "B", "B", "C", "D", "E")]
    assert facet_lines(rows, ("kind", "coin", "run"), limit=3) == (
        "kind  opened 6\ncoin  B 2 · A 1 · C 1 · +2 more"
    )


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------


def test_harness_starts_with_every_row_newest_first() -> None:
    """No query: all six rows, sorted by ``when`` descending, the input focused."""
    shot = _shoot()
    assert (shot.status, [r["when"] for r in shot.shown][:2], shot.focused) == (
        "6 of 6 · sort when ▼",
        ["2026-10-05 11:00", "2026-10-04 18:00"],
        "query-input",
    )


def test_typing_a_query_filters_the_rows() -> None:
    """``coin:btc`` keeps the two BTC rows and the bar says 2 of 6."""
    shot = _shoot(list("coin:btc"))
    assert (shot.status, [(r["coin"], r["kind"]) for r in shot.shown], shot.facets) == (
        "2 of 6 · sort when ▼",
        [("BTC", "opened"), ("BTC", "closed")],
        "kind  closed 1 · opened 1\ncoin  BTC 2",
    )


def test_since_and_year_combine() -> None:
    """``since:2d year>=2026`` keeps the three rows of the last two days."""
    shot = _shoot([*"since:2d", "space", *"year>=2026"])
    assert [r["when"] for r in shot.shown] == [
        "2026-10-05 11:00",
        "2026-10-04 18:00",
        "2026-10-04 10:00",
    ]


def test_free_text_searches_every_value() -> None:
    """A word from a warning's text finds that warning."""
    assert _kinds(_shoot(list("retried"))) == ["warning"]


def test_bad_query_shows_the_error_and_keeps_the_last_result() -> None:
    """An unknown field turns the status line into the error; the rows stay."""
    shot = _shoot(values=["kind:closed", "kind:closed run:1"])
    assert (shot.status, shot.error, _kinds(shot)) == (
        'unknown field "run"; fields: coin, day, kind, month, since, year',
        True,
        ["closed", "closed", "closed"],
    )


def test_fixing_the_query_clears_the_error() -> None:
    """Deleting the bad term brings the count back."""
    shot = _shoot([*"coins", "backspace", *":ETH"])
    assert (shot.status, shot.error) == ("2 of 6 · sort when ▼", False)


def test_tab_completes_the_field_name_and_keeps_focus() -> None:
    """``ki`` then Tab reads ``kind:`` and the input keeps the focus."""
    shot = _shoot(["k", "i", "tab"])
    assert (shot.value, shot.focused) == ("kind:", "query-input")


def test_enter_moves_to_the_table() -> None:
    """Enter on the query line focuses the table."""
    assert _shoot(["enter"]).focused == "query-table"


def test_s_on_the_sorted_column_flips_it() -> None:
    """``S`` on ``When`` (already descending) sorts it ascending."""
    shot = _shoot(["enter", "s"])
    assert (shot.sort, shot.shown[0]["when"], shot.headers[0]) == (
        (SortKey("when"),),
        "2025-12-30 11:00",
        "When ▲",
    )


def test_s_on_another_column_makes_it_primary() -> None:
    """``S`` on ``Kind`` sorts by kind, ties kept newest first."""
    shot = _shoot(["enter", "right", "s"])
    assert (shot.sort, _kinds(shot), [r["when"] for r in shot.shown][:3], shot.status) == (
        (SortKey("kind"), SortKey("when", descending=True)),
        ["closed", "closed", "closed", "opened", "opened", "warning"],
        ["2026-10-04 18:00", "2026-10-02 16:00", "2025-12-30 11:00"],
        "6 of 6 · sort kind ▲ · when ▼",
    )


def test_header_click_sorts_by_that_column() -> None:
    """Clicking the ``Coin`` header sorts by coin; rows without a coin go last."""
    shot = _shoot(header=2)
    assert (shot.sort[0], [r["coin"] for r in shot.shown], shot.headers[2]) == (
        SortKey("coin"),
        ["BTC", "BTC", "ETH", "ETH", "SOL", None],
        "Coin ▲",
    )


def test_query_widgets_import_no_adapter_or_port() -> None:
    """The widgets only read rows they are given."""
    tree = ast.parse(Path(tui_query_module.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert {m for m in imported if m.startswith(("hermes.adapters", "hermes.ports"))} == set()


def test_query_widgets_use_theme_tokens_and_no_raw_colour() -> None:
    """Every colour in the widgets' CSS is a ``$hud-*`` token, so a theme restyles them."""
    source = Path(tui_query_module.__file__).read_text(encoding="utf-8")
    blocks = re.findall(r'DEFAULT_CSS = """(.*?)"""|CSS = """(.*?)"""', source, flags=re.DOTALL)
    css = " ".join(part for block in blocks for part in block)
    colours = re.findall(r"(?:color|background):\s*([^;}\s]+)", css)
    assert (bool(colours), [c for c in colours if not c.startswith("$hud-")]) == (True, [])


def test_the_harness_takes_its_colours_from_the_chosen_theme() -> None:
    """The same list is drawn on the Neon void in Neon and on the base void in Control room."""

    async def screen(theme: str) -> str:
        app = activity_query_app(_ITEMS, MADRID, lambda: _NOW, theme=theme, motion=False)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            return app.export_screenshot()

    neon, control = asyncio.run(screen(NEON_THEME)), asyncio.run(screen(CONTROL_THEME))
    neon, control = neon.lower(), control.lower()
    assert ("#0b0420" in neon, "#0b0420" in control, "#070b14" in control) == (True, False, True)
