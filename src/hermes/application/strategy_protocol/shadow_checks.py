"""Daily shadow checks: the pre-registration §10 shadow kills and the drift rule (#796).

Serve runs these once per completed UTC day, over what its own ledger holds:

- **Shadow Sharpe** (§10): the 30-day rolling Sharpe below 0.3 for 60
  consecutive days, through :class:`ShadowSharpeKill` (decision of
  2026-10-03). One equity point per UTC day: the last portfolio mark of the day.
- **SA-9 repeat** (§10): any one SA-9 overlay rule (``RO-01`` to ``RO-08``)
  recorded as a HALT 3 or more times in the last 30 days.
- **Drift** (§10, §7): the median size of the fills' drift from the §6.2
  model price over the last 7 days above 30 bps. Direction is ignored;
  a window with no measured fill never breaches (Code Owner card, #796).
  Exits count only when the pre-registration models them at a market
  price: v1 models a stop exit at the stop, while serve sells at the bar
  close, so v1's exit gap is a known model choice, not execution drift.

A kill stops new entries for good (exits still fill) and makes the strategy
DEAD; a drift breach sends it back to UNTRUSTED (ADR-021). Each check
recomputes from the full history, so a restart cannot lose a streak.

Blind (ADR-012 D-06): a result says which rule fired, never a Sharpe, a
count or a drift value.

Reference: pre-registration-v1.md §7, §10; ADR-021; issue #796.
"""

import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from hermes.application.strategy_protocol.kill_trigger_evaluator import KillDecision
from hermes.application.strategy_protocol.preregistration import KillTriggers
from hermes.application.strategy_protocol.shadow_sharpe_kill import (
    ShadowSharpeKill,
    rolling_annualised_sharpe,
)
from hermes.domain.events.fill_quality_recorded import FillQualityRecorded
from hermes.domain.events.risk_halt_recorded import HaltKind, RiskHaltRecorded
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.statistics.performance import daily_returns

__all__ = [
    "SA9_REPEAT_RULE",
    "SHADOW_KILL_RULES",
    "ShadowCheckResult",
    "daily_equity",
    "day_end",
    "run_shadow_checks",
]

SA9_REPEAT_RULE: Final = "sa9_repeat"
"""``KillDecision.rule`` of the SA-9 repeat kill."""

_SHADOW_SHARPE_RULE: Final = "shadow_sharpe"

SHADOW_KILL_RULES: Final = frozenset({SA9_REPEAT_RULE, _SHADOW_SHARPE_RULE})
"""The kills the daily check can fire; a recorded one keeps entries stopped."""

_SA9_RULE_PREFIX: Final = "RO-"
_NOT_TRIGGERED: Final = KillDecision(triggered=False, reason="")


@dataclass(frozen=True, slots=True)
class ShadowCheckResult:
    """What one day's checks found, rule ids only.

    Args:
        kill: The first kill that fired, or an untriggered decision.
        drift_breached: The drift p50 was above its limit on this day.
        exits_left_out: Exit fills of the window the drift p50 did not count,
            because the pre-registration's exit model is not a market price.
    """

    kill: KillDecision
    drift_breached: bool
    exits_left_out: int = 0


def day_end(day: date) -> datetime:
    """Return the instant ``day`` ends, UTC (the next day's midnight)."""
    return datetime.combine(day + timedelta(days=1), time(), tzinfo=UTC)


def daily_equity(marks: Sequence[PortfolioMark]) -> tuple[tuple[date, float], ...]:
    """Return one equity point per UTC day: the day's last mark, oldest first.

    Args:
        marks: Portfolio marks in bar-close order.

    Returns:
        ``(day, equity)`` for each day that has a mark.
    """
    by_day: dict[date, float] = {}
    for mark in marks:
        by_day[mark.bar_close_time.astimezone(UTC).date()] = float(mark.snapshot.equity)
    return tuple(sorted(by_day.items()))


def run_shadow_checks(
    day: date,
    *,
    equity: Sequence[tuple[date, float]],
    halts: Sequence[RiskHaltRecorded],
    fills: Sequence[FillQualityRecorded],
    triggers: KillTriggers,
) -> ShadowCheckResult:
    """Run the §10 shadow kills and the drift rule for the completed UTC ``day``.

    Args:
        day: The UTC day just completed.
        equity: Daily equity points from :func:`daily_equity`.
        halts: The strategy's recorded halts; only overlay HALT rows count.
        fills: The strategy's fill-quality rows.
        triggers: The signed §10 thresholds.

    Returns:
        The kill that fired first (SA-9 repeat, then the Sharpe streak) and
        whether the drift breached.
    """
    end = day_end(day)
    kill = _sa9_repeat(end, halts, triggers)
    if not kill.triggered:
        kill = _shadow_sharpe(day, equity, triggers)
    breached, left_out = _drift(end, fills, triggers)
    return ShadowCheckResult(kill=kill, drift_breached=breached, exits_left_out=left_out)


def _sa9_repeat(
    end: datetime, halts: Sequence[RiskHaltRecorded], triggers: KillTriggers
) -> KillDecision:
    """Fire when one SA-9 overlay rule halted entries often enough inside the window."""
    start = end - timedelta(days=triggers.sa9_repeat_window_days)
    fires = Counter(
        h.rule
        for h in halts
        if h.kind is HaltKind.HALT
        and h.rule.startswith(_SA9_RULE_PREFIX)
        and start <= h.occurred_at < end
    )
    repeated = sorted(rule for rule, count in fires.items() if count >= triggers.sa9_repeat_fires)
    if not repeated:
        return _NOT_TRIGGERED
    return KillDecision(
        triggered=True,
        reason=(
            f"SA-9 rule {repeated[0]} fired {triggers.sa9_repeat_fires}+ times in "
            f"{triggers.sa9_repeat_window_days} days (pre-reg §10)."
        ),
        rule=SA9_REPEAT_RULE,
    )


def _shadow_sharpe(
    day: date, equity: Sequence[tuple[date, float]], triggers: KillTriggers
) -> KillDecision:
    """Replay the Sharpe streak over every day up to ``day``; fire if it ever reached the limit."""
    points = [(d, value) for d, value in equity if d <= day and value > 0.0]
    kill = ShadowSharpeKill(
        floor=triggers.rolling_sharpe_min,
        required_days=triggers.shadow_sharpe_consecutive_days,
    )
    returns = daily_returns([value for _, value in points])
    for index, (point_day, _) in enumerate(points):
        decision = kill.observe(point_day, rolling_annualised_sharpe(returns[:index]))
        if decision.triggered:
            return decision
    return _NOT_TRIGGERED


def _drift(
    end: datetime, fills: Sequence[FillQualityRecorded], triggers: KillTriggers
) -> tuple[bool, int]:
    """Return whether the window's median drift size is above the limit, and exits left out."""
    start = end - timedelta(days=triggers.shadow_drift_window_days)
    measured = [f for f in fills if f.drift_bps is not None and start <= f.occurred_at < end]
    counted = [f for f in measured if triggers.shadow_drift_counts_exits or f.side is TradeSide.BUY]
    sizes = [abs(f.drift_bps) for f in counted if f.drift_bps is not None]
    breached = bool(sizes) and statistics.median(sizes) > triggers.shadow_drift_limit_bps
    return breached, len(measured) - len(counted)
