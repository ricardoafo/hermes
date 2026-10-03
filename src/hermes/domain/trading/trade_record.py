"""TradeRecord — what one fill writes to the ``trades`` table.

A :class:`TradeRecord` carries the exchange-native values of a fill (quote
currency price and fee, which strategy, sizing and kill rules keep reading) and,
when the EUR rate is known, its EUR values (:class:`TradeEur`). A record without
EUR values is stored with ``eur_status = pending`` and filled by the backfill.

Reference: ADR-002 §3, ADR-016 items 3 and 5, change ``trade-eur-values`` D-2, D-3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from hermes.domain.fx.eur_pricing import EurPricing, EurStatus
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["TradeEur", "TradeRecord"]


@dataclass(frozen=True, slots=True)
class TradeEur:
    """EUR values of one trade.

    Attributes:
        pricing: The rate the values were converted with.
        price_eur: EUR price of one base unit.
        fee_eur: EUR value of the fee.
    """

    pricing: EurPricing
    price_eur: Decimal
    fee_eur: Decimal

    @classmethod
    def price(cls, pricing: EurPricing, *, price: Money, fee: Money) -> TradeEur | None:
        """Convert a native price and fee to EUR.

        Args:
            pricing: Rate for the price's currency.
            price: Native price of one base unit, in ``pricing.quote``.
            fee: Native fee. Converted when it is in ``pricing.quote`` or EUR.

        Returns:
            The EUR values, or ``None`` when the fee is in a currency the
            pricing cannot convert (the row is then stored pending).

        Raises:
            ValueError: When ``price`` is not in ``pricing.quote``.
        """
        price_eur = pricing.to_eur(price).amount
        if fee.currency == pricing.quote:
            fee_eur = pricing.to_eur(fee).amount
        elif fee.currency == Currency.EUR:
            fee_eur = fee.amount
        else:
            return None
        return cls(pricing=pricing, price_eur=price_eur, fee_eur=fee_eur)


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """One executed trade as the ledger stores it.

    Attributes:
        correlation_id: Tracing id shared with the ``TradeExecuted`` event.
        pair: Traded pair; ``pair.quote`` is the native currency.
        side: BUY opens a lot; SELL consumes lots FIFO.
        qty: Base quantity, strictly positive.
        price: Native price of one base unit (quote currency).
        fee: Native fee.
        executed_at: Timezone-aware fill time.
        strategy_id: Strategy that produced the fill.
        lot_id: Id of the lot a BUY opens; ``None`` for a SELL.
        eur: EUR values, or ``None`` while pending.
    """

    correlation_id: str
    pair: Pair
    side: OrderSide
    qty: Decimal
    price: Decimal
    fee: Money
    executed_at: datetime
    strategy_id: StrategyId
    lot_id: LotId | None
    eur: TradeEur | None

    def __post_init__(self) -> None:
        """Enforce positive quantity and price, an aware time and the BUY/lot pairing.

        Raises:
            ValueError: On any violated invariant.
        """
        if self.qty <= Decimal("0"):
            raise ValueError(f"TradeRecord.qty must be > 0, got {self.qty}")
        if self.price <= Decimal("0"):
            raise ValueError(f"TradeRecord.price must be > 0, got {self.price}")
        if self.executed_at.tzinfo is None:
            raise ValueError(
                f"TradeRecord.executed_at must be timezone-aware, got {self.executed_at!r}"
            )
        if (self.side == OrderSide.BUY) != (self.lot_id is not None):
            raise ValueError("TradeRecord.lot_id is required for a BUY and forbidden for a SELL")
        if self.eur is not None and self.eur.pricing.quote != self.pair.quote:
            raise ValueError(
                f"TradeRecord.eur prices {self.eur.pricing.quote}, "
                f"the pair quote is {self.pair.quote}"
            )

    @classmethod
    def from_fill(cls, fill: Fill, *, correlation_id: str, eur: TradeEur | None) -> TradeRecord:
        """Build the record of ``fill``, minting a lot id for a BUY.

        Args:
            fill: The executed fill.
            correlation_id: Tracing id for the row and its event.
            eur: EUR values, or ``None`` while pending.

        Returns:
            The record to persist.
        """
        side = fill.order.side
        return cls(
            correlation_id=correlation_id,
            pair=fill.order.pair,
            side=side,
            qty=fill.executed_qty,
            price=fill.avg_price,
            fee=fill.fee,
            executed_at=fill.timestamp,
            strategy_id=fill.strategy_id,
            lot_id=LotId.new() if side == OrderSide.BUY else None,
            eur=eur,
        )

    @property
    def eur_status(self) -> EurStatus:
        """Return ``OK`` when EUR values are present, ``PENDING`` otherwise."""
        return EurStatus.PENDING if self.eur is None else EurStatus.OK
