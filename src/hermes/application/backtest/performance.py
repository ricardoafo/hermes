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

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from hermes.application.backtest.replay import BacktestRun
from hermes.domain.calibration.trial_record import TrialSummary
from hermes.domain.statistics.performance import TradeResult
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.pair import Pair

__all__ = ["performance_file", "round_trips"]

_ZERO = Decimal("0")


@dataclass(slots=True)
class _Open:
    """The part of a round trip not yet sold."""

    qty: Decimal = _ZERO
    cost: Decimal = _ZERO
    fees: Decimal = _ZERO
    gross: Decimal = _ZERO


def round_trips(fills: Iterable[Fill]) -> list[TradeResult]:
    """Return the closed round trips of ``fills``, in the order they closed.

    Args:
        fills: The run's fills, in order.

    Returns:
        One :class:`TradeResult` per round trip that ended flat.

    Raises:
        ValueError: A SELL fill sells more than is held in its pair.
    """
    held: dict[Pair, _Open] = {}
    out: list[TradeResult] = []
    for fill in fills:
        pair = fill.order.pair
        position = held.setdefault(pair, _Open())
        qty = fill.executed_qty
        if fill.order.side is OrderSide.BUY:
            position.qty += qty
            position.cost += qty * fill.avg_price
            position.fees += fill.fee.amount
            continue
        if qty > position.qty:
            raise ValueError(f"{pair}: sell of {qty} exceeds the {position.qty} held")
        sold_cost = position.cost * qty / position.qty
        position.gross += qty * fill.avg_price - sold_cost
        position.fees += fill.fee.amount
        position.cost -= sold_cost
        position.qty -= qty
        if position.qty == _ZERO:
            out.append(TradeResult(gross_pnl=float(position.gross), fees=float(position.fees)))
            del held[pair]
    return out


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
