"""Tests for the Signals screen of the Cockpit console (issue #714).

Invariants:

- tab 2 shows the week's bar checks per coin, the last check's rules as yes
  or no, the week's counts and the frozen rules;
- a square's colour follows how many rules held, and only a signal is a ◆;
- a held coin's row says "holding" rather than a check serve never ran;
- the week's counts and the per-rule shares follow the checks;
- the week and the rules sit beside the checks from 120 columns, below them
  on narrower screens;
- no price, value or distance of the scenario is drawn on the screen;
- a read model that cannot serve rule checks leaves one "not available yet" line;
- the reviewed picture of the screen, in Neon and Control room at the spec's
  four breakpoints, only changes when a reviewer accepts it.

Every app runs with motion off, a frozen clock and synthetic demo data, and
never reads or writes the real settings file.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st
from rich.text import Text
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import ConsoleReadModel, Surface, ViewNotAvailableError
from hermes.application.console.render import RenderContext, to_plain_text
from hermes.application.console.views import RuleCheck
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_signals import (
    BESIDE_FROM,
    FROZEN_RULES,
    NO_CHECKS,
    RULES_NOTE,
    signals_screen,
)
from hermes.entrypoints.tui_theme import BASE

# The fixtures' read time (14:12 Madrid), well inside Auto's day window.
_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_LAST = datetime(2026, 10, 3, 12, tzinfo=UTC)
_CTX = RenderContext(surface=Surface.OPERATOR_TUI, now=_FROZEN)
_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)


def _check(
    coin: str = "BTC", at: datetime = _LAST, rules: tuple[bool, bool, bool] = (False, True, True)
) -> RuleCheck:
    breakout, momentum, regime = rules
    return RuleCheck(at, coin, "4h", breakout=breakout, momentum=momentum, regime=regime)


def _plain(checks: Sequence[RuleCheck], width: int, held: frozenset[str] = frozenset()) -> str:
    body = signals_screen(checks, _CTX, BASE, width=width, held=held)
    return "\n".join(line.rstrip() for line in to_plain_text(body, width=width).splitlines())


def _row(text: str, start: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(start))


def test_the_last_check_shows_each_rule_as_yes_or_no() -> None:
    """The last check's row reads breakout, trend and momentum as yes/no, then the result."""
    text = _plain([_check(rules=(False, True, True))], 80)

    assert _row(text, "BTC    ").split() == ["BTC", "no", "yes", "yes", "2", "of", "3"]


def test_a_signal_reads_signal() -> None:
    """All three rules met is a signal, and the row says so."""
    text = _plain([_check(rules=(True, True, True))], 80)

    assert _row(text, "BTC    ").split()[-2:] == ["◆", "signal"]


def test_a_held_coin_reads_holding() -> None:
    """A coin with a position gets no entry check, so its row says it is held."""
    text = _plain([_check()], 80, held=frozenset({"ETH"}))

    assert _row(text, "ETH    ").split() == ["ETH", "—", "—", "—", "holding"]


def test_only_a_signal_is_drawn_as_a_diamond() -> None:
    """Each square shows how many rules held; the ◆ glyph is kept for all three."""
    earlier = [
        _check(at=_LAST - timedelta(hours=4 * n), rules=(False, False, False)) for n in (1, 2)
    ]
    checks = [*earlier, _check(rules=(True, True, True))]

    assert _row(_plain(checks, 80), "BTC  ") == "BTC  ■■◆"


@given(st.lists(st.tuples(st.booleans(), st.booleans(), st.booleans()), min_size=1, max_size=12))
def test_square_colours_follow_the_rules_met(rules: list[tuple[bool, bool, bool]]) -> None:
    """INV: every square of a coin is styled by its own check's count of rules met."""
    checks = [_check(at=_LAST - timedelta(hours=4 * n), rules=r) for n, r in enumerate(rules)]
    styles = (BASE.line, BASE.gain[0], BASE.up, f"bold {BASE.live}")
    body = signals_screen(checks, _CTX, BASE, width=80)
    expected = [styles[c.met] for c in sorted(checks, key=lambda c: c.at)]

    grid_row = _grid_row(body)

    assert [str(span.style) for span in grid_row.spans] == expected


def _grid_row(body: object) -> Text:
    """Return the first heat-grid row of a stacked screen (its groups nest, no tables)."""
    stack: list[object] = [body]
    while stack:
        item = stack.pop(0)
        if (
            isinstance(item, Text)
            and item.plain.startswith("BTC  ")
            and {"■", "◆"} & set(item.plain)
        ):
            return item
        stack.extend(getattr(item, "renderables", ()))
    raise AssertionError("no heat-grid row")


