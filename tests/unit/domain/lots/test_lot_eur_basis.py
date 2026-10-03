"""Tests for pending EUR bases and fee-aware consumptions (change trade-eur-values D-5, D-7).

Reference: ADR-016 item 3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from returns.result import Failure

from hermes.domain.errors import InvalidLot
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.lots.selection import select_lots_fifo
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_AT = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


def _pending_lot() -> Lot:
    return Lot.open(
        lot_id=LotId.new(),
        asset=Currency.BTC,
        qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
        cost_basis_per_unit=None,
        strategy_id=StrategyId("swing"),
        opened_at=_AT,
        cost_basis_native_per_unit=Money(amount=Decimal("50050"), currency=Currency.USDT),
    ).unwrap()


class TestPendingLot:
    """A lot may exist before its EUR basis is known."""

    def test_pending_lot_keeps_its_native_basis(self) -> None:
        """Invariant: the USDT basis is stored as USDT, never relabelled EUR."""
        lot = _pending_lot()
        assert (lot.cost_basis_per_unit, lot.cost_basis_native_per_unit) == (
            None,
            Money(amount=Decimal("50050"), currency=Currency.USDT),
        )

    def test_fifo_selection_refuses_a_pending_lot(self) -> None:
        """Invariant: no gain is computed against an unknown EUR basis."""
        result = select_lots_fifo(
            [_pending_lot()],
            Quantity(amount=Decimal("1"), asset=Currency.BTC),
            Money(amount=Decimal("60000"), currency=Currency.EUR),
            _AT,
        )
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), InvalidLot)


class TestConsumptionFee:
    """The sell fee share comes off the gain."""

    def test_sell_fee_share_is_subtracted(self) -> None:
        """Invariant: gain = (110 - 101) x 1 - 1 = 8 when the basis holds the buy fee."""
        consumption = LotConsumption.record(
            lot_id=LotId.new(),
            consumed_qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
            sell_price_per_unit=Money(amount=Decimal("110"), currency=Currency.EUR),
            lot_cost_basis_per_unit=Money(amount=Decimal("101"), currency=Currency.EUR),
            consumed_at=_AT,
            sell_fee_eur=Money(amount=Decimal("1"), currency=Currency.EUR),
        ).unwrap()
        assert consumption.realized_gain == Money(amount=Decimal("8"), currency=Currency.EUR)

    def test_non_eur_sell_fee_is_rejected(self) -> None:
        """Invariant: a USDT fee cannot be subtracted from an EUR gain."""
        result = LotConsumption.record(
            lot_id=LotId.new(),
            consumed_qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
            sell_price_per_unit=Money(amount=Decimal("110"), currency=Currency.EUR),
            lot_cost_basis_per_unit=Money(amount=Decimal("101"), currency=Currency.EUR),
            consumed_at=_AT,
            sell_fee_eur=Money(amount=Decimal("1"), currency=Currency.USDT),
        )
        assert isinstance(result, Failure)
        assert "sell_fee_eur must be EUR" in str(result.failure())
