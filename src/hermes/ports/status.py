"""Status read port — the read-only ledger queries ``hermes status`` needs.

Implementations live in :mod:`hermes.adapters.ledger.status_reader`. The port
is read-only by contract: an implementation never writes, never migrates and
never takes the serve process's locks, so the console can run next to a live
``hermes serve``.

Reference: openspec/changes/370-hermes-status/change.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from hermes.domain.events.risk_halt_recorded import RiskHaltRecorded
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["ShadowEntry", "StatusReadPort"]


@dataclass(frozen=True, slots=True)
class ShadowEntry:
    """An open shadow position as the ledger records it: entry facts only.

    Attributes:
        pair: Trading pair.
        entry_time: ``occurred_at`` of the entry signal, UTC-aware.
        entry_price: Close price of the entry bar.
    """

    pair: Pair
    entry_time: datetime
    entry_price: Decimal


@runtime_checkable
class StatusReadPort(Protocol):
    """Read-only questions about the loop's progress."""

    def last_processed_close(self, pair: Pair, interval: KlineInterval) -> datetime | None:
        """Return the newest processed bar's inclusive close time, or ``None``.

        Args:
            pair: Trading pair.
            interval: Candle-duration bucket.
        """
        ...

    def count_bars_since(self, interval: KlineInterval, since: datetime) -> int:
        """Return how many bars of ``interval`` were processed at or after ``since``.

        Args:
            interval: Candle-duration bucket, across every pair.
            since: Inclusive lower bound on the bar's close, UTC-aware.
        """
        ...

    def count_signals_since(self, since: datetime) -> int:
        """Return how many signals were recorded at or after ``since``.

        Args:
            since: Inclusive lower bound, UTC-aware.
        """
        ...

    def open_entries(self) -> tuple[ShadowEntry, ...]:
        """Return every pair whose latest filled entry has not exited, oldest first."""
        ...

    def halts_since(self, since: datetime) -> tuple[RiskHaltRecorded, ...]:
        """Return every halt and kill recorded at or after ``since``, newest first.

        Args:
            since: Inclusive lower bound on the signal time, UTC-aware.
        """
        ...
