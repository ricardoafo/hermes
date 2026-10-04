"""``hermes money`` — the portfolio section on the command line (#498).

Usage: ``hermes [--demo SCENARIO] money [--range today|week|month|year] [--json] [--utc]``

Prints the console's portfolio section through the shared renderer: total
value in EUR with the change for the range, the Today / This year waterfall
(gross, fees, estimated tax, net) and the open-positions line, then the tax
footnote. Without a demo scenario there is no live money yet (fills start
with #353), so the answer is the empty state, not an error.

Exit codes: 0 success, 2 usage or unknown scenario.

Reference: issue #498; console build plan step 5.
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
from hermes.application.console import PortfolioRange, Surface
from hermes.application.console.render import (
    MADRID,
    RenderContext,
    render_portfolio,
    render_tax_footnote,
)
from hermes.application.console.views import PortfolioSummary, TaxEstimate, WaterfallColumn
from hermes.entrypoints import console as console_module

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
) -> int:
    """Run ``hermes money``.

    Args:
        argv: Arguments after ``money``.
        stream: Output stream; colour follows Rich's TTY and ``NO_COLOR`` detection.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot for ``HERMES_CONSOLE_DEMO``; defaults to empty.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="hermes money", add_help=False)
    parser.add_argument("--range", dest="range_", default=PortfolioRange.TODAY.value)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--utc", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
        range_ = PortfolioRange(parsed.range_.lower())
    except SystemExit, ValueError:
        stream.write(f"ERROR: bad arguments. {_USAGE}\n")
        return 2
    if parsed.help:
        stream.write(_USAGE + "\n")
        return 0
    try:
        model = console_module.resolve_read_model(demo_flag=demo_flag, env=env or {})
    except console_module.LiveReadModelUnavailableError:
        if parsed.json:
            stream.write(json.dumps({"money": None, "note": _NO_LIVE_MONEY}, indent=2) + "\n")
        else:
            stream.write(_NO_LIVE_MONEY + "\n")
        return 0
    except UnknownScenarioError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 2
    console = Console(file=stream, emoji=False, markup=False, highlight=False)
    ctx = RenderContext.for_model(
        model,
        surface=Surface.OPERATOR_CLI,
        tz=UTC if parsed.utc else MADRID,
        width=console.width,
    )
    summary = model.portfolio(range_)
    if parsed.json:
        stream.write(json.dumps(portfolio_to_json(summary, ctx), indent=2) + "\n")
    else:
        console.print(Group(render_portfolio(summary, ctx), render_tax_footnote(summary)))
    return 0


def portfolio_to_json(summary: PortfolioSummary, ctx: RenderContext) -> dict[str, object]:
    """Return the portfolio section as JSON-ready data.

    Money sits under ``pnl``, which a blind surface leaves out entirely, so a
    consumer cannot render a figure that was never sent.

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
    data["pnl"] = {
        "total_value_eur": _amount(summary.total_value),
        "change_eur": _amount(summary.change),
        "change_pct": _amount(summary.change_pct),
        "net_eur": _amount(summary.net),
        "cash_eur": _amount(summary.cash),
        "invested_eur": _amount(summary.invested),
        "today": _column(summary.today),
        "year": _column(summary.year),
        "open_net_eur": _amount(summary.open_pnl),
        "open_tax_if_sold_eur": _tax(summary.open_tax_if_sold),
    }
    return data


def _column(column: WaterfallColumn) -> dict[str, object]:
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
