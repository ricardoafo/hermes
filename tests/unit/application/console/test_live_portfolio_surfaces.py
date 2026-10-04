"""A live portfolio on every surface: EUR first, EUR pending never guessed.

Reference: #546, change live-home-screen D-1 to D-3.

Summaries come from ledgers built through the real ledger API and read by the
live read model, so the surfaces are tested on the figures ``hermes serve``
actually produces.

Invariants:

- the hero is the EUR total; the start line is the seed's EUR equivalent,
  labelled "Equivalent in EUR", and no USDT appears on the priced card;
- an EUR-pending portfolio shows the USDT total and "EUR pending", no € figure;
- a pending waterfall column or open line says "EUR pending", not €0.00;
- blind surfaces show no money, pending or not (``pnl_visible``);
- ``hermes money --json`` and the bot's ``/money`` carry the same pending state.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.application.bot.replies import Replies, ReplySources
from hermes.application.console import PortfolioRange, Surface
from hermes.application.console.render import RenderContext, render_portfolio, to_plain_text
from hermes.application.console.views import PortfolioSummary, TaxPending, WaterfallColumn
from hermes.application.status import StatusReport
from hermes.entrypoints.money import portfolio_to_json
from tests.unit.adapters.console._ledger_builder import T0, home_ledger

_READ_AT = T0 + timedelta(hours=9)


def _model(tmp_path: Path, *, priced: bool) -> LedgerReadModel:
    path = tmp_path / "hermes.sqlite3"
    home_ledger(path, priced=priced)
    return LedgerReadModel.open(path, clock=lambda: _READ_AT)


def _summary(tmp_path: Path, *, priced: bool = True) -> PortfolioSummary:
    return _model(tmp_path, priced=priced).portfolio(PortfolioRange.TODAY)


def _text(summary: PortfolioSummary, surface: Surface = Surface.OPERATOR_CLI) -> str:
    ctx = RenderContext(surface=surface, now=_READ_AT, width=80)
    return to_plain_text(render_portfolio(summary, ctx), width=80)


def test_priced_card_leads_with_the_eur_total(tmp_path: Path) -> None:
    """The first line is the EUR total and today's change."""
    first = _text(_summary(tmp_path)).splitlines()[0]
    assert first == "€4,078.76   ▲ +€78.76 +1.97% today"


def test_start_line_is_labelled_equivalent_in_eur(tmp_path: Path) -> None:
    """The 5,000 USDT seed reads as its EUR value with the label, and no USDT is shown."""
    text = _text(_summary(tmp_path))
    assert ("started €4,000.00 on 1 Oct · Equivalent in EUR" in text, "USDT" in text) == (
        True,
        False,
    )


def test_pending_card_shows_usdt_and_no_euro(tmp_path: Path) -> None:
    """No rate: the USDT total, "EUR pending", the backfill hint, and no € anywhere (A-1.2)."""
    lines = _text(_summary(tmp_path, priced=False)).splitlines()
    assert lines == [
        "Portfolio 5,098.45 USDT · EUR pending",
        "EUR values pending: no EUR rate in the ledger yet · run hermes fx backfill",
        "started 5,000.00 USDT on 1 Oct · EUR pending",
    ]


def test_pending_column_and_open_line_say_so(tmp_path: Path) -> None:
    """A pending waterfall column and open line read "EUR pending" instead of €0.00."""
    pending = WaterfallColumn(gross=Decimal(0), fees=Decimal(0), tax=TaxPending(), eur_pending=True)
    summary = replace(_summary(tmp_path), today=pending, open_pnl_pending=True)
    lines = _text(summary).splitlines()
    gross = next(line for line in lines if line.startswith("Gross"))
    open_line = next(line for line in lines if line.startswith("Open"))
    assert (gross.split()[1:3], open_line.startswith("Open EUR pending")) == (
        ["EUR", "pending"],
        True,
    )


@pytest.mark.parametrize("priced", [True, False])
@pytest.mark.parametrize("surface", [Surface.SLACK, Surface.REPORT, Surface.MCP_TOOL])
def test_blind_surfaces_show_no_money(tmp_path: Path, priced: bool, surface: Surface) -> None:
    """Off the operator's surfaces neither € nor USDT figures appear (R-4)."""
    text = _text(_summary(tmp_path, priced=priced), surface)
    assert text.strip() == "Portfolio · money hidden on this surface"


def test_money_json_of_a_pending_portfolio(tmp_path: Path) -> None:
    """The JSON says eur_pending and gives the USDT total, with no _eur keys (A-1.2)."""
    ctx = RenderContext(surface=Surface.OPERATOR_CLI, now=_READ_AT)
    data = portfolio_to_json(_summary(tmp_path, priced=False), ctx)
    assert data["pnl"] == {"eur_pending": True, "total_value_usdt": "5098.45"}


def test_money_json_of_a_priced_portfolio(tmp_path: Path) -> None:
    """The priced JSON carries the EUR total and the sale's net for today."""
    ctx = RenderContext(surface=Surface.OPERATOR_CLI, now=_READ_AT)
    pnl = portfolio_to_json(_summary(tmp_path), ctx)["pnl"]
    assert isinstance(pnl, dict)
    assert (pnl["total_value_eur"], pnl["today"]["net_eur"], pnl["open_net_eur"]) == (
        "4078.760",
        "39.16000000000000000000000000",
        "39.160",
    )


def _replies(model: LedgerReadModel) -> Replies:
    def no_status() -> StatusReport:
        raise AssertionError

    sources = ReplySources(
        status=no_status,
        read_model=lambda: model,
        activity=lambda since: [],
        warnings=list,
    )
    return Replies(sources=sources, surface=Surface.TELEGRAM_PULLED)


def test_bot_money_on_a_pending_ledger_says_eur_pending(tmp_path: Path) -> None:
    """``/money`` never prints €0.00 for a portfolio with no EUR rate."""
    reply = _replies(_model(tmp_path, priced=False)).reply("money", [], _READ_AT)
    assert reply == "Total: EUR pending (no EUR rate in the ledger yet)."


def test_bot_money_on_a_priced_ledger_shows_the_total(tmp_path: Path) -> None:
    """``/money`` on the live ledger leads with the EUR total."""
    reply = _replies(_model(tmp_path, priced=True)).reply("money", [], _READ_AT)
    assert reply.splitlines()[0] == "Total €4,078.76 (▲ +€78.76 +1.97% today)"
