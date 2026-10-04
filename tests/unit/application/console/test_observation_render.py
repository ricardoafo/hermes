"""The observation panel and its JSON.

Invariants:

- the panel shows day N of 90, the drift verdict against the 30 bps gate and
  the state of every §10 shadow-phase kill trigger;
- blind surfaces show no P&L: their output is identical whatever money the
  view carries and whatever the Sharpe trigger's day count is;
- the operator console shows shadow P&L (decision record 2026-10-03);
- the JSON carries P&L keys only where ``pnl_visible`` allows;
- snapshots pin the reviewed layout per scenario at 80 and 120 columns.
"""

from __future__ import annotations

import pytest
from syrupy.assertion import SnapshotAssertion

from hermes.adapters.console import FixtureReadModel, available_scenarios
from hermes.application.console import KillState, ObservationWindow, Surface
from hermes.application.console.observation_render import (
    kill_progress,
    observation_to_json,
    render_observation,
    slippage_buckets,
)
from hermes.application.console.render import RenderContext, to_plain_text
from hermes.application.console.views import KillCriterion, KillTrigger
from tests.unit.application.console._observation import (
    AS_OF,
    LEAKY_MONEY,
    LEAKY_SHARPE_DAYS,
    not_started,
    window,
)

_BLIND = tuple(
    s
    for s in Surface
    if s not in {Surface.OPERATOR_CLI, Surface.OPERATOR_TUI, Surface.TELEGRAM_PULLED}
)
_LEAK_STRINGS = ("98765", "98,765", "7.77", "47 of", "€", "P&L")


def _text(view: ObservationWindow, surface: Surface, width: int = 80) -> str:
    ctx = RenderContext(surface=surface, now=AS_OF, width=width)
    return to_plain_text(render_observation(view, ctx), width=width)


def _scenario_text(scenario: str, surface: Surface, width: int) -> str:
    model = FixtureReadModel.load(scenario)
    ctx = RenderContext.for_model(model, surface=surface, width=width)
    return to_plain_text(render_observation(model.observation(), ctx), width=width)


def test_panel_names_the_day_of_the_window() -> None:
    """The header reads day N of 90."""
    assert "day 13 of 90" in _text(window(), Surface.OPERATOR_CLI)


def test_panel_labels_failing_drift_as_fail() -> None:
    """p50 of |drift| at 31 bps reads as a failing gate."""
    text = _text(window(drift=("31", "-32", "30")), Surface.OPERATOR_CLI)
    assert "Fill drift fail · p50 31.0 bps" in text


def test_panel_shows_a_fired_trigger_as_fired() -> None:
    """A fired shock-overlay trigger is named fired, with its count."""
    lines = _text(window(shock=KillState.FIRED), Surface.OPERATOR_CLI).splitlines()
    assert [line.split()[0] for line in lines if "shock overlay" in line] == ["fired"]


def test_panel_before_day_one_says_not_started() -> None:
    """Before day 1 the panel says so and shows no figures."""
    assert _text(not_started(), Surface.OPERATOR_CLI).splitlines() == [
        "Observation window · not started · waits on #353",
        "Kill triggers arm on day 1.",
    ]


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
def test_blind_panel_does_not_depend_on_pnl(surface: Surface) -> None:
    """A blind panel is identical with and without money and Sharpe-day count."""
    leaky = window(money=LEAKY_MONEY, sharpe_days=LEAKY_SHARPE_DAYS)
    assert _text(leaky, surface) == _text(window(), surface)


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
def test_blind_panel_prints_no_pnl_figure(surface: Surface) -> None:
    """No money, Sharpe value or Sharpe-day count reaches a blind panel."""
    text = _text(window(money=LEAKY_MONEY, sharpe_days=LEAKY_SHARPE_DAYS), surface, width=120)
    assert [s for s in _LEAK_STRINGS if s in text] == []


def test_operator_panel_shows_shadow_pnl() -> None:
    """The operator sees net shadow P&L and Sharpe (decision record 2026-10-03)."""
    text = _text(window(money=LEAKY_MONEY), Surface.OPERATOR_CLI, width=120)
    assert "Shadow P&L since day 1 · ▼ −€98,765.43 net · Sharpe 7.77" in text  # noqa: RUF001


def test_sharpe_progress_is_hidden_on_blind_surfaces() -> None:
    """The Sharpe trigger's day count is P&L; blind surfaces get no count."""
    trigger = KillTrigger(KillCriterion.SHADOW_SHARPE, KillState.ARMED, 12)
    assert (
        kill_progress(trigger, shows_money=False),
        kill_progress(trigger, shows_money=True),
    ) == (None, "12 of 60 days")


def test_drift_progress_shows_on_blind_surfaces() -> None:
    """Drift days are execution quality, so every surface sees them."""
    trigger = KillTrigger(KillCriterion.DRIFT_SUSTAINED, KillState.ARMED, 4)
    assert kill_progress(trigger, shows_money=False) == "4 of 7 days"


def test_slippage_buckets_are_labelled_by_their_edges() -> None:
    """The histogram labels name the bps range each bucket covers."""
    assert [label for label, _ in slippage_buckets(())] == ["<0", "0–5", "5–10", "10–20", "≥20"]  # noqa: RUF001


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
def test_blind_json_has_no_pnl_keys(surface: Surface) -> None:
    """Blind JSON has no ``pnl`` key and no Sharpe-trigger count."""
    ctx = RenderContext(surface=surface, now=AS_OF)
    data = observation_to_json(window(money=LEAKY_MONEY, sharpe_days=47), ctx)
    triggers = data["kill_triggers"]
    assert isinstance(triggers, list)
    sharpe = next(t for t in triggers if t["criterion"] == "shadow_sharpe")
    assert ("pnl" in data, "count" in sharpe) == (False, False)


def test_operator_json_has_pnl() -> None:
    """Operator JSON carries net EUR and Sharpe as decimal strings."""
    ctx = RenderContext(surface=Surface.OPERATOR_CLI, now=AS_OF)
    data = observation_to_json(window(money=LEAKY_MONEY), ctx)
    assert data["pnl"] == {"net_eur": "-98765.43", "sharpe": "7.77"}


def test_json_carries_the_drift_verdict() -> None:
    """The JSON gives the same verdict as the panel."""
    ctx = RenderContext(surface=Surface.SLACK, now=AS_OF)
    data = observation_to_json(window(drift=("25", "26", "27")), ctx)
    assert data["drift"] == {
        "verdict": "at risk",
        "abs_bps": {"count": 3, "p50": "26", "p95": "26.9", "max": "27"},
    }


@pytest.mark.parametrize("width", [80, 120])
@pytest.mark.parametrize("scenario", available_scenarios())
def test_operator_panel_matches_snapshot(
    scenario: str, width: int, snapshot: SnapshotAssertion
) -> None:
    """The operator panel of each scenario matches its reviewed snapshot."""
    assert _scenario_text(scenario, Surface.OPERATOR_CLI, width).splitlines() == snapshot


@pytest.mark.parametrize("scenario", available_scenarios())
def test_blind_panel_matches_snapshot(scenario: str, snapshot: SnapshotAssertion) -> None:
    """The blind panel of each scenario matches its reviewed snapshot."""
    assert _scenario_text(scenario, Surface.TELEGRAM_PUSHED, 80).splitlines() == snapshot
