"""TradeRatesPort — the ECB rates the tax pack's trail prints.

Read-only, like :mod:`hermes.ports.filing`. The concrete implementation is
:class:`hermes.adapters.ledger.trade_rates_reader.LedgerTradeRates`.

Reference: openspec/changes/568-tax-pack/change.md.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hermes.domain.filing.trail import TradeRate

__all__ = ["TradeRatesPort"]


@runtime_checkable
class TradeRatesPort(Protocol):
    """Read-only lookup of the ECB rate stored on each trade.

    Adapters MUST raise :class:`hermes.domain.errors.LedgerError` on any
    storage failure.
    """

    def eur_rates(self) -> dict[str, TradeRate]:
        """Return the stored rate per trade id; trades with no rate yet are absent."""
        ...
