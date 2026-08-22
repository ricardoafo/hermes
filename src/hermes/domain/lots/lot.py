"""Lot aggregate — a FIFO tax lot opened by a BUY trade.

A ``Lot`` records the identity, asset, original acquired quantity, remaining
quantity available for future SELL consumption, cost basis (EUR per base unit),
and the moment the lot was opened. Lots are frozen — ``consume`` returns a new
``Lot`` with ``remaining_qty`` reduced rather than mutating in place.

Domain purity: this module imports only from ``hermes.domain.value_objects``
and ``hermes.domain.errors``. No ``sqlite3``, no ``pydantic``, no
infrastructure. Import-linter enforces (see pyproject ``domain-is-pure``).

Reference: ADR-003 §03 (TAX_LOTS table + FIFO example), ADR-002 (tax posture),
code-conventions.md §1 (Value Object), §3 (immutability), §6 (Decimal).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError, InvalidLot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity

__all__ = ["Lot"]


@dataclass(frozen=True, slots=True)
class Lot:
    """Immutable FIFO tax lot opened by a BUY trade.

    Fields:
        lot_id: Stable ULID identifier. Lexical order matches creation order,
            so ``lot_id`` doubles as the deterministic tie-breaker when two
            lots share ``opened_at`` (see :func:`select_lots_fifo`).
        asset: The base currency this lot holds (BTC, ETH, …).
        original_qty: The quantity acquired at open time. Never mutates.
        remaining_qty: The quantity still available for consumption. Reduced
            by every ``consume`` call via a new frozen instance.
        cost_basis_per_unit: Cost of ONE unit of ``asset`` in EUR at trade
            time. Multiplied by consumed quantity to derive realized gain.
        opened_at: UTC-aware datetime of the opening BUY. Primary FIFO key.

    Invariants:
        - ``original_qty.asset == asset``
        - ``remaining_qty.asset == asset``
        - ``0 ≤ remaining_qty.amount ≤ original_qty.amount``
        - ``cost_basis_per_unit.currency == Currency.EUR`` (ADR-003 §03)
        - ``opened_at`` is timezone-aware (I-06 requires UTC timestamps)

    Reference: ADR-003 §03 TAX_LOTS table.
    """

    lot_id: LotId
    asset: Currency
    original_qty: Quantity
    remaining_qty: Quantity
    cost_basis_per_unit: Money
    opened_at: datetime

    def __post_init__(self) -> None:
        """Enforce the invariants documented on the class."""
        if self.original_qty.asset != self.asset:
            raise ValueError(
                f"Lot.original_qty asset {self.original_qty.asset!r} does not match "
                f"lot asset {self.asset!r}"
            )
        if self.remaining_qty.asset != self.asset:
            raise ValueError(
                f"Lot.remaining_qty asset {self.remaining_qty.asset!r} does not match "
                f"lot asset {self.asset!r}"
            )
        if self.remaining_qty.amount > self.original_qty.amount:
            raise ValueError(
                f"Lot.remaining_qty {self.remaining_qty.amount} exceeds "
                f"original_qty {self.original_qty.amount}"
            )
        if self.cost_basis_per_unit.currency != Currency.EUR:
            raise ValueError(
                f"Lot.cost_basis_per_unit must be in EUR (ADR-003 §03), "
                f"got {self.cost_basis_per_unit.currency!r}"
            )
        if self.opened_at.tzinfo is None:
            raise ValueError(f"Lot.opened_at must be timezone-aware, got naive {self.opened_at!r}")

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def open(
        cls,
        *,
        lot_id: LotId,
        asset: Currency,
        qty: Quantity,
        cost_basis_per_unit: Money,
        opened_at: datetime,
    ) -> Result[Lot, DomainError]:
        """Open a fresh lot with ``remaining_qty == qty`` (nothing consumed yet).

        Args:
            lot_id: Deterministic identifier — see :class:`LotId`.
            asset: Base currency of the acquired quantity.
            qty: Non-zero positive quantity. Zero-quantity lots are rejected
                because they carry no economic meaning and would confuse FIFO.
            cost_basis_per_unit: Cost of one unit in EUR at trade time.
            opened_at: UTC-aware datetime of the opening BUY.

        Returns:
            ``Success(Lot)`` on valid input; ``Failure(InvalidLot)`` when
            ``qty`` is zero (a lot with 0 quantity is invalid — a BUY of
            zero would never emit a TradeExecuted).
        """
        if qty.amount == Decimal("0"):
            return Failure(InvalidLot(reason="Lot.open: qty must be > 0"))
        try:
            return Success(
                cls(
                    lot_id=lot_id,
                    asset=asset,
                    original_qty=qty,
                    remaining_qty=qty,
                    cost_basis_per_unit=cost_basis_per_unit,
                    opened_at=opened_at,
                )
            )
        except ValueError as exc:
            return Failure(InvalidLot(reason=str(exc)))

    # ------------------------------------------------------------------
    # Consumption — returns a new Lot with reduced remaining_qty
    # ------------------------------------------------------------------

    def consume(self, amount: Quantity) -> Result[Lot, DomainError]:
        """Return a new :class:`Lot` with ``remaining_qty`` reduced by ``amount``.

        Args:
            amount: Non-negative quantity to consume. Must not exceed
                ``self.remaining_qty`` and must share the lot's asset.

        Returns:
            ``Success(new_lot)`` with ``remaining_qty -= amount``, or
            ``Failure(InvalidLot)`` on asset mismatch or over-consumption.
        """
        if amount.asset != self.asset:
            return Failure(
                InvalidLot(
                    reason=(
                        f"Lot.consume: amount asset {amount.asset!r} does not match "
                        f"lot asset {self.asset!r}"
                    )
                )
            )
        if amount.amount > self.remaining_qty.amount:
            return Failure(
                InvalidLot(
                    reason=(
                        f"Lot.consume: cannot consume {amount.amount} from lot "
                        f"{self.lot_id} with remaining {self.remaining_qty.amount}"
                    )
                )
            )
        new_remaining = Quantity(amount=self.remaining_qty.amount - amount.amount, asset=self.asset)
        return Success(replace(self, remaining_qty=new_remaining))

    def is_closed(self) -> bool:
        """Return True when ``remaining_qty`` has been fully consumed."""
        return self.remaining_qty.amount == Decimal("0")
