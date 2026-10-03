"""JSON shape of the positions screens.

Invariants:

- money sits under ``pnl`` and a blind surface carries no ``pnl`` and no
  current-price, stop-distance or P&L field anywhere in the JSON (the
  shadow-P&L ruling in #385);
- the detail ``pnl`` net equals gross - fees paid - fees to close - tax;
- a UTC context changes only the timestamps' offset.
"""

from __future__ import annotations

import json
from datetime import UTC
from decimal import Decimal

import pytest

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import OPERATOR_SURFACES, Surface
from hermes.application.console.render import RenderContext
from hermes.application.console.serialize import position_detail_to_json, positions_to_json

_BLIND = [s for s in Surface if s not in OPERATOR_SURFACES]
_BLIND_ROW_KEYS = {
    "coin",
    "timeframe",
    "side",
    "size",
    "opened_at",
    "entry_price",
    "stop_price",
}
_MONEY_WORDS = (
    "pnl",
    "net",
    "gross",
    "fees",
    "tax",
    "current_price",
    "change",
    "high_since_entry",
    "stop_distance",
)
_PNL_KEYS = [
    "change_pct",
    "current_price",
    "current_price_eur",
    "entry_price_eur",
    "eur_per_usdt",
    "high_since_entry",
    "high_since_entry_eur",
    "net_eur",
    "stop_distance_atr",
    "stop_distance_pct",
    "stop_price_eur",
]


def _ctx(model: FixtureReadModel, surface: Surface) -> RenderContext:
    return RenderContext.for_model(model, surface=surface)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _all_keys(v)}
    return set()


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
def test_blind_json_has_only_the_allowed_row_fields(surface: Surface) -> None:
    """A blind list row is exactly pair, side, opened at, entry, size and stop."""
    model = FixtureReadModel.load("ok-two-positions")
    data = json.loads(json.dumps(positions_to_json(model.positions(), _ctx(model, surface))))
    keys = {k for row in data["positions"] for k in row}
    assert keys == _BLIND_ROW_KEYS | {"timeframe"}


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
def test_blind_detail_json_has_no_money_key_at_any_depth(surface: Surface) -> None:
    """A blind detail leaks no P&L, current-price or stop-distance key."""
    model = FixtureReadModel.load("ok-two-positions")
    detail = model.position("ETH")
    assert detail is not None
    keys = _all_keys(json.loads(json.dumps(position_detail_to_json(detail, _ctx(model, surface)))))
    leaked = {k for k in keys if any(word in k for word in _MONEY_WORDS)}
    assert leaked == set()


def test_operator_json_nests_money_under_pnl() -> None:
    """On the operator CLI every row's money is inside its ``pnl`` object."""
    model = FixtureReadModel.load("ok-two-positions")
    data = positions_to_json(model.positions(), _ctx(model, Surface.OPERATOR_CLI))
    rows = data["positions"]
    assert isinstance(rows, list)
    assert [sorted(r["pnl"]) for r in rows] == [_PNL_KEYS, _PNL_KEYS]


def test_detail_json_net_is_gross_minus_fees_minus_tax() -> None:
    """The detail ``pnl`` reports net = gross - fees paid - fees to close - tax."""
    model = FixtureReadModel.load("ok-two-positions")
    detail = model.position("ETH")
    assert detail is not None
    pnl = position_detail_to_json(detail, _ctx(model, Surface.OPERATOR_CLI))["pnl"]
    assert isinstance(pnl, dict)
    expected = (
        Decimal(pnl["gross_eur"])
        - Decimal(pnl["fees_paid_eur"])
        - Decimal(pnl["fees_to_close_eur"])
        - Decimal(pnl["tax_eur"])
    )
    assert Decimal(pnl["net_eur"]) == expected


def test_pending_tax_is_null_and_flagged() -> None:
    """While the tax module is absent the tax is null with ``tax_pending`` true."""
    model = FixtureReadModel.load("tax-pending")
    detail = model.position(model.positions()[0].coin)
    assert detail is not None
    pnl = position_detail_to_json(detail, _ctx(model, Surface.OPERATOR_CLI))["pnl"]
    assert isinstance(pnl, dict)
    assert (pnl["tax_eur"], pnl["tax_pending"]) == (None, True)


def test_utc_context_moves_timestamps_to_utc() -> None:
    """A UTC context prints +00:00 offsets where Madrid prints +02:00."""
    model = FixtureReadModel.load("ok-two-positions")
    ctx = RenderContext.for_model(model, surface=Surface.OPERATOR_CLI, tz=UTC)
    rows = positions_to_json(model.positions(), ctx)["positions"]
    assert isinstance(rows, list)
    assert rows[0]["opened_at"] == "2026-10-02T08:00:00+00:00"


def test_empty_scenario_lists_no_positions() -> None:
    """With nothing open the list is empty, not missing."""
    model = FixtureReadModel.load("empty")
    data = positions_to_json(model.positions(), _ctx(model, Surface.OPERATOR_CLI))
    assert data["positions"] == []
