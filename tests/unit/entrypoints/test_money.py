"""Tests for ``hermes money`` (#498).

Invariants protected here:

- on every demo scenario the section matches its reviewed snapshot at 80 and
  120 columns and carries the DEMO DATA tag;
- ``--range`` picks the change line's period; a bad range is a usage error;
- ``--json`` carries money under ``pnl`` on the operator surface and leaves
  ``pnl`` out on a blind one;
- without a demo the empty state shows and the exit code is 0;
- ``hermes --demo SCENARIO money`` works through the top-level CLI.
"""

from __future__ import annotations

import io
import json

import pytest
from syrupy.assertion import SnapshotAssertion

from hermes.adapters.console import FixtureReadModel, available_scenarios
from hermes.application.console import PortfolioRange, Surface
from hermes.application.console.render import RenderContext
from hermes.entrypoints import cli
from hermes.entrypoints import money as money_module


def _run(args: list[str], *, demo: str | None = "ok-two-positions") -> tuple[int, str]:
    out = io.StringIO()
    code = money_module.main(args, stream=out, demo_flag=demo, env={})
    return code, out.getvalue()


def _lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines()]


@pytest.mark.parametrize("width", [80, 120])
@pytest.mark.parametrize("scenario", available_scenarios())
def test_section_matches_snapshot(
    scenario: str, width: int, monkeypatch: pytest.MonkeyPatch, snapshot: SnapshotAssertion
) -> None:
    """The reviewed portfolio section per scenario and width."""
    monkeypatch.setenv("COLUMNS", str(width))

    code, out = _run([], demo=scenario)

    assert (code, _lines(out)) == (0, snapshot)


def test_section_is_tagged_and_leads_with_the_total_for_the_range() -> None:
    """DEMO DATA first, then the EUR total with this week's change."""
    _, out = _run(["--range", "week"])
    lines = out.splitlines()

    assert "DEMO DATA" in lines[0]
    assert lines[1].startswith("€") and lines[1].rstrip().endswith("this week")


@pytest.mark.parametrize("bad", [["--range", "decade"], ["--nope"]])
def test_bad_arguments_exit_two(bad: list[str]) -> None:
    """An unknown range or flag is a usage error."""
    code, out = _run(bad)

    assert code == 2
    assert "Usage: hermes [--demo SCENARIO] money" in out


def test_json_carries_the_figures_under_pnl() -> None:
    """The operator gets the same figures the screen shows."""
    code, out = _run(["--json", "--range", "year"])
    data = json.loads(out)

    assert code == 0
    assert (data["range"], data["demo_scenario"]) == ("year", "ok-two-positions")
    assert sorted(data["pnl"]) == [
        "cash_eur",
        "change_eur",
        "change_pct",
        "invested_eur",
        "net_eur",
        "open_net_eur",
        "open_tax_if_sold_eur",
        "today",
        "total_value_eur",
        "year",
    ]


def test_blind_surfaces_get_no_pnl_key() -> None:
    """A blind consumer never receives a figure, not even an empty one."""
    model = FixtureReadModel.load("ok-two-positions")
    ctx = RenderContext.for_model(model, surface=Surface.TELEGRAM_PUSHED)

    data = money_module.portfolio_to_json(model.portfolio(PortfolioRange.TODAY), ctx)

    assert sorted(data) == ["as_of", "demo_scenario", "range"]


def test_tax_pending_shows_as_null_in_json() -> None:
    """Before the tax module lands, tax is null, not zero."""
    _, out = _run(["--json"], demo="tax-pending")
    today = json.loads(out)["pnl"]["today"]

    assert (today["tax_eur"], today["net_eur"] == today["gross_eur"]) == (None, False)


def test_without_a_demo_the_empty_state_shows() -> None:
    """No live money before fills: a sentence and exit 0, text and JSON."""
    text = _run([], demo=None)
    data = _run(["--json"], demo=None)

    assert text == (0, "No live money yet: simulated fills start with #353.\n")
    assert (data[0], json.loads(data[1])["money"]) == (0, None)


def test_demo_flag_reaches_money_through_the_cli() -> None:
    """`hermes --demo SCENARIO money` is a console command."""
    out = io.StringIO()

    code = cli.main(["--demo", "ok-two-positions", "money"], stream=out)

    assert code == 0
    assert "DEMO DATA" in out.getvalue()
