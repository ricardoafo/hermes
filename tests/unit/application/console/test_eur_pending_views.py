"""How the console shows a position whose fills have no EUR values yet (#546 D-4).

Invariants:

- an EUR-pending row shows no figure under a euro sign on any surface: prices
  are labelled USDT and the net cell says the EUR values are pending;
- its JSON carries ``eur_pending: true`` and no ``*_eur`` key or rate;
- a priced live row still follows ``pnl_visible``: blind surfaces get no money.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.application.console import OPERATOR_SURFACES, Surface
from hermes.application.console.position_figures import LotFacts, PositionFacts, build_detail
from hermes.application.console.render import (
    RenderContext,
    render_position_detail,
    render_positions,
    to_plain_text,
)
from hermes.application.console.serialize import position_detail_to_json, positions_to_json
from hermes.application.console.views import PositionDetail

_T0 = datetime(2026, 10, 1, 8, tzinfo=UTC)
_BLIND = [s for s in Surface if s not in OPERATOR_SURFACES]
_WIDTH = 120


def _detail(*, priced: bool) -> PositionDetail:
    lot = LotFacts(
        remaining_qty=Decimal("0.5"),
        original_qty=Decimal("0.5"),
        opened_at=_T0 + timedelta(seconds=5),
        price_eur=Decimal(40000) if priced else None,
        fee_eur=Decimal(20) if priced else None,
    )
    return build_detail(
        PositionFacts(
            strategy="swing-donchian-4h",
            coin="BTC",
            timeframe="4h",
            size=Decimal("0.5"),
            cost_native=Decimal(25000),
            current_price=Decimal(52000),
            stop_price=Decimal(48000),
            atr=Decimal(1000),
            signal_at=_T0,
            lots=(lot,),
            price_path=(Decimal(50000), Decimal(52000)),
            eur_per_usdt=Decimal("0.8"),
        )
    )


def _ctx(surface: Surface) -> RenderContext:
    return RenderContext(surface=surface, now=_T0 + timedelta(hours=9), width=_WIDTH)


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_pending_list_has_no_euro_figure() -> None:
    """The operator list shows USDT prices and 'EUR pending', never a euro amount."""
    text = to_plain_text(
        render_positions([_detail(priced=False).row], _ctx(Surface.OPERATOR_CLI)), width=_WIDTH
    )
    assert ("€" in text, "EUR pending" in text, "52,000 USDT" in text) == (False, True, True)


def test_pending_detail_has_no_euro_figure() -> None:
    """The detail replaces the money block with the backfill pointer and keeps USDT prices."""
    text = to_plain_text(
        render_position_detail(_detail(priced=False), _ctx(Surface.OPERATOR_CLI)), width=_WIDTH
    )
    assert ("€" in text, "hermes fx backfill" in text) == (False, True)


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
def test_pending_blind_list_labels_prices_usdt(surface: Surface) -> None:
    """Blind surfaces show the entry and stop as USDT levels while EUR is pending."""
    text = to_plain_text(render_positions([_detail(priced=False).row], _ctx(surface)), width=_WIDTH)
    assert ("€" in text, "50,000 USDT" in text, "48,000 USDT" in text) == (False, True, True)


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
def test_pending_blind_caption_does_not_claim_eur(surface: Surface) -> None:
    """The caption under USDT levels says so instead of the plain 'prices in EUR'."""
    text = to_plain_text(render_position_detail(_detail(priced=False), _ctx(surface)), width=_WIDTH)
    assert "USDT where EUR values are pending" in text


def test_pending_json_flags_pending_and_has_no_eur_keys() -> None:
    """The pnl object says eur_pending and carries no EUR key or rate."""
    data = position_detail_to_json(_detail(priced=False), _ctx(Surface.OPERATOR_CLI))
    pnl = data["pnl"]
    assert isinstance(pnl, dict)
    eur_keys = {k for k in _keys(data) if k.endswith("_eur") or k == "eur_per_usdt"}
    assert (pnl["eur_pending"], eur_keys) == (True, set())


def test_priced_list_json_has_no_pending_flag() -> None:
    """A priced row keeps today's pnl shape (no eur_pending key)."""
    data = json.loads(
        json.dumps(positions_to_json([_detail(priced=True).row], _ctx(Surface.OPERATOR_CLI)))
    )
    assert "eur_pending" not in data["positions"][0]["pnl"]


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
def test_priced_live_row_shows_no_money_on_blind_surfaces(surface: Surface) -> None:
    """Only pnl_visible decides: blind surfaces get neither the net nor a pnl object."""
    detail = _detail(priced=True)
    text = to_plain_text(render_position_detail(detail, _ctx(surface)), width=_WIDTH)
    data = position_detail_to_json(detail, _ctx(surface))
    assert ("net" in text, "pnl" in data) == (False, False)


def test_priced_live_row_shows_net_to_the_operator() -> None:
    """The operator CLI shows the live net (759.20 in the worked example)."""
    text = to_plain_text(
        render_positions([_detail(priced=True).row], _ctx(Surface.OPERATOR_CLI)), width=_WIDTH
    )
    assert "+€759.20 net" in text
