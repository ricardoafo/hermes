"""Activity feed helpers shared by every read model and the ``--json`` output.

- :func:`group_activity` turns a flat list of :class:`ActivityItem` into the
  day-grouped, newest-first shape every surface renders, dropping bar checks
  and signals unless the caller asks for them.
- :func:`activity_to_json` gives the same rows as JSON-ready data. Money (the
  net of a closed row) sits under ``net_eur`` only on surfaces where
  :func:`~hermes.application.console.visibility.pnl_visible` allows it, so a
  blind consumer never receives a figure. Prices keep their USDT quote value
  under ``price`` and the EUR value the console shows under ``price_eur``.
- :func:`activity_query_rows` gives the rows a console query list filters and
  sorts (issue #729): local time facets, coin, kind and text, never money, so
  the same rows suit every surface.

Reference: openspec/archive/374-hermes-activity/change.md.
"""

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal
from itertools import groupby
from typing import Final

from hermes.application.console.render import RenderContext
from hermes.application.console.views import (
    QUIET_ACTIVITY_KINDS,
    ActivityDay,
    ActivityItem,
)

__all__ = [
    "ACTIVITY_QUERY_FIELDS",
    "activity_query_rows",
    "activity_to_json",
    "candle_boundary",
    "group_activity",
]

type JsonObject = dict[str, object]

_EUR_PRECISION = Decimal("0.0001")
_ONE_MS = timedelta(milliseconds=1)


def candle_boundary(at: datetime) -> datetime:
    """Return the candle boundary for Binance's inclusive close (boundary - 1 ms).

    A bar that closes at ``12:59:59.999`` belongs to the ``13:00:00`` boundary;
    any other time is returned unchanged. Ledger events and journal lines both
    pass through this so one 4h check lines up across the two sources.
    """
    bumped = at + _ONE_MS
    return bumped if bumped.second == 0 and bumped.microsecond == 0 else at


ACTIVITY_QUERY_FIELDS: Final = ("year", "month", "day", "coin", "kind")


def group_activity(
    items: Iterable[ActivityItem], tz: tzinfo, *, include_quiet: bool = False
) -> tuple[ActivityDay, ...]:
    """Group ``items`` by local calendar day, newest day and newest item first.

    Args:
        items: Activity rows in any order.
        tz: The timezone whose calendar days the feed is split by.
        include_quiet: Keep bar checks and signals (``hermes activity --all``).

    Returns:
        Days with at least one row, newest first.
    """
    kept = sorted(
        (i for i in items if include_quiet or i.kind not in QUIET_ACTIVITY_KINDS),
        key=lambda i: i.at,
        reverse=True,
    )

    def local_day(item: ActivityItem) -> date:
        return item.at.astimezone(tz).date()

    return tuple(
        ActivityDay(day=day, items=tuple(group)) for day, group in groupby(kept, key=local_day)
    )


def activity_to_json(days: Sequence[ActivityDay], ctx: RenderContext) -> JsonObject:
    """Return the feed as a JSON-ready dictionary.

    Args:
        days: Activity grouped by local day, newest day first.
        ctx: Render context; its surface decides whether ``net_eur`` is present
            and its timezone sets the day keys and timestamps.

    Returns:
        ``{"days": [{"day": ..., "items": [...]}], "demo_scenario": ..., "as_of": ...}``
        with amounts as decimal strings.
    """
    return {
        "days": [
            {"day": day.day.isoformat(), "items": [_item(item, ctx) for item in day.items]}
            for day in days
        ],
        "demo_scenario": ctx.demo_scenario,
        "as_of": ctx.now.astimezone(ctx.tz).isoformat(),
    }


def _eur(value: Decimal) -> Decimal:
    """Quantize an EUR figure to 4 places, as the positions JSON does."""
    return value.quantize(_EUR_PRECISION)


def _item(item: ActivityItem, ctx: RenderContext) -> JsonObject:
    data: JsonObject = {"at": item.at.astimezone(ctx.tz).isoformat(), "kind": item.kind.value}
    if item.coin is not None:
        data["coin"] = item.coin
    if item.text is not None:
        data["text"] = item.text
    if item.price is not None:
        data["price"] = format(item.price, "f")
        data["price_eur"] = format(_eur(item.price * item.eur_per_usdt), "f")
    if item.count != 1:
        data["count"] = item.count
    if item.stop_from is not None and item.stop_to is not None:
        data["stop_from"] = format(item.stop_from, "f")
        data["stop_to"] = format(item.stop_to, "f")
        data["stop_from_eur"] = format(_eur(item.stop_from * item.eur_per_usdt), "f")
        data["stop_to_eur"] = format(_eur(item.stop_to * item.eur_per_usdt), "f")
    if item.net is not None and ctx.shows_money:
        data["net_eur"] = format(item.net, "f")
    return data


def activity_query_rows(items: Iterable[ActivityItem], tz: tzinfo) -> tuple[dict[str, object], ...]:
    """Return activity as rows for a console query list, newest first.

    Args:
        items: Activity rows in any order (quiet kinds included if passed).
        tz: The timezone the time facets and the ``when`` column use.

    Returns:
        One mapping per item with ``at`` (for ``since:``), ``when``, ``year``,
        ``month``, ``day``, ``coin``, ``kind``, ``text`` and ``count`` (the coins
        a folded check line covers, else 1). Restarts, deploys, warnings, errors
        and folded check lines are rows like any other; no price, stop level or
        net is ever copied, so the rows suit every surface.
    """
    rows: list[dict[str, object]] = []
    for item in sorted(items, key=lambda i: i.at, reverse=True):
        local = item.at.astimezone(tz)
        rows.append(
            {
                "at": item.at,
                "when": f"{local:%Y-%m-%d %H:%M}",
                "year": local.year,
                "month": f"{local:%Y-%m}",
                "day": local.date().isoformat(),
                "coin": item.coin,
                "kind": item.kind.value,
                "text": item.text,
                "count": item.count,
            }
        )
    return tuple(rows)
