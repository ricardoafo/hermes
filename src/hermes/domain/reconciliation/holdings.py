"""Holdings comparison — what the trade book says is held versus another view.

The *book* is the trades table: starting capital plus every fill's flows. Each
other view of the same account (the open tax lots, the ``TradeExecuted``
replay the shadow loop trades from, an exchange's balances) is compared to it
asset by asset. A difference within the tolerance is a match; anything larger
is drift. Fail loud, never auto-heal (ADR-003 §10): this module only reports.

Reference: openspec/archive/35-monthly-reconciliation/change.md D-1, D-2.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Final

from hermes.domain.portfolio.precision import EXACT_CONTEXT
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "CheckStatus",
    "HoldingCheck",
    "LedgerTrade",
    "book_balances",
    "compare_holdings",
]

_ZERO: Final = Decimal("0")


@dataclass(frozen=True, slots=True)
class LedgerTrade:
    """One row of the trades table, as the reconciliation reads it.

    Attributes:
        trade_id: Ledger row id; breaks ties between fills at the same instant.
        pair: Traded pair.
        side: BUY or SELL.
        qty: Base quantity, strictly positive.
        price: Native price of one base unit, in ``pair.quote``.
        fee: Native fee.
        executed_at: UTC-aware fill time.
        price_eur: EUR price of one base unit; ``None`` while the rate is pending.
        fee_eur: EUR fee; ``None`` while the rate is pending.

    Raises:
        ValueError: On a non-positive quantity, a negative price or fee, a
            naive ``executed_at`` or exactly one EUR value set.
    """

    trade_id: int
    pair: Pair
    side: OrderSide
    qty: Decimal
    price: Decimal
    fee: Money
    executed_at: datetime
    price_eur: Decimal | None
    fee_eur: Decimal | None

    def __post_init__(self) -> None:
        """Validate the row.

        Raises:
            ValueError: See the class docstring.
        """
        if self.qty <= _ZERO:
            raise ValueError(f"LedgerTrade.qty must be > 0, got {self.qty}")
        if self.price < _ZERO or self.fee.amount < _ZERO:
            raise ValueError("LedgerTrade.price and fee must be >= 0")
        if self.executed_at.tzinfo is None:
            raise ValueError("LedgerTrade.executed_at must be UTC-aware")
        if (self.price_eur is None) != (self.fee_eur is None):
            raise ValueError("LedgerTrade.price_eur and fee_eur are both set or both pending")

    @property
    def eur_pending(self) -> bool:
        """True while the trade has no EUR values yet."""
        return self.price_eur is None


class CheckStatus(StrEnum):
    """Outcome of one asset's comparison."""

    MATCH = "match"
    DRIFT = "drift"


@dataclass(frozen=True, slots=True)
class HoldingCheck:
    """One asset compared between the book and one other view.

    Attributes:
        source: Name of the view compared to the book (``"lots"``,
            ``"events"``, ``"exchange"``).
        asset: The asset.
        book: Quantity the trade book holds.
        observed: Quantity the other view holds.
        tolerance: Largest absolute difference still counted as a match.
    """

    source: str
    asset: Currency
    book: Decimal
    observed: Decimal
    tolerance: Decimal

    @property
    def drift(self) -> Decimal:
        """``observed - book``: positive when the other view holds more."""
        with localcontext(EXACT_CONTEXT):
            return self.observed - self.book

    @property
    def status(self) -> CheckStatus:
        """MATCH when ``|drift| <= tolerance``, else DRIFT."""
        return CheckStatus.MATCH if abs(self.drift) <= self.tolerance else CheckStatus.DRIFT


def book_balances(
    trades: Iterable[LedgerTrade], starting: Mapping[Currency, Decimal]
) -> dict[Currency, Decimal]:
    """Return what the trade book holds per asset after ``trades``.

    A BUY adds its quantity to the base and takes ``qty * price`` from the
    quote; a SELL does the opposite. Every fee is taken from the currency it
    was charged in. Assets that net to zero stay in the result.

    Args:
        trades: The fills to fold, in any order (the sum does not depend on it).
        starting: Balances before the first fill (the configured capital).

    Returns:
        Balance per asset.
    """
    balances: dict[Currency, Decimal] = dict(starting)
    with localcontext(EXACT_CONTEXT):
        for trade in trades:
            sign = 1 if trade.side == OrderSide.BUY else -1
            base, quote = trade.pair.base, trade.pair.quote
            balances[base] = balances.get(base, _ZERO) + sign * trade.qty
            balances[quote] = balances.get(quote, _ZERO) - sign * trade.qty * trade.price
            fee_ccy = trade.fee.currency
            balances[fee_ccy] = balances.get(fee_ccy, _ZERO) - trade.fee.amount
    return balances


def compare_holdings(
    book: Mapping[Currency, Decimal],
    observed: Mapping[Currency, Decimal],
    *,
    source: str,
    tolerance: Decimal,
    assets: Iterable[Currency] | None = None,
) -> tuple[HoldingCheck, ...]:
    """Compare two views of the account asset by asset.

    An asset missing from one side counts as zero there. Assets that are zero
    on both sides are left out, so a coin traded and fully sold long ago does
    not pad the report.

    Args:
        book: What the trade book holds.
        observed: What the other view holds.
        source: Name of the other view, copied to every check.
        tolerance: Largest absolute difference still counted as a match.
        assets: Restrict the comparison to these assets; defaults to every
            asset on either side.

    Returns:
        One check per asset, sorted by asset code.

    Raises:
        ValueError: On a negative tolerance.
    """
    if tolerance < _ZERO:
        raise ValueError(f"tolerance must be >= 0, got {tolerance}")
    scope = set(assets) if assets is not None else set(book) | set(observed)
    checks = [
        HoldingCheck(
            source=source,
            asset=asset,
            book=book.get(asset, _ZERO),
            observed=observed.get(asset, _ZERO),
            tolerance=tolerance,
        )
        for asset in scope
        if book.get(asset, _ZERO) != _ZERO or observed.get(asset, _ZERO) != _ZERO
    ]
    return tuple(sorted(checks, key=lambda check: check.asset.value))
