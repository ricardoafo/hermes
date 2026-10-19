"""The shared drill-down framework of the Cockpit console (issue #730).

Invariants:

- a request for a ``DetailPane`` opens a ``DetailScreen`` below 100 columns
  (or when the host has no room beside the list);
- ``[`` and ``]`` move to the previous / next item and STOP at the ends
  (no wrap: inside a detail the list is out of sight, so a wrap would jump
  from the last item to the first without the operator seeing it);
- Esc or Backspace leaves a ``DetailScreen`` and hands the list its saved
  filters, sort and cursor back, the cursor on the item shown last;
- the breadcrumb reads ``◂ System › Releases › 2bc2798`` and, when it does
  not fit, gives up the leading crumbs first and keeps the item;
- ``TruncatedText`` cuts at the panel edge with "…", ends a cut list with
  "+N", and shows the full text on Enter;
- number keys and Ctrl+P keep working inside a ``DetailScreen``;
- on Home the positions detail is a ``DetailPane`` when wide and a
  ``DetailScreen`` on compact terminals, with its content unchanged.

Every app here gets a frozen clock and motion off; the SVG pictures live in
``test_tui_snapshots.py`` (``drill_position_*`` and ``drill_breadcrumb_*``).
"""

# ruff: noqa: RUF001, RUF002 - the single right angle quote is the design spec's breadcrumb separator

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st
from rich.cells import cell_len
from rich.text import Text
from textual.app import App, ComposeResult
from textual.command import CommandPalette
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_drilldown import (
    Breadcrumb,
    DetailMode,
    DetailPane,
    DetailScreen,
    ListState,
    TruncatedText,
    breadcrumb_text,
    detail_mode,
    item_position,
    step_item,
    truncate_items,
    truncate_line,
)
from hermes.entrypoints.tui_theme import BASE, theme_variables

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_ITEMS = ("2bc2798", "9f01a3c", "77de210")


def _screen_text(app: App[None]) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


async def _press(pilot: Pilot[None], keys: Sequence[str]) -> None:
    for key in keys:
        await pilot.press(key)
        await pilot.pause()


# ----------------------------------------------------------------------
# Pane or screen
# ----------------------------------------------------------------------


def test_compact_width_opens_a_screen_instead_of_a_pane() -> None:
    """Below 100 columns a pane request becomes a screen; from 100 it stays a pane."""
    assert (detail_mode(80), detail_mode(99), detail_mode(100), detail_mode(160)) == (
        DetailMode.SCREEN,
        DetailMode.SCREEN,
        DetailMode.PANE,
        DetailMode.PANE,
    )


def test_a_host_without_room_beside_the_list_opens_a_screen() -> None:
    """A wide terminal still gets a screen when the host cannot place the pane beside the list."""
    assert detail_mode(110, beside=False) is DetailMode.SCREEN


# ----------------------------------------------------------------------
# Previous / next
# ----------------------------------------------------------------------


def test_next_and_previous_move_one_item() -> None:
    """] moves to the following item, [ to the one before."""
    assert (step_item(_ITEMS, "9f01a3c", 1), step_item(_ITEMS, "9f01a3c", -1)) == (
        "77de210",
        "2bc2798",
    )


def test_previous_and_next_stop_at_the_ends() -> None:
    """[ on the first item and ] on the last stay put: the rule is stop, not wrap."""
    assert (step_item(_ITEMS, "2bc2798", -1), step_item(_ITEMS, "77de210", 1)) == (
        "2bc2798",
        "77de210",
    )


def test_an_item_that_left_the_list_steps_to_the_first() -> None:
    """When the shown item vanished on a refresh, [ or ] lands on the first item still listed."""
    assert step_item(_ITEMS, "gone", 1) == "2bc2798"


def test_an_empty_list_keeps_the_current_item() -> None:
    """With nothing listed there is nowhere to go; the current key comes back unchanged."""
    assert step_item((), "2bc2798", 1) == "2bc2798"