def test_the_week_counts_checks_signals_and_rule_shares() -> None:
    """The week panel counts every check, every signal and how often each rule held."""
    checks = [
        _check(at=_LAST, rules=(True, True, True)),
        _check(at=_LAST - timedelta(hours=4), rules=(False, True, True)),
        _check(at=_LAST - timedelta(hours=8), rules=(False, False, True)),
        _check(at=_LAST - timedelta(hours=12), rules=(False, False, True)),
    ]
    text = _plain(checks, 80)

    assert "4  bar checks" in text
    assert _row(text, "signals").split()[:2] == ["signals", "1"]
    assert _row(text, "breakout 4h ").endswith("25%")
    assert _row(text, "trend 1D ").endswith("100%")
    assert _row(text, "momentum 4h ").endswith("50%")


def test_checks_older_than_a_week_are_not_shown() -> None:
    """With no check in the last 7 days the screen says so and keeps the rules."""
    text = _plain([_check(at=_FROZEN - timedelta(days=8))], 80)

    assert text.splitlines()[0] == NO_CHECKS
    assert "The rules · frozen" in text


def test_the_frozen_rules_are_listed_with_the_distance_note() -> None:
    """Each pre-registered entry rule is listed in words, with the yes/no-only note."""
    text = _plain([_check()], 80)

    for name, words in FROZEN_RULES:
        assert re.search(rf"{re.escape(name)}\s+{re.escape(words)}", text)
    assert RULES_NOTE in text


def test_the_week_sits_beside_the_checks_from_120_columns() -> None:
    """From 120 columns the checks' title and the week's title share the first line."""
    wide = _plain([_check()], BESIDE_FROM).splitlines()
    narrow = _plain([_check()], BESIDE_FROM - 1).splitlines()

    assert wide[0].startswith("Bar checks · last 7 days")
    assert wide[0].endswith("This week")
    assert narrow[0] == "Bar checks · last 7 days"
    assert "This week" in narrow


def _app(model: ConsoleReadModel, theme: ThemeChoice = ThemeChoice.NEON) -> HomeApp:
    return HomeApp(
        model,
        clock=lambda: _FROZEN,
        motion=False,
        settings_path=None,
        theme=theme,
        refresh_seconds=3600,
    )


def _signals_text(model: ConsoleReadModel) -> str:
    async def drive() -> str:
        app = _app(model)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            assert app.tab == 1
            return app.plain_text("screen-2")

    return asyncio.run(drive())


def test_pressing_2_shows_the_scenarios_checks() -> None:
    """Tab 2 draws the scenario's last check and holds for its open positions."""
    text = _signals_text(FixtureReadModel.load("ok-two-positions"))

    assert "Last check · 14:00 bar" in text
    assert _row(text, "BTC    ").split()[:7] == ["BTC", "yes", "yes", "no", "2", "of", "3"]
    assert _row(text, "ETH    ").split()[:5] == ["ETH", "—", "—", "—", "holding"]
    assert "signals         1  last ETH, Fri 10:00" in text


def test_the_screen_draws_no_scenario_figure() -> None:
    """No entry, current or stop price, net or total of the scenario is on the screen."""
    model = FixtureReadModel.load("ok-two-positions")
    figures = {
        f"{value:,.2f}"
        for row in model.positions()
        for value in (row.entry_price, row.current_price, row.stop_price, abs(row.net))
    } | {f"{model.portfolio().total_value:,.2f}"}

    text = _signals_text(model)

    assert [f for f in figures if f in text] == []
    assert "€" not in text


class _NoJournal(FixtureReadModel):
    """A demo scenario whose journal cannot be read."""

    def rule_checks(self) -> tuple[RuleCheck, ...]:
        raise ViewNotAvailableError("rule checks: the hermes journal is not readable here")


def test_without_a_journal_the_screen_is_not_available_yet() -> None:
    """A read model that cannot serve rule checks leaves one plain line, not a crash."""
    model = _NoJournal("ok-two-positions", _document("ok-two-positions"))

    assert _signals_text(model).strip() == "Signals · not available yet"


def _document(name: str) -> dict[str, object]:
    path = resources.files("hermes.adapters.console") / "scenarios" / f"{name}.json"
    loaded: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


class _NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``signals_neon_160x45``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


def _svg(size: tuple[int, int], theme: ThemeChoice) -> str:
    async def drive() -> str:
        app = _app(FixtureReadModel.load("ok-two-positions"), theme)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            svg = app.export_screenshot()
        return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"

    return asyncio.run(drive())


@pytest.mark.parametrize(
    ("theme_name", "theme", "size"),
    [
        pytest.param(name, theme, size, id=f"signals_{name}_{size[0]}x{size[1]}")
        for name, theme in _THEMES
        for size in _SIZES
    ],
)
def test_signals_svg_snapshot(
    snapshot: SnapshotAssertion, theme_name: str, theme: ThemeChoice, size: tuple[int, int]
) -> None:
    """The reviewed picture of the Signals screen, in one theme, at one spec breakpoint."""
    name = f"signals_{theme_name}_{size[0]}x{size[1]}"
    assert _svg(size, theme) == snapshot(extension_class=_NamedSVG, name=name)
