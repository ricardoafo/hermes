"""Parity source port: what the backtest/live parity check reads from the ledger.

The parity check (#358) replays a day's stored live bars and compares the
decisions it gets with the ones the serve loop recorded. Everything it needs
is a read: bars, the signals serve emitted, and the trades serve filled. The
concrete implementation opens the ledger read-only.

Reference: code-conventions.md §5 (Ports as Protocols), GitHub issue #358.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["ParitySourcePort"]


@runtime_checkable
class ParitySourcePort(Protocol):
    """Read-only view of the ledger for the parity check.

    Adapters MUST raise :class:`hermes.domain.errors.LedgerError` on a failed
    read and MUST NOT write.
    """

    def bars_closing_between(
        self, pair: Pair, interval: KlineInterval, after: datetime, upto: datetime
    ) -> tuple[Bar, ...]:
        """Return stored live bars with ``after < close_time <= upto``, oldest first."""
        ...

    def signals_between(
        self, strategy_id: StrategyId, after: datetime, upto: datetime
    ) -> tuple[SignalEmitted, ...]:
        """Return the strategy's ``SignalEmitted`` with ``after < occurred_at <= upto``."""
        ...

    def latest_signal(
        self,
        strategy_id: StrategyId,
        pair: Pair,
        *,
        at_or_before: datetime,
        kind: SignalKind | None = None,
    ) -> SignalEmitted | None:
        """Return the newest ``SignalEmitted`` of the strategy and pair up to a bound.

        With ``kind``, only signals of that kind count (any kind when ``None``).
        """
        ...

    def trades(self, strategy_id: StrategyId) -> tuple[TradeExecuted, ...]:
        """Return every ``TradeExecuted`` of the strategy, oldest first."""
        ...