@given(
    st.lists(st.text(min_size=1, max_size=4), min_size=1, max_size=8, unique=True),
    st.integers(min_value=0, max_value=7),
    st.sampled_from([-1, 1]),
)
def test_step_never_leaves_the_list_and_moves_at_most_one(
    keys: list[str], start: int, step: int
) -> None:
    """Property: a step lands on a listed item, at most one place away from where it started."""
    current = keys[start % len(keys)]
    moved = step_item(keys, current, step)
    assert abs(keys.index(moved) - keys.index(current)) <= 1


def test_item_position_names_the_place_in_the_list() -> None:
    """The footer reads "2 of 3"; an item no longer listed shows nothing."""
    assert (item_position(_ITEMS, "9f01a3c"), item_position(_ITEMS, "gone")) == ("2 of 3", "")


# ----------------------------------------------------------------------
# Breadcrumb
# ----------------------------------------------------------------------


def test_breadcrumb_text_reads_back_trail_and_item() -> None:
    """The top bar of a detail screen reads "◂ System › Releases › 2bc2798"."""
    text = breadcrumb_text(("System", "Releases", "2bc2798"), BASE)
    assert text.plain == "◂ System › Releases › 2bc2798"


def test_breadcrumb_gives_up_leading_crumbs_first() -> None:
    """Too narrow: the leading crumbs become "…" and the item stays whole."""
    text = breadcrumb_text(("System", "Releases", "2bc2798"), BASE, width=24)
    assert text.plain == "◂ … › Releases › 2bc2798"


def test_breadcrumb_item_is_styled_by_tokens() -> None:
    """The back glyph is drawn in the live token, the item in bold text."""
    text = breadcrumb_text(("Home", "Positions", "BTC"), BASE)
    styles = {span.style for span in text.spans}
    assert {f"bold {BASE.live}", f"bold {BASE.text}"} <= {str(style) for style in styles}


# ----------------------------------------------------------------------
# TruncatedText
# ----------------------------------------------------------------------


def test_truncate_line_cuts_with_an_ellipsis_at_the_edge() -> None:
    """A line wider than the panel ends in "…" and fills the width exactly."""
    assert truncate_line("stop moved to 61,200 after the 18:00 bar", 20) == "stop moved to 61,20…"


def test_truncate_line_leaves_a_fitting_line_alone() -> None:
    """A line that fits is returned unchanged, with no ellipsis."""
    assert truncate_line("fits", 20) == "fits"


def test_truncate_line_marks_hidden_lines() -> None:
    """A text with more lines shows its first line and "…" for the rest."""
    assert truncate_line("first line\nsecond line", 20) == "first line…"


@given(st.text(max_size=60), st.integers(min_value=0, max_value=40))
def test_truncate_line_never_overflows(text: str, width: int) -> None:
    """Property: the result fits; a line that fits is kept whole, any other ends in "…"."""
    cut = truncate_line(text, width)
    lines = text.splitlines()
    first = lines[0] if lines else ""
    assert cell_len(cut) <= width
    if len(lines) <= 1 and cell_len(first) <= width:
        assert cut == first
    elif width > 0:
        assert cut.endswith("…")
        assert first.startswith(cut[:-1])


def test_truncate_items_counts_what_was_cut() -> None:
    """A list cut at the edge keeps the items that fit and ends with "+N" for the rest."""
    items = ["BTC", "ETH", "SOL", "ADA", "XRP"]
    assert truncate_items(items, 17) == "BTC · ETH +3"


def test_truncate_items_shows_the_whole_list_when_it_fits() -> None:
    """No "+N" when nothing was left out."""
    assert truncate_items(["BTC", "ETH"], 40) == "BTC · ETH"


def test_truncate_items_cuts_the_first_item_when_none_fits() -> None:
    """One long item and more behind it: the item gets "…" and the count follows."""
    assert truncate_items(["a-very-long-coin-name", "ETH"], 10) == "a-very… +1"


