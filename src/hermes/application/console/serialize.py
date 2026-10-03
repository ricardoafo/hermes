"""JSON-ready dictionaries for the positions screens.

The same fields the renderers show, for ``--json`` and any other surface that
wants data instead of text. Money sits under a ``pnl`` key that blind surfaces
omit entirely, so a consumer cannot render a figure that was never sent.

Prices keep their USDT quote values under the original keys; the EUR figures
sit beside them under ``pnl`` with the rate used (operator ruling 2026-10-03).

Reference: openspec/changes/console-positions/change.md (D-2).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from hermes.application.console.format import round2
from hermes.application.console.render import RenderContext
from hermes.application.console.views import (
    PositionDetail,
    PositionRow,
    TaxEstimate,
    TaxPending,
)

__all__ = ["position_detail_to_json", "positions_to_json"]

type JsonObject = dict[str, object]

_EUR_PRECISION = Decimal("0.0001")


def _amount(value: Decimal) -> str:
    return format(value, "f")


def _stamp(ctx: RenderContext, at: datetime) -> str:
    return at.astimezone(ctx.tz).isoformat()


def _row_core(row: PositionRow, ctx: RenderContext) -> JsonObject:
    return {
        "coin": row.coin,
        "timeframe": row.timeframe,
        "side": row.side,
        "size": _amount(row.size),
        "opened_at": _stamp(ctx, row.opened_at),
        "entry_price": _amount(row.entry_price),
        "stop_price": _amount(row.stop_price),
    }


def _eur(value: Decimal) -> str:
    return _amount(value.quantize(_EUR_PRECISION))


def _row_pnl(row: PositionRow) -> JsonObject:
    return {
        "eur_per_usdt": _amount(row.eur_per_usdt),
        "entry_price_eur": _eur(row.entry_eur),
        "current_price_eur": _eur(row.current_eur),
        "stop_price_eur": _eur(row.stop_eur),
        "high_since_entry_eur": _eur(row.high_eur),
        "current_price": _amount(row.current_price),
        "change_pct": _amount(round2(row.change_pct)),
        "net_eur": _amount(row.net),
        "high_since_entry": _amount(row.high_since_entry),
        "stop_distance_atr": _amount(round2(row.stop_distance_atr)),
        "stop_distance_pct": _amount(round2(row.stop_distance_pct)),
    }


def _meta(ctx: RenderContext) -> JsonObject:
    return {"demo_scenario": ctx.demo_scenario, "as_of": _stamp(ctx, ctx.now)}


def positions_to_json(rows: Sequence[PositionRow], ctx: RenderContext) -> JsonObject:
    """Return the positions list as a JSON-ready dictionary.

    Args:
        rows: Open positions.
        ctx: Render context; its surface decides whether ``pnl`` is present.

    Returns:
        ``{"positions": [...], "demo_scenario": ..., "as_of": ...}`` with
        amounts as decimal strings and times in the context timezone.
    """
    items: list[JsonObject] = []
    for row in rows:
        item = _row_core(row, ctx)
        if ctx.shows_money:
            item["pnl"] = _row_pnl(row)
        items.append(item)
    return {"positions": items, **_meta(ctx)}


def position_detail_to_json(detail: PositionDetail, ctx: RenderContext) -> JsonObject:
    """Return one position's detail as a JSON-ready dictionary.

    Blind surfaces get the same fields as a blind list row and nothing else.

    Args:
        detail: The position.
        ctx: Render context; its surface decides whether ``pnl`` is present.

    Returns:
        The row fields plus, on operator surfaces, strategy, entry values, the
        timeline and a ``pnl`` object with gross, fees, tax and net.
    """
    data = _row_core(detail.row, ctx)
    data.update(_meta(ctx))
    if not ctx.shows_money:
        return data
    tax = detail.tax
    data["strategy"] = detail.strategy
    data["entry_values"] = {name: _amount(value) for name, value in detail.entry_values}
    data["stop_moves"] = detail.stop_moves
    data["timeline"] = [
        {"at": _stamp(ctx, event.at), "label": event.label} for event in detail.timeline
    ]
    pnl = _row_pnl(detail.row)
    pnl.update(
        {
            "gross_eur": _amount(detail.gross),
            "fees_paid_eur": _amount(detail.fees_paid),
            "fees_to_close_eur": _amount(detail.fees_to_close),
            "tax_eur": _amount(tax.amount) if isinstance(tax, TaxEstimate) else None,
            "tax_pending": isinstance(tax, TaxPending),
            "net_eur": _amount(detail.net),
        }
    )
    data["pnl"] = pnl
    return data
