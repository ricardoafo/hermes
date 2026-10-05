"""A live portfolio on every surface: EUR first, EUR pending never guessed.

Reference: #546, change live-home-screen D-1 to D-3.

Summaries come from ledgers built through the real ledger API and read by the
live read model, so the surfaces are tested on the figures ``hermes serve``
actually produces.

Invariants:

- the hero is the EUR total with the date of the ECB rate it uses (#692),
  so a stale rate shows; the start line is the seed as funded in USDT
  with its EUR value at the ECB rate (#650), and no other USDT figure appears
  on the priced card;
- an EUR-pending portfolio shows the USDT total and "EUR pending", no € figure;
- a pending waterfall column or open line says "EUR pending", not €0.00;
- blind surfaces show no money, pending or not (``pnl_visible``);
- ``hermes money --json`` and the bot's ``/money`` carry the same pending state.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
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
    """The first line is the EUR total with its ECB rate date (#692), then today's change."""
    first = _text(_summary(tmp_path)).splitlines()[0]
    assert first == "€4,078.76 at ECB rate, 1 Oct   ▲ +€78.76 +1.97% today"


def test_start_line_shows_the_seed_and_its_ecb_value(tmp_path: Path) -> None:
    """The 5,000 USDT seed reads as funded, with its EUR value at the ECB rate (#650).

    USDT appears on the priced card only on that line.
    """
    lines = _text(_summary(tmp_path)).splitlines()
    assert [line for line in lines if "USDT" in line] == [
        "started 5,000 USDT (€4,000.00 at ECB rate, 1 Oct)"
    ]


def test_start_line_keeps_the_seed_currency_and_cents(tmp_path: Path) -> None:
    """A seed with cents in another quote currency reads exactly as funded (#650)."""
    summary = replace(
        _summary(tmp_path), quote_currency="USDC", starting_capital_quote=Decimal("5000.50")
    )
    started = next(line for line in _text(summary).splitlines() if line.startswith("started"))
    assert started == "started 5,000.50 USDC (€4,000.00 at ECB rate, 1 Oct)"


def test_an_old_rate_date_shows_on_the_total_and_the_start_line(tmp_path: Path) -> None:
    """A stale ECB rate is visible: both EUR figures name the date of the rate they use (#692)."""
    summary = replace(
        _summary(tmp_path),
        eur_rate_date=date(2026, 9, 18),
        starting_capital_rate_date=date(2026, 9, 17),
    )
    lines = _text(summary).splitlines()
    started = next(line for line in lines if line.startswith("started"))
    assert (lines[0], started) == (
        "€4,078.76 at ECB rate, 18 Sep   ▲ +€78.76 +1.97% today",
        "started 5,000 USDT (€4,000.00 at ECB rate, 17 Sep)",
    )


def test_big_total_keeps_the_rate_date_on_the_change_line(tmp_path: Path) -> None:
    """With the three-row total the rate date moves to the change line below it."""
    summary = replace(_summary(tmp_path), eur_rate_date=date(2026, 9, 18))
    ctx = RenderContext(surface=Surface.OPERATOR_TUI, now=_READ_AT, width=80)
    lines = to_plain_text(render_portfolio(summary, ctx, big_total=True), width=80).splitlines()
    assert lines[3] == "▲ +€78.76 +1.97% today at ECB rate, 18 Sep"


def test_ledger_summary_carries_the_rate_dates_it_used(tmp_path: Path) -> None:
    """The ledger model reports the newest rate's date and the seed's own rate date (#692)."""
    summary = _summary(tmp_path)
    assert (summary.eur_rate_date, summary.starting_capital_rate_date) == (
        date(2026, 10, 1),
        date(2026, 10, 1),
    )


def test_pending_card_shows_usdt_and_no_euro(tmp_path: Path) -> None:
    """No rate: the USDT total, "EUR pending", the restart/backfill hint, no € (A-1.2, #650)."""
    lines = _text(_summary(tmp_path, priced=False)).splitlines()
    assert lines == [
        "Portfolio 5,098.45 USDT · EUR pending",
        "EUR pending · priced on the next restart, or run hermes fx backfill",
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