@given(
    st.lists(st.text(alphabet="abcXYZ", min_size=1, max_size=6), min_size=1, max_size=8),
    st.integers(min_value=6, max_value=50),
)
def test_truncate_items_fits_and_counts(items: list[str], width: int) -> None:
    """Property: the result fits; a "+N" suffix names exactly the items not shown whole."""
    cut = truncate_items(items, width)
    assert cell_len(cut) <= width
    if " +" in cut:
        hidden = int(cut.rsplit(" +", 1)[1])
        shown = cut.rsplit(" +", 1)[0]
        whole = 0 if shown.endswith("…") else len(shown.split(" · "))
        assert hidden == len(items) - max(whole, 1)


class _TokenApp(App[None]):
    """An app that gives TCSS the console's ``hud-*`` token variables."""

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return theme_variables(BASE)


class _TruncHost(_TokenApp):
    def __init__(self, value: str | Sequence[str]) -> None:
        super().__init__()
        self.value = value

    def compose(self) -> ComposeResult:
        yield TruncatedText(self.value, id="trunc")


def _truncated(value: str | Sequence[str], keys: Sequence[str]) -> str:
    async def drive() -> str:
        app = _TruncHost(value)
        async with app.run_test(size=(24, 6)) as pilot:
            await pilot.pause()
            app.query_one(TruncatedText).focus()
            await _press(pilot, keys)
            return _screen_text(app)

    return asyncio.run(drive())


def test_truncated_text_shows_one_cut_line() -> None:
    """Collapsed, a long text is one line ending in "…" at the widget's edge."""
    shown = _truncated("stop moved to 61,200 after the 18:00 bar", [])
    assert shown.splitlines()[0] == "stop moved to 61,200 af…"


def test_truncated_text_expands_on_enter() -> None:
    """Enter shows the full text, wrapped; a second Enter folds it again."""
    long = "stop moved to 61,200 after the 18:00 bar"
    opened = _truncated(long, ["enter"])
    folded = _truncated(long, ["enter", "enter"])
    assert (
        opened.splitlines()[:2],
        folded.splitlines()[0],
    ) == (["stop moved to 61,200", "after the 18:00 bar"], "stop moved to 61,200 af…")


def test_truncated_list_shows_the_count_then_every_item_on_enter() -> None:
    """A cut list ends in "+N"; Enter lists every item."""
    coins = ["BTC", "ETH", "SOL", "ADA", "XRP", "DOT"]
    folded = _truncated(coins, [])
    opened = _truncated(coins, ["enter"])
    assert (folded.splitlines()[0], opened.splitlines()[0]) == (
        "BTC · ETH · SOL · ADA +2",
        "BTC · ETH · SOL · ADA ·",
    )


# ----------------------------------------------------------------------
# DetailScreen with a host list
# ----------------------------------------------------------------------


class _ListHost(_TokenApp):
    """A list with filters, sort and cursor that opens its items in a DetailScreen."""

    def __init__(self) -> None:
        super().__init__()
        self.state = ListState(
            filters=(("since", "7d"), ("coin", "BTC")),
            sort="deployed",
            descending=True,
            cursor="9f01a3c",
        )
        self.restored: list[ListState] = []

    def list_state(self) -> ListState:
        return self.state

    def restore_list_state(self, state: ListState | None) -> None:
        if state is not None:
            self.state = state
            self.restored.append(state)

    def open_detail(self) -> None:
        current = self.state.cursor or _ITEMS[0]
        self.push_screen(
            DetailScreen(
                trail=("System", "Releases"),
                keys=lambda: _ITEMS,
                current=current,
                body=lambda key: Text(f"release {key}"),
                title="Release",
                state=self.list_state(),
            ),
            self.restore_list_state,
        )


def _host_run[T](probe: Callable[[_ListHost, Pilot[None]], Awaitable[T]]) -> T:
    async def drive() -> T:
        app = _ListHost()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.open_detail()
            await pilot.pause()
            return await probe(app, pilot)

    return asyncio.run(drive())


