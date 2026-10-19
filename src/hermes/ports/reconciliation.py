"""Reconciliation ports — the read-only views a monthly reconciliation compares.

:class:`ReconciliationReadPort` reads the ledger; implementations live in
:mod:`hermes.adapters.ledger.reconciliation_reader`. It is read-only by
contract: it never writes, never migrates and can run next to a live
``hermes serve``.

:class:`BalanceReadPort` reads an account's balances. Shadow mode has no real
account, so nothing implements it yet; the live exchange adapter supplies one
when real capital is wired (#29).

Reference: openspec/archive/35-monthly-reconciliation/change.md D-1, D-2.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Protocol, runtime_checkable

from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.reconciliation.holdings import LedgerTrade
from hermes.domain.value_objects.currency import Currency

__all__ = ["BalanceReadPort", "ReconciliationReadPort"]


@runtime_checkable
class ReconciliationReadPort(Protocol):
    """Read-only ledger queries for a reconciliation."""

    def trades(self) -> tuple[LedgerTrade, ...]:
        """Return every trades-table row, oldest fill first.

        Raises:
            LedgerError: On any read failure or an unreadable row.
        """
        ...

    def open_lot_quantities(self) -> Mapping[Currency, Decimal]:
        """Return the remaining quantity of the lots table, summed per asset.

        Raises:
            LedgerError: On any read failure.
        """
        ...

    def trade_events(self) -> tuple[TradeExecuted, ...]:
        """Return every ``TradeExecuted`` event of every strategy, oldest first.

        Raises:
            LedgerError: On any read failure or an unreadable payload.
        """
        ...


@runtime_checkable
class BalanceReadPort(Protocol):
    """An account's current balances, read-only."""

    def balances(self, assets: Iterable[Currency]) -> Mapping[Currency, Decimal]:
        """Return the total balance (free plus locked) of each asset.

        Args:
            assets: The assets to read; an asset not held reads zero.

        Raises:
            InfraError: On any transport or venue failure.
        """
        ...
