"""The ``hermes certify --performance`` file of one backtest run.

``hermes certify`` computes the §9 metrics from a performance file: one equity
point per UTC day, every closed trade's gross PnL and fees, the trial
statistics the Deflated Sharpe Ratio deflates by (§8.1), and the bootstrap
seed. This module builds that file from a :class:`BacktestRun`, so the
numbers certify judges are the run's own and nobody copies them by hand.

A trade is a round trip in one pair: from the first BUY fill on a flat pair
to the SELL fill that flattens it. Its gross PnL is sale proceeds minus the
cost of the quantity sold; its fees are every fee paid on the way, in the
quote currency. A position still open when the window ends is not a trade;
its value is in the equity curve.

Reference: pre-registration-v1.md §8.1, §9;
openspec/archive/822-portfolio-backtest/change.md D-4.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from hermes.application.backtest.replay import BacktestRun
from hermes.domain.calibration.trial_record import TrialSummary
from hermes.domain.portfolio.round_trips import ClosedRoundTrip, RoundTripFill
from hermes.domain.portfolio.round_trips import round_trips as closed_round_trips
from hermes.domain.statistics.performance import TradeResult
from hermes.domain.trading.fill import Fill

__all__ = ["performance_file", "round_trip_opened_at", "round_trips"]


def round_trips(fills: Iterable[Fill]) -> list[TradeResult]:
    """Return the closed round trips of ``fills``, in the order they closed.

    Args:
        fills: The run's fills, in order.

    Returns:
        One :class:`TradeResult` per round trip that ended flat.

    Raises:
        RoundTripError: A SELL fill sells more than is held in its pair, or a
            fee is not in the pair's quote currency.
    """
    return [TradeResult(gross_pnl=float(t.gross_pnl), fees=float(t.fees)) for t in _closed(fills)]


def round_trip_opened_at(fills: Iterable[Fill]) -> list[datetime]:
    """Return when each closed round trip of ``fills`` opened, in the order they closed.

    The ADR-029 §4 cap counts round trips per 90 days by these times; they
    come from the same loop as :func:`round_trips`, so both count the same trades.

    Raises:
        RoundTripError: As :func:`round_trips`.
    """
    return [t.entered_at for t in _closed(fills)]


def _closed(fills: Iterable[Fill]) -> list[ClosedRoundTrip]:
    return closed_round_trips(
        RoundTripFill(
            pair=fill.order.pair,
            side=fill.order.side,
            qty=fill.executed_qty,
            price=fill.avg_price,
            fee=fill.fee,
            at=fill.timestamp,
        )
        for fill in fills
    )


def performance_file(
    run: BacktestRun, *, trials: TrialSummary, bootstrap_seed: int
) -> dict[str, Any]:
    """Return the ``train_val`` performance file of ``run``.

    Args:
        run: The replay, its daily equity one point per UTC day.
        trials: The trial log summary for the strategy, this run included.
        bootstrap_seed: Seed of the §8.2 block bootstrap, for a reproducible
            interval.

    Returns:
        The JSON object ``hermes certify --performance`` reads.

    Raises:
        ValueError: The trial log has two or more trials but fewer than two
            Sharpe ratios, so the DSR has no variance to deflate by.
    """
    variance = trials.sharpe_variance
    if variance is None:
        if trials.count > 1:
            raise ValueError(
                f"{trials.count} trials but fewer than two report a Sharpe ratio; "
                "the Deflated Sharpe Ratio needs their variance"
            )
        # One trial deflates by nothing: its expected maximum Sharpe is 0 at any variance.
        variance = 0.0
    return {
        "window": "train_val",
        "daily_equity": [float(equity) for _, equity in run.daily_equity],
        "trades": [{"gross_pnl": t.gross_pnl, "fees": t.fees} for t in round_trips(run.fills)],
        "trials": {"count": trials.count, "sharpe_variance": variance},
        "bootstrap_seed": bootstrap_seed,
    }