def test_back_restores_filters_sort_and_cursor() -> None:
    """A refresh wiped the list while the detail was open; Esc hands the saved state back."""

    async def probe(app: _ListHost, pilot: Pilot[None]) -> tuple[ListState, int]:
        app.state = ListState()
        await _press(pilot, ["escape"])
        return app.state, len(app.screen_stack)

    assert _host_run(probe) == (
        ListState(
            filters=(("since", "7d"), ("coin", "BTC")),
            sort="deployed",
            descending=True,
            cursor="9f01a3c",
        ),
        1,
    )


def test_backspace_goes_back_like_escape() -> None:
    """Backspace leaves the detail screen and restores the list too."""

    async def probe(app: _ListHost, pilot: Pilot[None]) -> tuple[int, int]:
        await _press(pilot, ["backspace"])
        return len(app.screen_stack), len(app.restored)

    assert _host_run(probe) == (1, 1)


def test_back_puts_the_cursor_on_the_item_shown_last() -> None:
    """] moves to the next release; back leaves the list cursor on it, filters and sort intact."""

    async def probe(app: _ListHost, pilot: Pilot[None]) -> ListState:
        await _press(pilot, ["right_square_bracket", "escape"])
        return app.state

    assert _host_run(probe) == ListState(
        filters=(("since", "7d"), ("coin", "BTC")),
        sort="deployed",
        descending=True,
        cursor="77de210",
    )


def test_brackets_move_through_items_and_stop_at_the_end() -> None:
    """] twice from the middle shows the last release, and stays there; [ goes back one."""

    async def probe(app: _ListHost, pilot: Pilot[None]) -> list[str]:
        screen = app.screen
        assert isinstance(screen, DetailScreen)
        seen = []
        for key in ("right_square_bracket", "right_square_bracket", "left_square_bracket"):
            await _press(pilot, [key])
            seen.append(screen.current)
        return seen

    assert _host_run(probe) == ["77de210", "77de210", "9f01a3c"]


def test_detail_screen_top_bar_is_the_breadcrumb() -> None:
    """The first row of the screen is the breadcrumb, ending with the item shown."""

    async def probe(app: _ListHost, pilot: Pilot[None]) -> tuple[str, str]:
        first = _screen_text(app).splitlines()[0].strip()
        await _press(pilot, ["right_square_bracket"])
        return first, _screen_text(app).splitlines()[0].strip()

    assert _host_run(probe) == (
        "◂ System › Releases › 9f01a3c",
        "◂ System › Releases › 77de210",
    )


def test_detail_screen_body_and_footer_follow_the_item() -> None:
    """The body shows the item's detail and the footer its place in the list."""

    async def probe(app: _ListHost, pilot: Pilot[None]) -> tuple[bool, bool]:
        await _press(pilot, ["left_square_bracket"])
        text = _screen_text(app)
        return "release 2bc2798" in text, text.splitlines()[-1].rstrip().endswith(
            "1 of 3 · read-only"
        )

    assert _host_run(probe) == (True, True)


def test_breadcrumb_widget_repaints_on_a_new_trail() -> None:
    """Setting a new trail on the widget redraws it with the new item."""

    class _Host(_TokenApp):
        def compose(self) -> ComposeResult:
            yield Breadcrumb(("Home", "Positions", "BTC"))

    async def drive() -> tuple[str, str]:
        app = _Host()
        async with app.run_test(size=(60, 3)) as pilot:
            await pilot.pause()
            before = _screen_text(app).splitlines()[0].strip()
            app.query_one(Breadcrumb).trail = ("Home", "Positions", "SOL")
            await pilot.pause()
            return before, _screen_text(app).splitlines()[0].strip()

    assert asyncio.run(drive()) == ("◂ Home › Positions › BTC", "◂ Home › Positions › SOL")


# ----------------------------------------------------------------------
# Home adoption: the positions detail
# ----------------------------------------------------------------------


def _home[T](
    probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]], size: tuple[int, int] = (80, 24)
) -> T:
    async def drive() -> T:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=lambda: _FROZEN,
            motion=False,
            settings_path=None,
            refresh_seconds=3600,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return await probe(app, pilot)

    return asyncio.run(drive())


