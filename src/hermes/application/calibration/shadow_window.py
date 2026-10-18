"""M-09 and M-10 over a whole shadow window, shadow against its replay (#965).

ADR-021 Done rule 3 compares the counted shadow with a backtest replay of the
same days. This module turns both sides into what
:mod:`~hermes.application.calibration.shadow_parity` judges:

- **Round trips**: from a pair's first BUY while flat to the SELL that brings
  it back to flat. The net P&L is sale proceeds minus the cost of what was
  sold minus every fee on the way, as the backtest's performance file counts
  a trade. A position still open at the end is not a round trip.
- **Matching**: a shadow round trip matches the first unmatched replay round
  trip of its pair entered at or less than one 4h bar before it (exactly
  one bar earlier does not match): serve fills inside the bar after the
  replay's fill at the bar close, the execution window of ``hermes parity
  check``. Unmatched round trips are left out.
- **Daily equity**: the shadow's is rebuilt from its recorded trades and the
  stored bars, marked bar by bar as serve marks them; the replay's is the
  run's own. Returns are taken over the 1D close times both curves have.

Only the verdicts leave this module; the values stay sealed (ADR-012 D-06).

Reference: openspec/changes/965-shadow-parity-window/change.md.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Final

from returns.result import Failure

from hermes.application.calibration.shadow_parity import (
    MetricVerdict,
    m09_pnl_correlation,
    m10_sharpe_match,
)
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import interval_duration, order_for_delivery
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.round_trips import RoundTripError, RoundTripFill
from hermes.domain.portfolio.round_trips import round_trips as closed_round_trips
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "BOOTSTRAP_SEED",
    "RoundTrip",
    "ShadowWindowError",
    "aligned_returns",
    "matched_pnl",
    "round_trips",
    "shadow_daily_equity",
    "window_verdicts",
]

BOOTSTRAP_SEED: Final = 965
"""Fixed M-09 bootstrap seed, so the same window always gets the same verdict."""

_MATCH_WINDOW: Final = interval_duration(KlineInterval.H4)
_NO_GAP: Final = timedelta(0)
_ZERO: Final = Decimal(0)


class ShadowWindowError(ValueError):
    """The recorded trades cannot be read as a shadow run."""


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """One closed round trip in one pair."""

    pair: Pair
    entered_at: datetime
    """Time of the BUY that opened it."""

    net_pnl: Decimal
    """Proceeds minus cost minus every fee, in the quote currency."""


def round_trips(trades: Iterable[TradeExecuted]) -> list[RoundTrip]:
    """Return the closed round trips of ``trades``, in the order they closed.

    Args:
        trades: Fills of one strategy, oldest first.

    Returns:
        One :class:`RoundTrip` per pair that went from flat back to flat.

    Raises:
        ShadowWindowError: A SELL sells more than its pair holds, or a fee is
            not in the pair's quote currency.
    """
    fills = (
        RoundTripFill(
            pair=trade.pair,
            side=OrderSide(trade.side.value),
            qty=trade.qty.amount,
            price=trade.price.amount,
            fee=trade.fee,
            at=trade.occurred_at,
        )
        for trade in trades
    )
    try:
        closed = closed_round_trips(fills)
    except RoundTripError as exc:
        raise ShadowWindowError(str(exc)) from exc
    return [RoundTrip(t.pair, t.entered_at, t.net_pnl) for t in closed]


def matched_pnl(
    shadow: Sequence[RoundTrip], replay: Sequence[RoundTrip]
) -> tuple[list[float], list[float]]:
    """Return the net P&L of each matched pair, shadow and replay, in shadow order.

    Args:
        shadow: The shadow's round trips.
        replay: The replay's round trips.

    Returns:
        Two equal-length lists, the i-th entries being one matched pair.
    """
    taken: set[int] = set()
    shadow_pnl: list[float] = []
    replay_pnl: list[float] = []
    for trip in sorted(shadow, key=lambda t: t.entered_at):
        match = next(
            (
                i
                for i, candidate in enumerate(replay)
                if i not in taken
                and candidate.pair == trip.pair
                and _NO_GAP <= trip.entered_at - candidate.entered_at < _MATCH_WINDOW
            ),
            None,
        )
        if match is None:
            continue
        taken.add(match)
        shadow_pnl.append(float(trip.net_pnl))
        replay_pnl.append(float(replay[match].net_pnl))
    return shadow_pnl, replay_pnl


def shadow_daily_equity(
    seed: PortfolioSnapshot,
    trades: Sequence[TradeExecuted],
    bars: Iterable[Bar],
    *,
    start: datetime,
) -> list[tuple[datetime, Decimal]]:
    """Rebuild the shadow's equity at each 1D close from its trades and the stored bars.

    Each bar marks the portfolio after every trade recorded before its close,
    as serve marks a bar before its strategy runs; the point at a 1D close is
    taken once the last pair's 1D bar has marked it, as the replay takes its own.

    Args:
        seed: The flat portfolio the shadow started from at day 0.
        trades: The shadow's fills since day 0, oldest first.
        bars: The stored bars of the window.
        start: Day 0; 1D closes at or before it are not points.

    Returns:
        ``(close_time, equity)`` per 1D close, oldest first.

    Raises:
        ShadowWindowError: A recorded trade cannot be applied to the portfolio.
    """
    pending = sorted(trades, key=lambda t: t.occurred_at)
    tracker = PortfolioTracker(seed)
    applied = 0
    points: list[tuple[datetime, Decimal]] = []
    for bar in order_for_delivery(bars):
        due = [t for t in pending[applied:] if t.occurred_at < bar.close_time]
        if due:
            rebuilt = PortfolioTracker.rebuilt(tracker.snapshot, due)
            if isinstance(rebuilt, Failure):
                raise ShadowWindowError(str(rebuilt.failure()))
            tracker = rebuilt.unwrap()
            applied += len(due)
        tracker.mark(bar)
        if bar.interval is KlineInterval.D1 and bar.close_time > start:
            point = (bar.close_time, tracker.snapshot.equity)
            if points and points[-1][0] == bar.close_time:
                points[-1] = point
            else:
                points.append(point)
    return points


def aligned_returns(
    shadow: Sequence[tuple[datetime, Decimal]], replay: Sequence[tuple[datetime, Decimal]]
) -> tuple[list[float], list[float]]:
    """Return both sides' daily returns over the 1D close times both curves have.

    Args:
        shadow: The shadow's ``(close_time, equity)`` points.
        replay: The replay's ``(close_time, equity)`` points.

    Returns:
        Two equal-length lists of simple daily returns, oldest first.
    """
    replay_at = dict(replay)
    common = [(at, equity, replay_at[at]) for at, equity in shadow if at in replay_at]
    shadow_returns: list[float] = []
    replay_returns: list[float] = []
    for (_, s_before, r_before), (_, s_after, r_after) in pairwise(common):
        if _ZERO in (s_before, r_before):
            continue
        shadow_returns.append(float(s_after / s_before - 1))
        replay_returns.append(float(r_after / r_before - 1))
    return shadow_returns, replay_returns


def window_verdicts(
    *,
    shadow_trades: Sequence[TradeExecuted],
    replay_trades: Sequence[TradeExecuted],
    shadow_equity: Sequence[tuple[datetime, Decimal]],
    replay_equity: Sequence[tuple[datetime, Decimal]],
    resamples: int | None = None,
) -> tuple[MetricVerdict, MetricVerdict]:
    """Return the M-09 and M-10 verdicts of a shadow window.

    Args:
        shadow_trades: The shadow's fills since day 0, oldest first.
        replay_trades: The replay's fills, oldest first.
        shadow_equity: The shadow's daily equity points.
        replay_equity: The replay's daily equity points.
        resamples: M-09 bootstrap resamples; the pre-registered count when ``None``.

    Returns:
        ``(m09, m10)``.

    Raises:
        ShadowWindowError: A SELL on either side sells more than its pair holds.
    """
    shadow_pnl, replay_pnl = matched_pnl(round_trips(shadow_trades), round_trips(replay_trades))
    m09 = (
        m09_pnl_correlation(shadow_pnl, replay_pnl, seed=BOOTSTRAP_SEED)
        if resamples is None
        else m09_pnl_correlation(shadow_pnl, replay_pnl, seed=BOOTSTRAP_SEED, resamples=resamples)
    )
    m10 = m10_sharpe_match(*aligned_returns(shadow_equity, replay_equity))
    return m09, m10
