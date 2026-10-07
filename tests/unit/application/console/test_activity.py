"""Tests for the shared activity helpers (#374).

Invariants protected here:

- rows group by the display timezone's calendar day, newest day and row first;
- bar checks and signals are dropped unless asked for;
- the JSON form carries a closed row's net only where ``pnl_visible`` allows;
- query rows carry local time facets and no money (#729), including the
  restart, deploy, warning, error and folded check rows the journal adds (#653).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hermes.application.console import Surface
from hermes.application.console.activity import (
    ACTIVITY_QUERY_FIELDS,
    activity_query_rows,
    activity_to_json,
    group_activity,
)
from hermes.application.console.journal_activity import fold_checks
from hermes.application.console.render import MADRID, RenderContext
from hermes.application.console.views import ActivityDay, ActivityItem, ActivityKind

_NOW = datetime(2026, 10, 3, 16, tzinfo=UTC)


def _item(hour: int, kind: ActivityKind, *, day: int = 3, **kw: object) -> ActivityItem:
    return ActivityItem(at=datetime(2026, 10, day, hour, tzinfo=UTC), kind=kind, coin="ETH", **kw)  # type: ignore[arg-type]


def test_rows_group_by_local_day_newest_first() -> None:
    """23:00 UTC on the 2nd is already the 3rd in Madrid (CEST, UTC+2)."""
    items = [
        _item(8, ActivityKind.OPENED, day=2),
        _item(23, ActivityKind.CLOSED, day=2),
        _item(10, ActivityKind.WARNING, day=3, text="retry"),
    ]

    days = group_activity(items, MADRID)

    assert [(d.day, [i.kind for i in d.items]) for d in days] == [
        (date(2026, 10, 3), [ActivityKind.WARNING, ActivityKind.CLOSED]),
        (date(2026, 10, 2), [ActivityKind.OPENED]),
    ]


def test_quiet_rows_are_dropped_unless_asked_for() -> None:
    """Bar checks and signals never get a row by default; --all keeps them."""
    items = [
        _item(8, ActivityKind.BAR),
        _item(8, ActivityKind.SIGNAL),
        _item(8, ActivityKind.OPENED),
    ]

    default = group_activity(items, MADRID)
    everything = group_activity(items, MADRID, include_quiet=True)

    assert [i.kind for d in default for i in d.items] == [ActivityKind.OPENED]
    assert sorted(i.kind.value for d in everything for i in d.items) == ["bar", "opened", "signal"]


def test_a_day_with_only_quiet_rows_disappears() -> None:
    """A day of nothing but bar checks shows no header at all."""
    days = group_activity([_item(8, ActivityKind.BAR, day=1)], MADRID)

    assert days == ()


@pytest.mark.parametrize(
    ("surface", "has_net"),
    [(Surface.OPERATOR_CLI, True), (Surface.TELEGRAM_PUSHED, False), (Surface.SLACK, False)],
)
def test_json_carries_net_only_on_operator_surfaces(surface: Surface, has_net: bool) -> None:
    """Blind surfaces never receive the money of a closed row."""
    closed = _item(10, ActivityKind.CLOSED, price=Decimal("61240"), net=Decimal("3.18"))
    ctx = RenderContext(surface=surface, now=_NOW)

    data = activity_to_json([ActivityDay(day=date(2026, 10, 3), items=(closed,))], ctx)

    row = data["days"][0]["items"][0]  # type: ignore[index]
    assert ("net_eur" in row) is has_net
    assert row["price"] == "61240"


def test_query_rows_use_local_time_facets_newest_first() -> None:
    """23:00 UTC on 31 Dec is already the next year, month and day in Madrid."""
    late = ActivityItem(at=datetime(2025, 12, 31, 23, tzinfo=UTC), kind=ActivityKind.OPENED)
    early = _item(8, ActivityKind.CLOSED, net=Decimal("12.5"), price=Decimal(3000))

    rows = activity_query_rows([late, early], MADRID)

    assert rows == (
        {
            "at": early.at,
            "when": "2026-10-03 10:00",
            "year": 2026,
            "month": "2026-10",
            "day": "2026-10-03",
            "coin": "ETH",
            "kind": "closed",
            "text": None,
            "count": 1,
        },
        {
            "at": late.at,
            "when": "2026-01-01 00:00",
            "year": 2026,
            "month": "2026-01",
            "day": "2026-01-01",
            "coin": None,
            "kind": "opened",
            "text": None,
            "count": 1,
        },
    )


def test_query_fields_are_shared_facets_the_rows_carry() -> None:
    """Every activity query field is a row key, so no condition is silently empty."""
    row = activity_query_rows([_item(8, ActivityKind.OPENED)], MADRID)[0]
    assert (ACTIVITY_QUERY_FIELDS, set(ACTIVITY_QUERY_FIELDS) <= row.keys()) == (
        ("year", "month", "day", "coin", "kind"),
        True,
    )


def test_query_rows_cover_the_journal_row_kinds_and_never_carry_money() -> None:
    """Restart, deploy, warning, error and folded check rows are queryable rows.

    SEV-1 row S8 / ADR-012: no price, stop level or net may reach a row, so a
    query list is safe on every surface. The folded check line keeps its coin
    count; the other rows keep the text the journal gave them.
    """
    restart = ActivityItem(at=_at(8), kind=ActivityKind.RESTART, text="restarted · release 2bc2798")
    deploy = ActivityItem(
        at=_at(9), kind=ActivityKind.RESTART, text="deployed 9f3a1c0 (was 2bc2798)"
    )
    warning = ActivityItem(
        at=_at(10), kind=ActivityKind.WARNING, coin="BTC", text="exchange.slow (Timeout)"
    )
    error = ActivityItem(at=_at(11), kind=ActivityKind.ERROR, text="order.failed (ApiError)")
    bars = [
        ActivityItem(at=_at(12), kind=ActivityKind.BAR, coin=coin, text="4h check")
        for coin in ("BTC", "ETH", "SOL")
    ]
    stop = ActivityItem(
        at=_at(13),
        kind=ActivityKind.STOP_RAISED,
        coin="BTC",
        count=2,
        stop_from=Decimal(60000),
        stop_to=Decimal(61000),
        price=Decimal(61240),
        net=Decimal("3.18"),
    )
    items = [*fold_checks(bars), restart, deploy, warning, error, stop]

    rows = activity_query_rows(items, MADRID)

    assert [(r["kind"], r["coin"], r["text"], r["count"]) for r in rows] == [
        ("stop_raised", "BTC", None, 2),
        ("bar", None, "checked 3 coins · no signal", 3),
        ("error", None, "order.failed (ApiError)", 1),
        ("warning", "BTC", "exchange.slow (Timeout)", 1),
        ("restart", None, "deployed 9f3a1c0 (was 2bc2798)", 1),
        ("restart", None, "restarted · release 2bc2798", 1),
    ]
    assert all(
        not isinstance(value, Decimal) and key not in {"price", "net", "stop_from", "stop_to"}
        for row in rows
        for key, value in row.items()
    )


def _at(hour: int) -> datetime:
    return datetime(2026, 10, 3, hour, tzinfo=UTC)
