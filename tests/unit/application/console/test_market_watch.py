"""The market watch view: fixture data, rendering and visibility (#650).

All data is synthetic: packaged demo scenarios or values built in the test.

Invariants:

- every demo scenario carries a market watch; the empty one watches nothing;
- a scenario signal is "entry", "exit" or null, and the next check follows the last;
- the panel shows coin, timeframe, last check, its result and the next check,
  and never a price or a money figure, on any surface;
- the panel reads the same on operator and blind surfaces, since it carries
  nothing ``pnl_visible`` hides;
- the overview places the market watch right after the positions.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from hermes.adapters.console import FixtureReadModel, ScenarioError, available_scenarios
from hermes.application.console import Surface, WatchedPair, pnl_visible
from hermes.application.console.render import (
    RenderContext,
    render_market_watch,
    render_overview,
    to_plain_text,
)

_NOW = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_BLIND = tuple(s for s in Surface if not pnl_visible(s))
_CLOCK_OR_TIMEFRAME = re.compile(r"\b\d{2}:\d{2}\b|\b\d+[hd]\b")


def _ctx(surface: Surface = Surface.OPERATOR_CLI) -> RenderContext:
    return RenderContext(surface=surface, now=_NOW, width=80)


def _rows() -> tuple[WatchedPair, ...]:
    last = datetime(2026, 10, 3, 12, tzinfo=UTC)
    return (
        WatchedPair("BTC", "USDT", "4h", last, "entry", last + timedelta(hours=4)),
        WatchedPair("ETH", "USDT", "4h", last, "exit", last + timedelta(hours=4)),
        WatchedPair(
            "SOL",
            "USDT",
            "1d",
            datetime(2026, 10, 3, tzinfo=UTC),
            None,
            datetime(2026, 10, 4, tzinfo=UTC),
        ),
    )


def _ok_document() -> dict[str, Any]:
    path = resources.files("hermes.adapters.console") / "scenarios" / "ok-two-positions.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def _load(tmp_path: Path, document: dict[str, Any]) -> FixtureReadModel:
    path = tmp_path / "custom.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return FixtureReadModel.from_path(path)


@pytest.mark.parametrize("scenario", [s for s in available_scenarios() if s != "empty"])
def test_every_demo_scenario_watches_btc_eth_and_sol(scenario: str) -> None:
    """Each non-empty scenario lists the three watched coins, each with a later next check."""
    rows = FixtureReadModel.load(scenario).market_watch()
    assert (
        [row.coin for row in rows],
        all(row.next_check_at > row.last_check_at for row in rows),
    ) == (["BTC", "ETH", "SOL"], True)


def test_empty_scenario_watches_nothing() -> None:
    """The empty scenario has no checked stream."""
    assert FixtureReadModel.load("empty").market_watch() == ()


def test_scenario_rows_are_read_exactly() -> None:
    """The fixture keeps each field as written: SOL is a daily stream checked at midnight UTC."""
    sol = FixtureReadModel.load("ok-two-positions").market_watch()[2]
    assert sol == WatchedPair(
        coin="SOL",
        quote="USDT",
        timeframe="1d",
        last_check_at=datetime(2026, 10, 3, tzinfo=UTC),
        signal=None,
        next_check_at=datetime(2026, 10, 4, tzinfo=UTC),
    )


def test_unknown_signal_kind_fails_on_load(tmp_path: Path) -> None:
    """A signal other than entry, exit or null is a broken scenario."""
    document = _ok_document()
    document["market_watch"][0]["signal"] = "maybe"
    with pytest.raises(ScenarioError, match="unknown value 'maybe'"):
        _load(tmp_path, document)


def test_next_check_before_last_check_fails_on_load(tmp_path: Path) -> None:
    """A next check that does not follow the last check is a broken scenario."""
    document = _ok_document()
    document["market_watch"][0]["next_check_at"] = document["market_watch"][0]["last_check_at"]
    with pytest.raises(ScenarioError, match="next_check_at must follow last_check_at"):
        _load(tmp_path, document)


def test_panel_lists_each_stream_and_its_result() -> None:
    """Rows read coin, timeframe, last check, result and next check in Madrid time."""
    lines = to_plain_text(render_market_watch(_rows(), _ctx()), width=80).splitlines()
    assert lines == [
        "Market watch",
        "coin   tf   last check   result   next check",
        "────────────────────────────────────────────",
        "BTC    4h   14:00        entry    18:00",
        "ETH    4h   14:00        exit     18:00",
        "SOL    1d   02:00        none     Sun 02:00",
    ]


def test_empty_panel_says_nothing_is_checked_yet() -> None:
    """With no checked stream the panel says so instead of drawing an empty table."""
    text = to_plain_text(render_market_watch((), _ctx()), width=80)
    assert text == "Market watch\nNo pairs checked yet.\n"


@pytest.mark.parametrize("surface", list(Surface), ids=lambda s: s.value)
def test_panel_never_shows_a_price_or_money(surface: Surface) -> None:
    """No euro sign appears, and the only digits are check times and timeframes."""
    text = to_plain_text(render_market_watch(_rows(), _ctx(surface)), width=80)
    rest = _CLOCK_OR_TIMEFRAME.sub("", text)
    assert ("€" in text, re.findall(r"\d", rest)) == (False, [])


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
def test_blind_surface_shows_the_same_panel(surface: Surface) -> None:
    """The panel carries nothing pnl_visible hides, so blind surfaces see the operator's rows."""
    operator = to_plain_text(render_market_watch(_rows(), _ctx()), width=80)
    blind = to_plain_text(render_market_watch(_rows(), _ctx(surface)), width=80)
    assert (blind == operator, blind.splitlines()[3]) == (
        True,
        "BTC    4h   14:00        entry    18:00",
    )


def test_overview_places_the_market_watch_after_positions() -> None:
    """In the stacked overview the market watch comes after positions and before activity."""
    model = FixtureReadModel.load("ok-two-positions")
    ctx = RenderContext.for_model(model, surface=Surface.OPERATOR_CLI, width=80)
    lines = to_plain_text(render_overview(model, ctx), width=80).splitlines()
    order = [
        next(i for i, line in enumerate(lines) if line.startswith(head))
        for head in ("coin   tf   entry", "Market watch", "Today")
    ]
    assert order == sorted(order)
