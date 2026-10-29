"""DailyTradeDigest domain event — end-of-day summary of trading activity.

Aggregates a day's trades into a compact summary suitable for a Slack
report (per the extended 2026-08-23 decisions-journal routing table).
Emitted once per trading day by a scheduled job; carries the top winners
and losers so the notifier can render a self-contained report without
re-querying the ledger.

Reference: ADR-003 §02 (reporting cadence), code-conventions.md §10 + §3.
"""

from dataclasses import dataclass
from datetime import date
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair

__all__ = ["DailyTradeDigest", "TradeSummary"]


@dataclass(frozen=True, slots=True)
class TradeSummary:
    """Compact per-pair PnL entry for the daily digest.

    Not a domain event on its own — nested inside :class:`DailyTradeDigest`
    to keep the digest self-contained. Frozen for equality-by-value and
    safe use in tuples embedded in events.

    Fields:
        pair: The trading pair the summary aggregates.
        pnl: Realized profit or loss for the day, in the reporting
            currency (typically EUR). May be negative.
    """

    pair: Pair
    pnl: Money


@dataclass(frozen=True, slots=True)
class DailyTradeDigest(DomainEvent):
    """One day's worth of trading activity, rolled up for reporting.

    Emitted at end-of-trading-day. The digest is intentionally small — a
    handful of aggregates plus the top winners / losers — so subscribers
    render the message without further ledger reads.

    Fields:
        day: The calendar date the digest covers (Europe/Madrid business
            date; time zone conversion happens at the scheduler edge).
        total_trades: Total number of trade attempts (fills + rejections)
            during ``day``. Non-negative.
        filled: How many of ``total_trades`` were filled successfully.
            ``filled <= total_trades`` (not enforced here — the
            aggregation job owns that invariant; the event is a passive
            data carrier).
        rejected: How many were rejected. Same invariant note as above.
        total_notional: Sum of notional value across every filled trade,
            in the reporting currency.
        top_winners: Up to N :class:`TradeSummary` entries with the
            highest positive PnL, ordered highest-first.
        top_losers: Up to N :class:`TradeSummary` entries with the
            largest losses, ordered largest-loss-first.

    Reference: ADR-003 §02 — daily reporting flow.
    """

    __event_schema_version__: ClassVar[int] = 1

    day: date
    total_trades: int
    filled: int
    rejected: int
    total_notional: Money
    top_winners: tuple[TradeSummary, ...]
    top_losers: tuple[TradeSummary, ...]
