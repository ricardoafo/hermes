"""The fixture read model serves packaged scenarios and rejects broken ones.

Invariants:

- the six agreed scenarios ship with the package;
- every position's row net equals its detail net (net = gross - fees - tax);
- derived figures (change, stop distance) come from the prices, not the file;
- quiet activity (bar checks, signals) appears only on request;
- a naive timestamp, a float amount or an unknown name fails on load.
"""

from __future__ import annotations

import json
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from hermes.adapters.console import (
    FixtureReadModel,
    ScenarioError,
    UnknownScenarioError,
    available_scenarios,
)
from hermes.application.console import (
    ActivityKind,
    ConsoleReadModel,
    HeartbeatState,
    PortfolioRange,
    TaxPending,
)

_EXPECTED = (
    "down-stale",
    "empty",
    "late",
    "losing-year",
    "ok-two-positions",
    "tax-pending",
)


def _ok_document() -> dict[str, Any]:
    path = resources.files("hermes.adapters.console") / "scenarios" / "ok-two-positions.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def _write(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "custom.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_the_six_agreed_scenarios_ship_with_the_package() -> None:
    """The demo scenarios are package data, so --demo works from an installed wheel."""
    assert available_scenarios() == _EXPECTED


def test_fixture_model_satisfies_the_contract() -> None:
    """The fixture model is usable wherever a ConsoleReadModel is expected."""
    model: ConsoleReadModel = FixtureReadModel.load("ok-two-positions")
    assert model.demo_scenario == "ok-two-positions"


@pytest.mark.parametrize("scenario", _EXPECTED)
def test_row_net_equals_detail_net(scenario: str) -> None:
    """The list row and the detail agree on net for every position."""
    model = FixtureReadModel.load(scenario)
    mismatches = [
        row.coin
        for row in model.positions()
        if model.position(row.coin).net != row.net  # type: ignore[union-attr]
    ]
    assert mismatches == []


@pytest.mark.parametrize("scenario", _EXPECTED)
def test_every_range_is_served(scenario: str) -> None:
    """Each scenario answers for today, week, month and year."""
    model = FixtureReadModel.load(scenario)
    assert [model.portfolio(r).range for r in PortfolioRange] == list(PortfolioRange)


def test_row_net_is_gross_minus_fees_minus_tax() -> None:
    """ETH: 2.10 - 0.08 - 0.09 - 0.39 = 1.54."""
    model = FixtureReadModel.load("ok-two-positions")
    assert model.positions()[0].net == Decimal("1.54")


def test_stop_distance_is_derived_from_prices() -> None:
    """ETH stop distance is (2672 - 2580) / 30.1 ATR."""
    model = FixtureReadModel.load("ok-two-positions")
    assert model.positions()[0].stop_distance_atr == (Decimal(2672) - Decimal(2580)) / Decimal(
        "30.1"
    )


def test_change_since_entry_is_derived_from_prices() -> None:
    """SOL change is (146.5 - 139.0) / 139.0 in percent."""
    model = FixtureReadModel.load("ok-two-positions")
    sol = model.positions()[1]
    assert sol.change_pct == (Decimal("146.5") - Decimal("139.0")) * 100 / Decimal("139.0")


def test_position_lookup_ignores_case() -> None:
    """Asking for 'eth' finds the ETH position."""
    model = FixtureReadModel.load("ok-two-positions")
    assert model.position("eth").row.coin == "ETH"  # type: ignore[union-attr]


def test_unknown_coin_has_no_detail() -> None:
    """A coin with no open position returns None."""
    model = FixtureReadModel.load("ok-two-positions")
    assert {coin: model.position(coin) for coin in ("DOGE", "BTC")} == {"DOGE": None, "BTC": None}


def test_quiet_activity_is_hidden_by_default() -> None:
    """Bar checks and signals never appear in the default feed."""
    model = FixtureReadModel.load("ok-two-positions")
    kinds = {item.kind for day in model.activity() for item in day.items}
    assert kinds & {ActivityKind.BAR, ActivityKind.SIGNAL} == set()


def test_quiet_activity_appears_on_request() -> None:
    """With include_quiet the bar check and the signal are returned."""
    model = FixtureReadModel.load("ok-two-positions")
    kinds = {item.kind for day in model.activity(include_quiet=True) for item in day.items}
    assert kinds >= {ActivityKind.BAR, ActivityKind.SIGNAL}


def test_down_scenario_is_down_since_1804_madrid() -> None:
    """The down scenario stopped at 16:04 UTC (18:04 in Madrid)."""
    heartbeat = FixtureReadModel.load("down-stale").heartbeat()
    assert (heartbeat.state, heartbeat.down_since.isoformat()) == (  # type: ignore[union-attr]
        HeartbeatState.DOWN,
        "2026-10-03T16:04:00+00:00",
    )


