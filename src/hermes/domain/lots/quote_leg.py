"""The quote-currency leg of every trade, replayed as its own FIFO asset.

ADR-016 item 4 treats the quote currency (USDT) as an asset with its own FIFO
lots: the shadow start capital opens one lot, buying the base asset disposes
of quote units and selling it acquires quote units. The quote side of the
ledger is therefore one more trade sequence for
:func:`~hermes.domain.lots.eur_fifo.replay_fifo_eur`, derived here from the
seed and the stored trades (change ``405-usdt-fifo`` D-2, D-4):

- seed: acquires ``amount`` quote units at ``1 / r_seed`` EUR each;
- BUY of base: disposes ``price * qty + quote_fee`` units at ``1 / r``, no fee
  (the fee is already in the base lot's acquisition value);
- SELL of base: acquires ``price * qty - quote_fee`` units at ``1 / r``, no fee
  (the fee is already in the base sale's transmission value).

``r`` is the trade's EUR rate in quote units per euro. EUR value moves between
the two legs, so the total basis over all assets changes only by realized gains
(R-3). A fee charged in the base asset on a BUY reduces the base lot's quantity
instead of adding quote units (D-2); a fee in any other asset is refused.

Reference: ADR-016 item 4, change ``405-usdt-fifo``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError
from hermes.domain.lots.eur_fifo import EurTrade, acquisition_value
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.precision import exact
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = [
    "CapitalSeed",
    "LegTrade",
    "QuoteLegError",
    "base_leg_trade",
    "net_base_qty",
    "quote_leg_qty",
    "quote_leg_trades",
    "quote_unit_eur",
    "seed_timestamp",
]

_UNIT_QUANTUM: Final[Decimal] = Decimal("1e-30")
_ZERO: Final[Decimal] = Decimal("0")


@dataclass(frozen=True, slots=True)
class QuoteLegError(DomainError):
    """A trade cannot be turned into a quote-currency leg.

    Fields:
        ref: The trade's ref.
        reason: What is wrong with it.
    """

    ref: str
    reason: str

    def __str__(self) -> str:
        """Return a human-readable summary."""
        return f"trade {self.ref} has no quote leg: {self.reason}"


@dataclass(frozen=True, slots=True)
class CapitalSeed:
    """The start capital of one strategy, held as a quote-currency lot.

    Attributes:
        strategy_id: Strategy the capital belongs to.
        amount: The capital, in the quote currency it is held in.
        seeded_at: Timezone-aware seed date (D-1).
        eur_rate: Quote units per euro on the seed date, ``None`` while pending.
    """

    strategy_id: StrategyId
    amount: Money
    seeded_at: datetime
    eur_rate: Decimal | None = None

    def __post_init__(self) -> None:
        """Reject a non-positive amount or rate and a naive seed date.

        Raises:
            ValueError: On any violated invariant.
        """
        if self.amount.amount <= _ZERO:
            raise ValueError(f"CapitalSeed.amount must be > 0, got {self.amount}")
        if self.seeded_at.tzinfo is None:
            raise ValueError(f"CapitalSeed.seeded_at must be timezone-aware: {self.seeded_at!r}")
        if self.eur_rate is not None and self.eur_rate <= _ZERO:
            raise ValueError(f"CapitalSeed.eur_rate must be > 0, got {self.eur_rate}")

    @property
    def value_eur(self) -> Decimal | None:
        """Return the EUR acquisition value of the seed lot, ``None`` while pending."""
        if self.eur_rate is None:
            return None
        return acquisition_value(
            price=quote_unit_eur(self.eur_rate), qty=self.amount.amount, fee=_ZERO
        )

    def leg(self, ref: str) -> EurTrade:
        """Return the seed as the BUY that opens its lot.

        Args:
            ref: The seed's ref in the replay.

        Returns:
            The opening trade, unpriced while the rate is pending.
        """
        unit = None if self.eur_rate is None else quote_unit_eur(self.eur_rate)
        return EurTrade(
            ref=ref,
            side=OrderSide.BUY,
            qty=self.amount.amount,
            executed_at=self.seeded_at,
            price_eur=unit,
            fee_eur=None if unit is None else _ZERO,
        )


@dataclass(frozen=True, slots=True)
class LegTrade:
    """One stored trade, with what both of its legs need.

    Attributes:
        ref: Caller's stable id (the ledger row id).
        pair: Traded pair.
        side: BUY or SELL of ``pair.base``.
        qty: Base quantity filled, strictly positive.
        price: Native price of one base unit, in ``pair.quote``.
        fee: Native fee.
        executed_at: Fill time.
        eur_rate: Quote units per euro, ``None`` while pending.
        price_eur: EUR price of one base unit, ``None`` while pending.
        fee_eur: EUR fee, ``None`` while pending.
    """

    ref: str
    pair: Pair
    side: OrderSide
    qty: Decimal
    price: Decimal
    fee: Money
    executed_at: datetime
    eur_rate: Decimal | None
    price_eur: Decimal | None
    fee_eur: Decimal | None


def seed_timestamp(earliest: datetime | None, now: datetime) -> datetime:
    """Return the seed date: the ledger's earliest activity, else ``now`` (D-1).

    Args:
        earliest: Earliest bar, signal or trade time already in the ledger.
        now: Current time, used for a ledger without activity.

    Returns:
        The seed date.
    """
    return now if earliest is None else earliest


def quote_unit_eur(rate: Decimal) -> Decimal:
    """Return the EUR value of one quote unit at ``rate`` quote units per euro.

    Cut to 30 decimals so stored values keep a bounded digit count; the cut
    moves a value by under ``5e-31`` EUR per unit.

    Args:
        rate: Quote units per euro, strictly positive.

    Returns:
        ``1 / rate``.
    """
    with exact():
        return (Decimal(1) / rate).quantize(_UNIT_QUANTUM)


def net_base_qty(*, side: OrderSide, qty: Decimal, fee: Money, base: Currency) -> Decimal:
    """Return the base quantity a trade moves after a fee taken in the base asset.

    Args:
        side: Trade side.
        qty: Filled base quantity.
        fee: Native fee.
        base: The pair's base asset.

    Returns:
        ``qty - fee`` for a BUY whose fee is in ``base`` (the venue delivers
        the net quantity, D-2), ``qty`` otherwise.
    """
    if side == OrderSide.BUY and fee.currency == base:
        return qty - fee.amount
    return qty


def base_leg_trade(trade: LegTrade) -> EurTrade:
    """Return the base-asset replay trade of a stored trade.

    Args:
        trade: The stored trade.

    Returns:
        The trade with its net base quantity and EUR values.
    """
    return EurTrade(
        ref=trade.ref,
        side=trade.side,
        qty=net_base_qty(side=trade.side, qty=trade.qty, fee=trade.fee, base=trade.pair.base),
        executed_at=trade.executed_at,
        price_eur=trade.price_eur,
        fee_eur=trade.fee_eur,
    )


def quote_leg_qty(trade: LegTrade) -> Result[Decimal, DomainError]:
    """Return how many quote units a trade disposes of (BUY) or acquires (SELL).

    Args:
        trade: The stored trade.

    Returns:
        ``price * qty + quote_fee`` for a BUY, ``price * qty - quote_fee`` for a
        SELL, or ``Failure(QuoteLegError)`` for a fee in an asset other than the
        quote (or the base, on a BUY) or a non-positive result.
    """
    quote_fee = _ZERO
    if trade.fee.currency == trade.pair.quote:
        quote_fee = trade.fee.amount
    elif not (trade.side == OrderSide.BUY and trade.fee.currency == trade.pair.base):
        return Failure(
            QuoteLegError(
                ref=trade.ref,
                reason=f"{trade.side.value} fee in {trade.fee.currency} is not supported",
            )
        )
    with exact():
        gross = trade.price * trade.qty
        moved = gross + quote_fee if trade.side == OrderSide.BUY else gross - quote_fee
    if moved <= _ZERO:
        return Failure(QuoteLegError(ref=trade.ref, reason=f"quote quantity {moved} is not > 0"))
    return Success(moved)


def quote_leg_trades(
    quote: Currency, seeds: Mapping[str, CapitalSeed], trades: Sequence[LegTrade]
) -> Result[tuple[EurTrade, ...], DomainError]:
    """Derive the quote asset's replay sequence from its seeds and trades.

    Args:
        quote: The quote asset being replayed.
        seeds: Seeds held in ``quote``, keyed by their replay ref.
        trades: Trades of pairs quoted in ``quote``, in any order.

    Returns:
        The trades in execution order, a seed ahead of a trade at the same
        instant, or the first ``QuoteLegError``.
    """
    keyed: list[tuple[tuple[datetime, int, str], EurTrade]] = []
    for ref, seed in seeds.items():
        if seed.amount.currency != quote:
            return Failure(QuoteLegError(ref=ref, reason=f"seed is held in {seed.amount.currency}"))
        keyed.append(((seed.seeded_at, 0, ref), seed.leg(ref)))
    for trade in trades:
        if trade.pair.quote != quote:
            return Failure(QuoteLegError(ref=trade.ref, reason=f"pair is quoted in {trade.pair}"))
        moved = quote_leg_qty(trade)
        if isinstance(moved, Failure):
            return moved
        unit = None if trade.eur_rate is None else quote_unit_eur(trade.eur_rate)
        leg = EurTrade(
            ref=trade.ref,
            side=OrderSide.SELL if trade.side == OrderSide.BUY else OrderSide.BUY,
            qty=moved.unwrap(),
            executed_at=trade.executed_at,
            price_eur=unit,
            fee_eur=None if unit is None else _ZERO,
        )
        keyed.append(((trade.executed_at, 1, trade.ref.zfill(20)), leg))
    keyed.sort(key=lambda item: item[0])
    return Success(tuple(leg for _, leg in keyed))
