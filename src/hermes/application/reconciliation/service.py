"""Monthly reconciliation use case — build the report from the ledger and the account.

Three views are compared with the trade book (the trades table plus the
configured starting capital):

- ``events``: the ``TradeExecuted`` replay the shadow loop rebuilds its
  portfolio from (the account a shadow run trades). Always compared.
- ``lots``: the remaining quantity of the lots table, the state the tax lots
  are read from. Compared only when no fill came after the period end, since
  the table holds current state.
- ``exchange``: a real account's balances, when one is supplied. Compared only
  for the current month, since a venue reports balances now, not at a past
  month end.

Read-only throughout; the result is a report, never a correction.

Reference: openspec/changes/35-monthly-reconciliation/change.md D-1, D-2, D-5.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Final

from returns.result import Failure

from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.reconciliation.holdings import (
    HoldingCheck,
    book_balances,
    compare_holdings,
)
from hermes.domain.reconciliation.month import ReconciliationMonth
from hermes.domain.reconciliation.report import ReconciliationReport, Verdict
from hermes.domain.reconciliation.tax_view import tax_view
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.precision import EXACT_CONTEXT
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.reconciliation import BalanceReadPort, ReconciliationReadPort

__all__ = ["StrategySeed", "reconcile_month", "reconciliation_alert"]

_ZERO: Final = Decimal("0")
_ALERT_SOURCE: Final = "reconciler"
_ALERT_DETAIL_LIMIT: Final = 3


@dataclass(frozen=True, slots=True)
class StrategySeed:
    """A strategy's starting capital, as the roster configures it.

    Attributes:
        strategy_id: The strategy bucket.
        capital: Starting cash; its currency is the strategy's quote.
    """

    strategy_id: StrategyId
    capital: Money


def reconcile_month(
    reader: ReconciliationReadPort,
    *,
    month: ReconciliationMonth,
    seeds: Sequence[StrategySeed],
    now: datetime,
    tolerance: Decimal,
    exchange: BalanceReadPort | None = None,
) -> ReconciliationReport:
    """Reconcile one month.

    Args:
        reader: The ledger, read-only.
        month: The month to cover. A past month is read as it stood at its
            end; the current month as it stands at ``now``.
        seeds: Starting capital of every roster strategy.
        now: The run time, UTC-aware.
        tolerance: Largest absolute per-asset difference still counted as a
            match.
        exchange: A real account to compare, or ``None`` (shadow mode).

    Returns:
        The report.

    Raises:
        ValueError: ``month`` starts after ``now``, or the tolerance is negative.
        LedgerError: The ledger cannot be read.
        InfraError: The exchange cannot be read.
    """
    if month.start > now:
        raise ValueError(f"{month} has not started yet")
    end = month.end
    month_closed = now >= end
    as_of = end if month_closed else now

    all_trades = reader.trades()
    scoped = [t for t in all_trades if t.executed_at < end]
    later = len(all_trades) - len(scoped)
    book = book_balances(scoped, _starting_capital(seeds))

    checks: list[HoldingCheck] = []
    problems: list[str] = []
    skipped: list[str] = []

    observed, replay_problems = _event_view(reader.trade_events(), seeds, end)
    problems.extend(replay_problems)
    if observed is not None:
        checks.extend(compare_holdings(book, observed, source="events", tolerance=tolerance))

    base_assets = {t.pair.base for t in scoped}
    if later:
        skipped.append(f"lots: {later} trade(s) after {month} change the lot table")
    else:
        lots = reader.open_lot_quantities()
        checks.extend(
            compare_holdings(
                book,
                lots,
                source="lots",
                tolerance=tolerance,
                assets=base_assets | set(lots),
            )
        )

    if month_closed:
        skipped.append(f"exchange: balances are current, {month} has ended")
    elif exchange is None:
        skipped.append("exchange: no account connected (shadow mode)")
    else:
        balances = exchange.balances(sorted(book, key=lambda a: a.value))
        checks.extend(compare_holdings(book, balances, source="exchange", tolerance=tolerance))

    replayed = tax_view(all_trades, start=month.start, end=end)
    tax = None
    if isinstance(replayed, Failure):
        problems.append(f"book: FIFO replay failed: {replayed.failure()}")
    else:
        tax = replayed.unwrap()

    return ReconciliationReport(
        month=month,
        as_of=as_of,
        checks=tuple(checks),
        tax=tax,
        problems=tuple(problems),
        skipped=tuple(skipped),
    )


def reconciliation_alert(report: ReconciliationReport, *, now: datetime) -> AnomalyDetected | None:
    """Return the alert a non-clean report raises, or ``None`` for a clean one.

    Drift is a WARNING (a human resolves it before the month-end cut); pending
    EUR values are INFO (the backfill clears them). The alert is pushed, so it
    names views and assets but carries no amount: pushed surfaces stay blind to
    shadow money (``hermes.application.console.visibility``); the operator pulls
    the figures with ``hermes reconcile``.

    Args:
        report: The finished report.
        now: When the alert is raised, UTC-aware.

    Returns:
        An :class:`AnomalyDetected` from source ``reconciler``, or ``None``.
    """
    verdict = report.verdict
    if verdict is Verdict.CLEAN:
        return None
    if verdict is Verdict.REVIEW:
        pending = report.tax.pending_trades if report.tax is not None else 0
        return AnomalyDetected(
            occurred_at=now,
            correlation_id=f"reconcile-{report.month}",
            source=_ALERT_SOURCE,
            severity=AnomalySeverity.INFO,
            reason=f"{report.month}: {pending} trade(s) still without EUR values",
        )
    details = [f"{c.source} {c.asset.value} disagrees with the book" for c in report.drifts]
    details += list(report.problems)
    shown = "; ".join(details[:_ALERT_DETAIL_LIMIT])
    more = len(details) - _ALERT_DETAIL_LIMIT
    if more > 0:
        shown += f"; {more} more"
    return AnomalyDetected(
        occurred_at=now,
        correlation_id=f"reconcile-{report.month}",
        source=_ALERT_SOURCE,
        severity=AnomalySeverity.WARNING,
        reason=f"{report.month} drift: {shown}; run `hermes reconcile {report.month}`",
    )


def _starting_capital(seeds: Sequence[StrategySeed]) -> dict[Currency, Decimal]:
    capital: dict[Currency, Decimal] = {}
    with localcontext(EXACT_CONTEXT):
        for seed in seeds:
            ccy = seed.capital.currency
            capital[ccy] = capital.get(ccy, _ZERO) + seed.capital.amount
    return capital


def _event_view(
    events: Sequence[TradeExecuted], seeds: Sequence[StrategySeed], end: datetime
) -> tuple[Mapping[Currency, Decimal] | None, list[str]]:
    """Replay each seeded strategy's events before ``end``; sum cash and positions.

    Returns:
        The account the events describe (``None`` when a replay failed) and
        the problems found.
    """
    seeded = {seed.strategy_id for seed in seeds}
    problems = [
        f"events: trades of strategy {sid.value!r}, which the roster does not seed"
        for sid in sorted({e.strategy_id for e in events} - seeded, key=lambda s: s.value)
    ]
    account: dict[Currency, Decimal] = {}
    replay_failed = False
    for seed in seeds:
        mine = [e for e in events if e.strategy_id == seed.strategy_id and e.occurred_at < end]
        rebuilt = PortfolioTracker.rebuilt(
            PortfolioSnapshot.seed(seed.strategy_id, seed.capital), mine
        )
        if isinstance(rebuilt, Failure):
            problems.append(f"events: {rebuilt.failure()}")
            replay_failed = True
            continue
        snapshot = rebuilt.unwrap().snapshot
        with localcontext(EXACT_CONTEXT):
            account[snapshot.quote] = account.get(snapshot.quote, _ZERO) + snapshot.cash
            for pair, position in snapshot.positions.items():
                account[pair.base] = account.get(pair.base, _ZERO) + position.quantity
    return (None if replay_failed else account), problems
