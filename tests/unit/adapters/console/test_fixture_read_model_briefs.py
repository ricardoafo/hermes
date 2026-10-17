"""Tests for the briefs a fixture scenario serves (#720, ADR-012 D-06).

Invariants:

- every scenario's brief passes the supervisor desk's output gate, so the demo
  shows the full brief, as the desk would publish it;
- no price, value or P&L figure of a scenario appears anywhere in its brief,
  whatever the prices are (the Briefs screen is P&L-blind);
- the brief describes the scenario: its open positions and its watched streams.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.console import FixtureReadModel, available_scenarios
from hermes.adapters.console.fixture_read_model import desk_inputs
from hermes.application.console.views import ConsoleBrief
from hermes.application.supervisor.briefing import BriefKind, compose


def _text(brief: ConsoleBrief) -> str:
    return "\n".join(f"{s.heading} {s.text} {' '.join(s.sources)}" for s in brief.sections)


def _figures(model: FixtureReadModel) -> set[str]:
    """Every money or price figure a scenario holds, as it could be printed."""
    figures: set[str] = set()
    for row in model.positions():
        for value in (row.entry_price, row.current_price, row.stop_price, row.net):
            figures.add(f"{abs(value):f}")
    summary = model.portfolio()
    for value in (summary.total_value, summary.net, summary.cash, summary.invested):
        figures.add(f"{abs(value):f}")
    return {f for f in figures if f.strip("0.")}


@pytest.mark.parametrize("scenario", available_scenarios())
def test_every_scenario_brief_passes_the_gate(scenario: str) -> None:
    """The demo shows the full brief: it passes the desk's output gate (D-08)."""
    (brief,) = FixtureReadModel.load(scenario).briefs()

    assert brief.digest is False


@pytest.mark.parametrize("scenario", available_scenarios())
def test_no_scenario_figure_reaches_its_brief(scenario: str) -> None:
    """No price, value or net figure of the scenario is written in its brief."""
    model = FixtureReadModel.load(scenario)
    (brief,) = model.briefs()

    text = _text(brief)

    assert [f for f in _figures(model) if f in text] == []
    assert "€" not in text


def test_the_brief_describes_the_scenario() -> None:
    """Open positions, watched streams and health come from the scenario."""
    (brief,) = FixtureReadModel.load("ok-two-positions").briefs()

    text = _text(brief)

    assert brief.kind == "daily"
    assert "2 open position(s)" in text
    assert "ETH/USDT: 6 4h bars checked; no signal; no fills; holding since" in text
    assert "Health ok" in text


@given(
    price=st.decimals(
        min_value=Decimal("10000.01"), max_value=Decimal("99999.99"), places=2, allow_nan=False
    )
)
def test_any_entry_price_stays_out_of_the_brief(price: Decimal) -> None:
    """Whatever a position's prices are, they never reach the desk's input or the brief."""
    model = FixtureReadModel.load("ok-two-positions")
    rows = tuple(replace(r, entry_price=price, current_price=price) for r in model.positions())

    inputs = desk_inputs(model.heartbeat(), rows, model.market_watch())
    text = compose(inputs, kind=BriefKind.DAILY).render()

    assert str(price) not in repr(inputs)
    assert str(price) not in text


def test_the_brief_is_one_daily_brief_at_the_read_time() -> None:
    """The list holds today's daily brief, dated at the scenario's read time."""
    model = FixtureReadModel.load("late")

    assert [(b.kind, b.as_of) for b in model.briefs()] == [("daily", model.heartbeat().as_of)]
