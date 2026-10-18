"""The funnel replays of pre-registration v2 §8.5 and the ``hermes funnel`` evidence body.

Every backtest run of v2 also replays six neighbours, one parameter moved at a
time (Donchian 15 and 25, ADX gate 20 and 30, chandelier multiple 2.5 and
3.5), and the base parameters at twice the fees and slippage (§6.2). Each
replay is logged as its own trial. The evidence file holds the base run's
daily equity, the stress run's, and the daily returns of the base run and its
neighbours, the CSCV matrix of §8.5.

The replays override parameters of whatever strategy the driver loads and
scale whatever execution model it builds, so they do not depend on which
strategy runs.

Reference: openspec/changes/v2-funnel-evidence/change.md;
openspec/changes/v2-backtest-funnel/change.md Design §2.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any, Final

from hermes.application.backtest.funnel_replay import (
    BASE_TRIAL_ID,
    COST_STRESS,
    COST_STRESS_MULTIPLIER,
    FunnelReplay,
    neighbour,
)
from hermes.domain.statistics.performance import daily_returns

__all__ = [
    "COST_STRESS_MULTIPLIER",
    "FUNNEL_REPLAYS",
    "NEIGHBOUR_TRIAL_IDS",
    "FunnelReplay",
    "funnel_evidence",
]

_ONE_DAY: Final = timedelta(days=1)


FUNNEL_REPLAYS: Final[tuple[FunnelReplay, ...]] = (
    FunnelReplay(trial_id=BASE_TRIAL_ID),
    neighbour("donchian_period", 15),
    neighbour("donchian_period", 25),
    neighbour("adx_threshold", Decimal("20")),
    neighbour("adx_threshold", Decimal("30")),
    neighbour("chandelier_atr_mult", Decimal("2.5")),
    neighbour("chandelier_atr_mult", Decimal("3.5")),
    COST_STRESS,
)
"""The base run, the six §8.5 neighbours and the cost stress, in that order."""

NEIGHBOUR_TRIAL_IDS: Final[tuple[str, ...]] = tuple(
    r.trial_id for r in FUNNEL_REPLAYS if r.overrides
)


def _day(close_time: datetime) -> datetime:
    """Return the start of the UTC day a 1D bar closing at ``close_time`` covers."""
    covered = close_time - timedelta(microseconds=1)
    return covered.replace(hour=0, minute=0, second=0, microsecond=0)


def _equity(curve: Sequence[tuple[datetime, Decimal]]) -> list[float]:
    return [float(equity) for _, equity in curve]


def funnel_evidence(
    strategy_id: str,
    curves: Mapping[str, Sequence[tuple[datetime, Decimal]]],
    *,
    trade_count: int,
    replays: Sequence[FunnelReplay] = FUNNEL_REPLAYS,
) -> dict[str, Any]:
    """Return the ``hermes funnel`` evidence body (``v2-backtest-funnel`` Design §2).

    Args:
        strategy_id: The strategy the run was for.
        curves: Each replay's ``(close_time, equity)`` daily points, by trial id;
            every id of ``replays`` is required.
        trade_count: The base run's round trips.
        replays: The grid that ran: v2's by default, or a batch candidate's
            (ADR-026 §1).

    Returns:
        The JSON object, with ``running_strategies`` empty.

    Raises:
        ValueError: A replay is missing, the base curve is empty, a curve
            closes on other days than the base's, or the days are not
            consecutive.
    """
    missing = [r.trial_id for r in replays if r.trial_id not in curves]
    if missing:
        raise ValueError(f"no daily equity for replays {missing}")
    base = curves[BASE_TRIAL_ID]
    if not base:
        raise ValueError("the base run has no daily equity point")
    closes = [at for at, _ in base]
    for replay in replays:
        if [at for at, _ in curves[replay.trial_id]] != closes:
            raise ValueError(f"replay {replay.trial_id} closes on other days than the base run")
    days = [_day(at) for at in closes]
    for before, after in pairwise(days):
        if after - before != _ONE_DAY:
            raise ValueError(
                f"daily equity is not one point per consecutive day: {before.date()} then "
                f"{after.date()}"
            )
    neighbours = [r.trial_id for r in replays if r.overrides]
    trial_ids = (BASE_TRIAL_ID, *neighbours)
    return {
        "strategy_id": strategy_id,
        "start_date": days[0].date().isoformat(),
        "daily_equity": _equity(base),
        "trade_count": trade_count,
        "cost_stress": {
            "multiplier": float(COST_STRESS_MULTIPLIER),
            "daily_equity": _equity(curves[COST_STRESS.trial_id]),
        },
        "trials": {tid: daily_returns(_equity(curves[tid])) for tid in trial_ids},
        "neighbour_trial_ids": neighbours,
        "running_strategies": {},
    }
