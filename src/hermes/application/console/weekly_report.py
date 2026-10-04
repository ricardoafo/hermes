"""The weekly observation report: blind plain text for a pushed message.

The report is pushed to Slack or Telegram, where it becomes a durable record AI
sessions can read, so it is blind by construction: it takes no surface, never
reads :attr:`ObservationWindow.money` and never prints the Sharpe trigger's day
count. It reports execution quality and operations only.

Sending it (a Sunday 20:00 Europe/Madrid timer) is a follow-up; ``hermes
observe --weekly`` prints the same text.

Reference: openspec/changes/observation-dashboard/change.md (D-5, D-6);
decision record 2026-10-03 rule 4; #319; ADR-008 §4-§5.
"""

from __future__ import annotations

from datetime import tzinfo
from typing import Final

from hermes.application.console.observation import DRIFT_GATE_BPS
from hermes.application.console.observation_render import (
    KILL_RULE,
    fmt_bps,
    fmt_ms,
    kill_progress,
)
from hermes.application.console.render import DEMO_TAG, MADRID
from hermes.application.console.views import KillState, ObservationStats, ObservationWindow

__all__ = ["weekly_report"]

_TITLE: Final = "Hermes weekly"


def weekly_report(
    window: ObservationWindow, *, demo_scenario: str | None = None, tz: tzinfo = MADRID
) -> str:
    """Return the blind weekly report for ``window``.

    Args:
        window: The observation view; its ``money`` is never read.
        demo_scenario: Fixture scenario name; set means a DEMO DATA line heads
            the text.
        tz: Timezone of the week-ending date.

    Returns:
        Plain text, one line per row, ending with a newline.
    """
    lines: list[str] = []
    if demo_scenario is not None:
        lines.append(f"[{DEMO_TAG} · scenario {demo_scenario}]")
    lines.append(f"{_TITLE} · week ending {window.as_of.astimezone(tz):%a %d %b %Y}")
    test = window.test
    if not window.started:
        note = f" · {test.note}" if test.note else ""
        lines.append(f"Shadow observation · not started{note}")
        return "".join(f"{line}\n" for line in lines)
    lines += [
        f"Shadow observation · day {test.day} of {test.length_days} · {test.status.value}",
        _drift(window.to_date),
        *_week(window.last_7_days),
        _ops(window.last_7_days),
        *_kills(window),
    ]
    return "".join(f"{line}\n" for line in lines)


def _drift(stats: ObservationStats) -> str:
    drift = stats.drift
    figures = "" if drift is None else f" · p50 {fmt_bps(drift.p50)} · p95 {fmt_bps(drift.p95)}"
    return (
        f"Fill drift to date: {stats.drift_verdict.value}{figures}"
        f" (bps; gate p50 < {DRIFT_GATE_BPS})"
    )


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _week(stats: ObservationStats) -> list[str]:
    lines = [f"This week: {_count(stats.fills, 'fill')} · {_count(stats.rejections, 'rejection')}"]
    slippage = stats.slippage
    if slippage is not None:
        lines.append(
            f"Slippage this week: p50 {fmt_bps(slippage.p50)} · p95 {fmt_bps(slippage.p95)}"
            f" · max {fmt_bps(slippage.max)} bps"
        )
    latency = stats.decision_latency
    if latency is not None:
        lines.append(
            f"Decision latency this week: p50 {fmt_ms(latency.p50)} · p95 {fmt_ms(latency.p95)} ms"
        )
    return lines


def _ops(stats: ObservationStats) -> str:
    return (
        f"Operations this week: {_count(stats.data_gaps, 'data gap')}"
        f" · {_count(stats.late_bars, 'late bar')} · uptime {stats.uptime_pct:.1f}%"
        f" · {_count(stats.restarts, 'restart')}"
    )


def _kills(window: ObservationWindow) -> list[str]:
    fired = [t for t in window.kill_triggers if t.state is KillState.FIRED]
    if not fired:
        return [f"Kill triggers: all {len(window.kill_triggers)} armed, none fired"]
    lines = [f"Kill triggers: {len(fired)} FIRED"]
    for trigger in fired:
        progress = kill_progress(trigger, shows_money=False)
        suffix = f" ({progress})" if progress else ""
        lines.append(f"  FIRED · {KILL_RULE[trigger.criterion]}{suffix}")
    return lines
