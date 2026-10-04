"""The weekly observation report is blind text for a pushed message.

Invariants:

- the report does not depend on shadow P&L or the Sharpe trigger's day count:
  it is identical with and without them, and prints no money figure
  (decision record 2026-10-03 rule 4; #319);
- it names day N of 90, the drift verdict and any fired kill trigger;
- demo data is tagged;
- snapshots pin the reviewed text per scenario.
"""

from __future__ import annotations

import pytest
from syrupy.assertion import SnapshotAssertion

from hermes.adapters.console import FixtureReadModel, available_scenarios
from hermes.application.console import KillState
from hermes.application.console.weekly_report import weekly_report
from tests.unit.application.console._observation import (
    LEAKY_MONEY,
    LEAKY_SHARPE_DAYS,
    not_started,
    window,
)

_LEAK_STRINGS = ("98765", "98,765", "7.77", "47", "€", "EUR", "P&L", "equity", "Sharpe")


def test_report_does_not_depend_on_pnl() -> None:
    """The text is the same whatever money and Sharpe-day count the view holds."""
    leaky = window(money=LEAKY_MONEY, sharpe_days=LEAKY_SHARPE_DAYS, shock=KillState.FIRED)
    assert weekly_report(leaky) == weekly_report(window(shock=KillState.FIRED))


@pytest.mark.parametrize("shock", list(KillState), ids=lambda s: s.value)
def test_report_prints_no_pnl_figure(shock: KillState) -> None:
    """No money, equity or Sharpe figure reaches the pushed text."""
    text = weekly_report(window(money=LEAKY_MONEY, sharpe_days=LEAKY_SHARPE_DAYS, shock=shock))
    assert [s for s in _LEAK_STRINGS if s in text] == []


@pytest.mark.parametrize("scenario", available_scenarios())
def test_scenario_report_prints_no_pnl_figure(scenario: str) -> None:
    """No scenario's money figures reach its report."""
    model = FixtureReadModel.load(scenario)
    money = model.observation().money
    text = weekly_report(model.observation())
    figures = [] if money is None else [f"{abs(money.net):.2f}", f"{money.sharpe}"]
    assert [s for s in [*figures, "€", "Sharpe"] if s in text] == []


def test_report_names_the_day_and_the_verdict() -> None:
    """Line two and three: day N of 90 and the drift verdict against the gate."""
    lines = weekly_report(window(drift=("31", "30", "40"))).splitlines()
    assert lines[1:3] == [
        "Shadow observation · day 13 of 90 · on track",
        "Fill drift to date: fail · p50 31.0 · p95 39.1 (bps; gate p50 < 30)",
    ]


def test_report_lists_a_fired_trigger() -> None:
    """A fired kill trigger is spelled out, not summarised."""
    text = weekly_report(window(shock=KillState.FIRED))
    assert "  FIRED · shock overlay 3+ times in 30 days (3 of 3)" in text.splitlines()


def test_report_before_day_one_says_not_started() -> None:
    """Before day 1 the report is the title and one line."""
    assert weekly_report(not_started()).splitlines() == [
        "Hermes weekly · week ending Sun 04 Oct 2026",
        "Shadow observation · not started · waits on #353",
    ]


def test_demo_report_is_tagged() -> None:
    """Demo numbers can never pass for real ones in a pushed message."""
    assert weekly_report(window(), demo_scenario="late").splitlines()[0] == (
        "[DEMO DATA · scenario late]"
    )


@pytest.mark.parametrize("scenario", available_scenarios())
def test_report_matches_snapshot(scenario: str, snapshot: SnapshotAssertion) -> None:
    """Each scenario's weekly report matches its reviewed snapshot."""
    model = FixtureReadModel.load(scenario)
    text = weekly_report(model.observation(), demo_scenario=model.demo_scenario)
    assert text.splitlines() == snapshot