def test_home_compact_enter_opens_a_detail_screen() -> None:
    """At 80 columns Enter on a position opens a DetailScreen with the Home breadcrumb."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str]:
        await _press(pilot, ["enter"])
        return isinstance(app.screen, DetailScreen), _screen_text(app).splitlines()[0].strip()

    assert _home(probe) == (True, "◂ Home › Positions › ETH")


def test_home_wide_enter_opens_the_pane_beside_the_list() -> None:
    """At 160 columns Enter opens the DetailPane on the home screen; no screen is pushed."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, int]:
        await _press(pilot, ["enter"])
        return app.query_one("#detail-panel", DetailPane).is_open, len(app.screen_stack)

    assert _home(probe, size=(160, 45)) == (True, 1)


def test_home_from_100_columns_opens_the_pane_not_a_screen() -> None:
    """At 100 columns Home has two columns, so Enter opens the pane, not a screen."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, int]:
        await _press(pilot, ["enter"])
        return app.query_one("#detail-panel", DetailPane).is_open, len(app.screen_stack)

    assert _home(probe, size=(100, 30)) == (True, 1)


def test_backspace_closes_the_home_pane_like_escape() -> None:
    """Backspace closes the open DetailPane on Home and stays on Home."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool, int]:
        await _press(pilot, ["enter"])
        opened = app.query_one("#detail-panel", DetailPane).is_open
        await _press(pilot, ["backspace"])
        return opened, app.query_one("#detail-panel", DetailPane).is_open, app.tab

    assert _home(probe, size=(160, 45)) == (True, False, 0)


def test_help_names_the_drill_down_keys() -> None:
    """Help lists Backspace as back and [ ] as previous / next inside a detail screen."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool]:
        await _press(pilot, ["question_mark"])
        text = _screen_text(app)
        return "Esc or Backspace back" in text, (
            "[ ] previous / next item inside a detail screen" in text
        )

    assert _home(probe, size=(120, 36)) == (True, True)


def test_home_detail_content_is_the_same_as_a_pane_or_a_screen() -> None:
    """The position detail reads the same whether it opens as a pane or as a screen."""

    async def pane(app: HomeApp, pilot: Pilot[None]) -> str:
        await _press(pilot, ["enter"])
        return app.plain_text("detail")

    async def screen(app: HomeApp, pilot: Pilot[None]) -> bool:
        await _press(pilot, ["enter"])
        return "Money if sold now" in _screen_text(app)

    assert ("Money if sold now" in _home(pane, size=(160, 45)), _home(screen)) == (True, True)


def test_home_back_from_the_screen_keeps_the_cursor_on_the_item_shown() -> None:
    """] moves the detail to SOL; back leaves the positions cursor on SOL."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, int]:
        await _press(pilot, ["enter", "right_square_bracket", "escape"])
        return len(app.screen_stack), app.plain_text("positions").count("▸SOL")

    assert _home(probe) == (1, 1)


def test_number_keys_still_jump_from_a_detail_screen() -> None:
    """3 inside the detail screen closes it and shows the Positions tab."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, int]:
        await _press(pilot, ["enter", "3"])
        return app.tab, len(app.screen_stack)

    assert _home(probe) == (2, 1)


def test_ctrl_p_still_opens_the_palette_from_a_detail_screen() -> None:
    """Ctrl+P inside the detail screen opens the command palette."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> bool:
        await _press(pilot, ["enter", "ctrl+p"])
        return isinstance(app.screen, CommandPalette)

    assert _home(probe) is True


def test_home_list_state_carries_the_cursor() -> None:
    """Home's positions list saves its cursor; restoring a state moves the cursor."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[ListState, str | None]:
        saved = app.list_state()
        app.restore_list_state(ListState(cursor="SOL"))
        await pilot.pause()
        return saved, app.list_state().cursor

    assert _home(probe) == (ListState(cursor="ETH"), "SOL")
