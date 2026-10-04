"""``hermes money`` — the portfolio section on the command line (#498).

Usage: ``hermes [--demo SCENARIO] money [--range today|week|month|year] [--json] [--utc]``

Prints the console's portfolio section through the shared renderer: total
value in EUR with the change for the range, the Today / This year waterfall
(gross, fees, estimated tax, net) and the open-positions line, then the tax
footnote. Without a demo scenario the ledger is read, read-only (#546); with
no ledger, or a ledger with no portfolio mark or capital seed yet, the answer
is the empty state, not an error.

Exit codes: 0 success, 2 usage or unknown scenario, 3 unreadable ledger.

Reference: issue #498; console build plan step 5;
openspec/changes/live-home-screen/change.md (D-1 to D-3).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from datetime import UTC
from decimal import Decimal
from typing import Final, TextIO

from rich.console import Console, Group

from hermes.adapters.console import UnknownScenarioError
from hermes.application.console import (
    ConsoleReadModel,
    PortfolioRange,
    Surface,
    ViewNotAvailableError,
)
from hermes.application.console.render import (
    MADRID,
    RenderContext,
    render_portfolio,
    render_tax_footnote,
)
from hermes.application.console.views import PortfolioSummary, TaxEstimate, WaterfallColumn
from hermes.domain.errors import LedgerError
from hermes.entrypoints import console as console_module
from hermes.entrypoints._streams import resolve_err

__all__ = ["main", "portfolio_to_json"]

_USAGE: Final = (
    "Usage: hermes [--demo SCENARIO] money [--range today|week|month|year] [--json] [--utc]"
)
_NO_LIVE_MONEY: Final = "No live money yet: simulated fills start with #353."


def main(
    argv: list[str],
    *,
    stream: TextIO,
    demo_flag: str | None = None,
    env: Mapping[str, str] | None = None,
    err: TextIO | None = None,
) -> int:
    """Run ``hermes money``.

    Args:
        argv: Arguments after ``money``.
        stream: Output stream; colour follows Rich's TTY and ``NO_COLOR`` detection.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot for ``HERMES_CONSOLE_DEMO``; defaults to empty.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = argparse.ArgumentParser(prog="hermes money", add_help=False)
    parser.add_argument("--range", dest="range_", default=PortfolioRange.TODAY.value)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--utc", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
        range_ = PortfolioRange(parsed.range_.lower())
    except SystemExit, ValueError:
        err.write(f"ERROR: bad arguments. {_USAGE}\n")
        return 2
    if parsed.help:
        stream.write(_USAGE + "\n")
        return 0
    try:
        model = console_module.resolve_read_model(demo_flag=demo_flag, env=env or {})
    except console_module.LiveReadModelUnavailableError:
        return _empty(stream, _NO_LIVE_MONEY, as_json=parsed.json)
    except UnknownScenarioError as exc:
        err.write(f"ERROR: {exc}\n")
        return 2
    return _print(model, range_, stream, as_json=parsed.json, utc=parsed.utc)


def _empty(stream: TextIO, note: str, *, as_json: bool) -> int:
    """Print the no-money state and return exit code 0."""
    if as_json:
        stream.write(json.dumps({"money": None, "note": note}, indent=2) + "\n")
    else:
        stream.write(note + "\n")
    return 0


def _print(
    model: ConsoleReadModel, range_: PortfolioRange, stream: TextIO, *, as_json: bool, utc: bool
) -> int:
    """Print the portfolio; return 0, or 3 when the ledger cannot be read."""
    console = Console(file=stream, emoji=False, markup=False, highlight=False)
    try:
        ctx = RenderContext.for_model(
            model, surface=Surface.OPERATOR_CLI, tz=UTC if utc else MADRID, width=console.width
        )
        summary = model.portfolio(range_)
    except ViewNotAvailableError as exc:
        return _empty(stream, str(exc), as_json=as_json)
    except LedgerError as exc:
        stream.write(f"ERROR: could not read the ledger: {exc}\n")
        return 3
    if as_json:
        stream.write(json.dumps(portfolio_to_json(summary, ctx), indent=2) + "\n")
    else:
        console.print(Group(render_portfolio(summary, ctx), render_tax_footnote(summary)))
    return 0


def portfolio_to_json(summary: PortfolioSummary, ctx: RenderContext) -> dict[str, object]:
    """Return the portfolio section as JSON-ready data.

    Money sits under ``pnl``, which a blind surface leaves out entirely, so a
    consumer cannot render a figure that was never sent. A figure still EUR
    pending is replaced by ``"eur_pending": true``, never a guess.

    Args:
        summary: The portfolio view for the chosen range.
        ctx: Render context; its surface decides whether ``pnl`` is present.

    Returns:
        ``{"range", "as_of", "demo_scenario"}`` plus ``pnl`` on operator surfaces.
    """
    data: dict[str, object] = {
        "range": summary.range.value,
        "as_of": summary.as_of.astimezone(ctx.tz).isoformat(),
        "demo_scenario": ctx.demo_scenario,
    }
    if not ctx.shows_money:
        return data
    if summary.eur_pending:
        pending: dict[str, object] = {"eur_pending": True}
        if summary.total_value_quote is not None:
            pending["total_value_usdt"] = _amount(summary.total_value_quote)
        data["pnl"] = pending
        return data
    data["pnl"] = {
        "total_value_eur": _amount(summary.total_value),
        "change_eur": _amount(summary.change),
        "change_pct": _amount(summary.change_pct),
        "net_eur": _amount(summary.net),
        "cash_eur": _amount(summary.cash),
        "invested_eur": _amount(summary.invested),
        "today": _column(summary.today),
        "year": _column(summary.year),
        "open_net_eur": None if summary.open_pnl_pending else _amount(summary.open_pnl),
        "open_tax_if_sold_eur": _tax(summary.open_tax_if_sold),
    }
    return data


def _column(column: WaterfallColumn) -> dict[str, object]:
    if column.eur_pending:
        return {"eur_pending": True}
    return {
        "gross_eur": _amount(column.gross),
        "fees_eur": _amount(column.fees),
        "tax_eur": _tax(column.tax),
        "net_eur": _amount(column.net),
    }


def _tax(tax: object) -> str | None:
    """Return the estimate as a string, or ``None`` while tax is pending."""
    return _amount(tax.amount) if isinstance(tax, TaxEstimate) else None


def _amount(value: Decimal) -> str:
    return format(value, "f")
