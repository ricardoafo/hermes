"""Builders of observation views for the panel and weekly report tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from hermes.application.console.views import (
    KillCriterion,
    KillState,
    KillTrigger,
    ObservationMoney,
    ObservationStats,
    ObservationWindow,
    ShadowTest,
    ShadowTestStatus,
)

AS_OF = datetime(2026, 10, 4, 18, 0, tzinfo=UTC)


def stats(*, drift: tuple[str, ...] = ("5.0", "-7.0", "9.0")) -> ObservationStats:
    """Return figures with three fills and the given drift samples (bps)."""
    return ObservationStats(
        fills=3,
        rejections=1,
        drift_bps=tuple(Decimal(v) for v in drift),
        slippage_bps=(Decimal("2.0"), Decimal("4.0"), Decimal("12.0")),
        decision_latency_ms=(Decimal(200), Decimal(250), Decimal(900)),
        data_gaps=0,
        late_bars=2,
        uptime_pct=Decimal("99.5"),
        restarts=1,
    )


def triggers(
    *, sharpe_days: int = 0, shock: KillState = KillState.ARMED
) -> tuple[KillTrigger, ...]:
    """Return the three §10 shadow-phase triggers."""
    return (
        KillTrigger(KillCriterion.DRIFT_SUSTAINED, KillState.ARMED, 2),
        KillTrigger(KillCriterion.SHOCK_OVERLAY, shock, 3 if shock is KillState.FIRED else 1),
        KillTrigger(KillCriterion.SHADOW_SHARPE, KillState.ARMED, sharpe_days),
    )


def window(
    *,
    money: ObservationMoney | None = None,
    sharpe_days: int = 0,
    drift: tuple[str, ...] = ("5.0", "-7.0", "9.0"),
    shock: KillState = KillState.ARMED,
) -> ObservationWindow:
    """Return a started window on day 13 of 90."""
    return ObservationWindow(
        as_of=AS_OF,
        test=ShadowTest(status=ShadowTestStatus.ON_TRACK, day=13),
        started_on=date(2026, 9, 22),
        last_7_days=stats(drift=drift),
        to_date=stats(drift=drift),
        kill_triggers=triggers(sharpe_days=sharpe_days, shock=shock),
        money=money,
    )


def not_started() -> ObservationWindow:
    """Return a window before day 1."""
    return replace(
        window(),
        test=ShadowTest(status=ShadowTestStatus.NOT_STARTED, day=None, note="waits on #353"),
        started_on=None,
        kill_triggers=(),
    )


# Distinctive P&L figures: if any of them reaches blind output, the tests see it.
LEAKY_MONEY = ObservationMoney(net=Decimal("-98765.43"), sharpe=Decimal("7.77"))
LEAKY_SHARPE_DAYS = 47