def test_tax_pending_scenario_has_no_estimates() -> None:
    """Before the tax module, every tax figure in the scenario is pending."""
    summary = FixtureReadModel.load("tax-pending").portfolio()
    assert (summary.year.tax, summary.open_tax_if_sold, summary.tax_band) == (
        TaxPending(),
        TaxPending(),
        None,
    )


def test_losing_year_has_a_tax_credit() -> None:
    """A losing year carries a negative tax estimate (a credit)."""
    summary = FixtureReadModel.load("losing-year").portfolio()
    assert summary.year.net == Decimal("-39.53")


def test_unknown_scenario_lists_the_valid_names() -> None:
    """Asking for a missing scenario names every valid one."""
    with pytest.raises(UnknownScenarioError, match="ok-two-positions"):
        FixtureReadModel.load("nope")


def test_naive_timestamp_is_rejected(tmp_path: Path) -> None:
    """A timestamp without an offset fails on load (no naive datetimes)."""
    document = _ok_document()
    document["heartbeat"]["as_of"] = "2026-10-03T12:12:00"
    with pytest.raises(ScenarioError, match="no timezone offset"):
        FixtureReadModel.from_path(_write(tmp_path, document))


def test_float_amount_is_rejected(tmp_path: Path) -> None:
    """Amounts must be decimal strings so they parse exactly."""
    document = _ok_document()
    document["portfolio"]["cash"] = 790.36
    with pytest.raises(ScenarioError, match="decimal string"):
        FixtureReadModel.from_path(_write(tmp_path, document))


def test_missing_field_is_named(tmp_path: Path) -> None:
    """A missing field fails on load and names the field."""
    document = _ok_document()
    del document["shadow_test"]
    with pytest.raises(ScenarioError, match="shadow_test"):
        FixtureReadModel.from_path(_write(tmp_path, document))


def test_down_without_start_time_is_rejected(tmp_path: Path) -> None:
    """A down heartbeat without down_since fails on load."""
    document = _ok_document()
    document["heartbeat"]["state"] = "down"
    with pytest.raises(ScenarioError, match="down_since"):
        FixtureReadModel.from_path(_write(tmp_path, document))


def test_unknown_activity_kind_is_rejected(tmp_path: Path) -> None:
    """An activity kind outside the agreed list fails on load."""
    document = _ok_document()
    document["activity"][0]["items"][0]["kind"] = "heartbeat"
    with pytest.raises(ScenarioError, match="heartbeat"):
        FixtureReadModel.from_path(_write(tmp_path, document))


def test_non_object_document_is_rejected(tmp_path: Path) -> None:
    """A scenario must be a JSON object."""
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ScenarioError, match="JSON object"):
        FixtureReadModel.from_path(path)


def test_from_path_names_the_scenario_after_the_file(tmp_path: Path) -> None:
    """A scenario loaded from a file is tagged with the file name."""
    model = FixtureReadModel.from_path(_write(tmp_path, _ok_document()))
    assert model.demo_scenario == "custom"


def _set(document: dict[str, Any], path: tuple[str | int, ...], value: object) -> None:
    target: Any = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("heartbeat",), [], "must be an object"),
        (("positions",), {}, "must be a list"),
        (("positions", 0, "coin"), 7, "must be a string"),
        (("positions", 0, "detail", "stop_moves"), "3", "must be an integer"),
        (("portfolio", "cash"), "lots", "not a decimal"),
        (("portfolio", "cash"), "NaN", "must be finite"),
        (("heartbeat", "as_of"), "yesterday", "not an ISO timestamp"),
        (("heartbeat", "state"), "sleepy", "unknown value"),
        (("activity", 0, "day"), "03/10/2026", "not an ISO date"),
        (("positions", 0, "detail", "entry_values"), [["atr"]], "pairs"),
        (("positions", 0, "atr"), "0", "must be positive"),
        (("portfolio", "tax_band"), "19%", "object or null"),
        (("activity", 0, "items"), ["closed"], "must be an object"),
    ],
)
def test_malformed_field_fails_on_load(
    tmp_path: Path, path: tuple[str | int, ...], value: object, message: str
) -> None:
    """Each kind of malformed field fails on load with a message naming the problem."""
    document = _ok_document()
    _set(document, path, value)
    with pytest.raises(ScenarioError, match=message):
        FixtureReadModel.from_path(_write(tmp_path, document))


def test_invalid_json_fails_on_load(tmp_path: Path) -> None:
    """A file that is not JSON fails on load."""
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ScenarioError, match="not valid JSON"):
        FixtureReadModel.from_path(path)
