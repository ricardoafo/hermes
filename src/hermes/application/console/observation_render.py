"""The observation window panel and its JSON: ``hermes observe``.

One screen for the 90-day shadow observation window: day N of 90, the fill
drift against the §7 gate, execution and operations figures for the last seven
days and since day 1, the slippage histogram, and the §10 shadow-phase kill
triggers. Shadow P&L and the Sharpe trigger's day count are P&L, so they render
only where :func:`~hermes.application.console.visibility.pnl_visible` allows;
every other line is execution quality or operations and renders everywhere.

Reference: openspec/changes/observation-dashboard/change.md (D-4, D-5);
pre-registration v1 §7, §10; decision record 2026-10-03.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from itertools import pairwise
from typing import Final

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from hermes.application.console.format import MINUS, arrow, fmt_eur
from hermes.application.console.observation import (
    DRIFT_GATE_BPS,
    DRIFT_SUSTAINED_DAYS,
    SHADOW_SHARPE_DAYS,
    SHADOW_SHARPE_FLOOR,
    SHOCK_FIRES,
    SHOCK_WINDOW_DAYS,
    SLIPPAGE_EDGES_BPS,
    Distribution,
    DriftVerdict,
    histogram,
)
from hermes.application.console.render import RenderContext, framed
from hermes.application.console.views import (
    KillCriterion,
    KillState,
    KillTrigger,
    ObservationMoney,
    ObservationStats,
    ObservationWindow,
)

__all__ = [
    "KILL_RULE",
    "VERDICT_STYLE",
    "fmt_bps",
    "fmt_ms",
    "kill_progress",
    "observation_to_json",
    "render_observation",
    "slippage_buckets",
]

KILL_RULE: Final[dict[KillCriterion, str]] = {
    KillCriterion.DRIFT_SUSTAINED: (
        f"drift p50 > {DRIFT_GATE_BPS} bps for {DRIFT_SUSTAINED_DAYS} days"
    ),
    KillCriterion.SHOCK_OVERLAY: (
        f"shock overlay {SHOCK_FIRES}+ times in {SHOCK_WINDOW_DAYS} days"
    ),
    KillCriterion.SHADOW_SHARPE: f"Sharpe < {SHADOW_SHARPE_FLOOR} for {SHADOW_SHARPE_DAYS} days",
}
_KILL_LIMIT: Final[dict[KillCriterion, str]] = {
    KillCriterion.DRIFT_SUSTAINED: f"of {DRIFT_SUSTAINED_DAYS} days",
    KillCriterion.SHOCK_OVERLAY: f"of {SHOCK_FIRES}",
    KillCriterion.SHADOW_SHARPE: f"of {SHADOW_SHARPE_DAYS} days",
}
VERDICT_STYLE: Final[dict[DriftVerdict, str]] = {
    DriftVerdict.NO_FILLS: "dim",
    DriftVerdict.PASS: "green",
    DriftVerdict.AT_RISK: "yellow",
    DriftVerdict.FAIL: "bold red",
}
_KILL_STYLE: Final[dict[KillState, str]] = {KillState.ARMED: "green", KillState.FIRED: "bold red"}
_NONE: Final = "—"

type JsonObject = dict[str, object]
_TENTH: Final = Decimal("0.1")


def fmt_bps(value: Decimal) -> str:
    """Return basis points to one decimal, e.g. ``6.2``."""
    return f"{value.quantize(_TENTH)}"


def fmt_ms(value: Decimal) -> str:
    """Return milliseconds as a whole number, e.g. ``210``."""
    return f"{value:.0f}"


def kill_progress(trigger: KillTrigger, *, shows_money: bool) -> str | None:
    """Return ``"N of LIMIT"`` for ``trigger``, or ``None`` when it would reveal P&L.

    Args:
        trigger: The kill trigger.
        shows_money: Whether the surface may show P&L.

    Returns:
        The progress text, or ``None`` for the Sharpe criterion on a blind surface.
    """
    if trigger.criterion.reveals_pnl and not shows_money:
        return None
    return f"{trigger.count} {_KILL_LIMIT[trigger.criterion]}"


def slippage_buckets(samples: tuple[Decimal, ...]) -> tuple[tuple[str, int], ...]:
    """Return ``(label, count)`` per slippage bucket, lowest first.

    Args:
        samples: Signed slippage samples in bps.

    Returns:
        One pair per bucket of :data:`SLIPPAGE_EDGES_BPS`, e.g. ``("<0", 1)``.
    """
    edges = SLIPPAGE_EDGES_BPS
    labels = [
        f"<{edges[0]}",
        *(f"{low}–{high}" for low, high in pairwise(edges)),  # noqa: RUF001
        f"≥{edges[-1]}",
    ]
    return tuple(zip(labels, histogram(samples, edges), strict=True))


def _pair(dist: Distribution | None, fmt: Callable[[Decimal], str], unit: str) -> str:
    if dist is None:
        return _NONE
    return f"{fmt(dist.p50)} / {fmt(dist.p95)} {unit}"


def _maximum(dist: Distribution | None, fmt: Callable[[Decimal], str], unit: str) -> str:
    return _NONE if dist is None else f"{fmt(dist.max)} {unit}"


def _header(window: ObservationWindow, ctx: RenderContext) -> Text:
    test = window.test
    header = Text("Observation window", style="bold")
    if not window.started:
        header.append(" · not started")
        if test.note:
            header.append(f" · {test.note}", style="dim")
        return header
    header.append(f" · day {test.day} of {test.length_days} · {test.status.value}")
    if window.started_on is not None:
        header.append(f" · since {window.started_on:%a %d %b}", style="dim")
    if ctx.stale_since is not None:
        header.append(f" · as of {ctx.stale_since.astimezone(ctx.tz):%H:%M}", style="dim")
    return header


def _drift_line(stats: ObservationStats) -> Text:
    verdict = stats.drift_verdict
    line = Text("Fill drift ")
    line.append(verdict.value, style=VERDICT_STYLE[verdict])
    drift = stats.drift
    if drift is not None:
        line.append(f" · p50 {fmt_bps(drift.p50)} bps · p95 {fmt_bps(drift.p95)} bps")
    line.append(f" · gate p50 < {DRIFT_GATE_BPS} bps", style="dim")
    return line


def _figures(window: ObservationWindow) -> Table:
    table = Table(box=None, pad_edge=False, padding=(0, 2), show_edge=False)
    table.add_column("")
    table.add_column("last 7 days", justify="right")
    table.add_column("to date", justify="right")
    week, total = window.last_7_days, window.to_date
    rows: tuple[tuple[str, Callable[[ObservationStats], str]], ...] = (
        ("Fills", lambda s: str(s.fills)),
        ("Rejections", lambda s: str(s.rejections)),
        ("Drift p50 / p95", lambda s: _pair(s.drift, fmt_bps, "bps")),
        ("Slippage p50 / p95", lambda s: _pair(s.slippage, fmt_bps, "bps")),
        ("Slippage max", lambda s: _maximum(s.slippage, fmt_bps, "bps")),
        ("Decision latency p50 / p95", lambda s: _pair(s.decision_latency, fmt_ms, "ms")),
        ("Data gaps", lambda s: str(s.data_gaps)),
        ("Late bars", lambda s: str(s.late_bars)),
        ("Uptime", lambda s: f"{s.uptime_pct.quantize(_TENTH)}%"),
        ("Restarts", lambda s: str(s.restarts)),
    )
    for label, cell in rows:
        table.add_row(label, cell(week), cell(total))
    return table


def _histogram_line(stats: ObservationStats) -> Text:
    buckets = " · ".join(
        f"{label}: {count}" for label, count in slippage_buckets(stats.slippage_bps)
    )
    return Text(f"Slippage to date, bps · {buckets}", style="dim")


def _kill_table(window: ObservationWindow, ctx: RenderContext) -> Table:
    table = Table(box=None, pad_edge=False, padding=(0, 2), show_edge=False, show_header=False)
    table.add_column("")
    table.add_column("")
    table.add_column("", style="dim")
    for trigger in window.kill_triggers:
        table.add_row(
            Text(trigger.state.value, style=_KILL_STYLE[trigger.state]),
            KILL_RULE[trigger.criterion],
            kill_progress(trigger, shows_money=ctx.shows_money) or "",
        )
    return table


def _money_line(money: ObservationMoney, ctx: RenderContext) -> Text:
    style = "dim" if ctx.stale_since is not None else _direction_style(money.net)
    line = Text("Shadow P&L since day 1 · ")
    line.append(f"{fmt_eur(money.net)} net", style=style)
    if money.sharpe is not None:
        line.append(f" · Sharpe {money.sharpe:.2f}".replace("-", MINUS))
    return line


def _direction_style(value: Decimal) -> str:
    direction = arrow(value)
    if direction == "▲":
        return "green"
    if direction == "▼":
        return "red"
    return ""


def render_observation(window: ObservationWindow, ctx: RenderContext) -> Group:
    """Render the observation window panel.

    Args:
        window: The observation view.
        ctx: Render context; its surface decides whether P&L shows.

    Returns:
        The panel. Before day 1 it is the header and one note.
    """
    if not window.started:
        return framed(
            ctx,
            [_header(window, ctx), Text("Kill triggers arm on day 1.", style="dim")],
        )
    parts: list[RenderableType] = [
        _header(window, ctx),
        _drift_line(window.to_date),
        Text(""),
        _figures(window),
        Text(""),
        _histogram_line(window.to_date),
        Text(""),
        Text("Kill triggers · pre-registration §10", style="bold"),
        _kill_table(window, ctx),
    ]
    if ctx.shows_money and window.money is not None:
        parts += [Text(""), _money_line(window.money, ctx)]
    return framed(ctx, parts)


def _dist_json(dist: Distribution | None) -> JsonObject | None:
    if dist is None:
        return None
    return {
        "count": dist.count,
        "p50": format(dist.p50, "f"),
        "p95": format(dist.p95, "f"),
        "max": format(dist.max, "f"),
    }


def _stats_json(stats: ObservationStats) -> JsonObject:
    return {
        "fills": stats.fills,
        "rejections": stats.rejections,
        "drift_abs_bps": _dist_json(stats.drift),
        "drift_verdict": stats.drift_verdict.value,
        "slippage_bps": _dist_json(stats.slippage),
        "slippage_histogram_bps": dict(slippage_buckets(stats.slippage_bps)),
        "decision_latency_ms": _dist_json(stats.decision_latency),
        "data_gaps": stats.data_gaps,
        "late_bars": stats.late_bars,
        "uptime_pct": format(stats.uptime_pct, "f"),
        "restarts": stats.restarts,
    }


def _trigger_json(trigger: KillTrigger, *, shows_money: bool) -> JsonObject:
    data: JsonObject = {
        "criterion": trigger.criterion.value,
        "rule": KILL_RULE[trigger.criterion],
        "state": trigger.state.value,
    }
    if shows_money or not trigger.criterion.reveals_pnl:
        data["count"] = trigger.count
    return data


def observation_to_json(window: ObservationWindow, ctx: RenderContext) -> JsonObject:
    """Return the observation window as a JSON-ready dictionary.

    The ``pnl`` key and the Sharpe trigger's ``count`` are present only where
    the surface may show P&L, so a blind consumer never receives them.

    Args:
        window: The observation view.
        ctx: Render context; its surface gates the P&L keys and its timezone
            the timestamps.

    Returns:
        Day, gate, both periods, kill triggers and, on operator surfaces, P&L.
    """
    test = window.test
    data: JsonObject = {
        "as_of": window.as_of.astimezone(ctx.tz).isoformat(),
        "demo_scenario": ctx.demo_scenario,
        "started": window.started,
        "started_on": None if window.started_on is None else window.started_on.isoformat(),
        "day": test.day,
        "length_days": test.length_days,
        "status": test.status.value,
        "note": test.note,
        "drift_gate_p50_bps": format(DRIFT_GATE_BPS, "f"),
        "drift": {
            "verdict": window.to_date.drift_verdict.value,
            "abs_bps": _dist_json(window.to_date.drift),
        },
        "last_7_days": _stats_json(window.last_7_days),
        "to_date": _stats_json(window.to_date),
        "kill_triggers": [
            _trigger_json(t, shows_money=ctx.shows_money) for t in window.kill_triggers
        ],
    }
    if ctx.shows_money and window.money is not None:
        money = window.money
        data["pnl"] = {
            "net_eur": format(money.net, "f"),
            "sharpe": None if money.sharpe is None else format(money.sharpe, "f"),
        }
    return data
