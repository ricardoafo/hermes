"""Tests for the :class:`Lot` aggregate.

Verifies the class-level invariants documented on :class:`Lot`:

- ``original_qty.asset`` and ``remaining_qty.asset`` must match ``asset``.
- ``remaining_qty`` cannot exceed ``original_qty``.
- ``cost_basis_per_unit`` must be EUR (ADR-003 §03).
- ``opened_at`` must be timezone-aware.
- ``Lot.open`` refuses zero quantity.
- ``consume`` returns a new frozen instance with reduced remaining.
- ``consume`` rejects over-consumption and asset mismatch.
- ``is_closed`` flips only when ``remaining_qty == 0``.

Reference: ADR-003 §03, code-conventions.md §1 + §3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from returns.result import Failure, Success

from hermes.domain.errors import InvalidLot
from hermes.domain.lots.lot import Lot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_SWING = StrategyId("swing")


def _btc_lot(
    *,
    remaining: Decimal = Decimal("0.5"),
    original: Decimal = Decimal("0.5"),
    cost_basis: Decimal = Decimal("50000"),
    opened_at: datetime | None = None,
) -> Lot:
    """Return a deterministic BTC lot for reuse across tests."""
    return Lot(
        lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
        asset=Currency.BTC,
        original_qty=Quantity(amount=original, asset=Currency.BTC),
        remaining_qty=Quantity(amount=remaining, asset=Currency.BTC),
        cost_basis_per_unit=Money(amount=cost_basis, currency=Currency.EUR),
        strategy_id=_SWING,
        opened_at=opened_at or datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
    )


class TestLotConstruction:
    """Constructor enforces the class invariants."""

    def test_original_asset_mismatch_raises(self) -> None:
        """original_qty.asset must match Lot.asset — mismatch raises ValueError."""
        with pytest.raises(ValueError, match="original_qty asset"):
            Lot(
                lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
                asset=Currency.BTC,
                original_qty=Quantity(amount=Decimal("1"), asset=Currency.ETH),
                remaining_qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
                cost_basis_per_unit=Money(amount=Decimal("50000"), currency=Currency.EUR),
                strategy_id=_SWING,
                opened_at=datetime(2026, 8, 23, tzinfo=UTC),
            )

    def test_remaining_asset_mismatch_raises(self) -> None:
        """remaining_qty.asset must match Lot.asset — mismatch raises ValueError."""
        with pytest.raises(ValueError, match="remaining_qty asset"):
            Lot(
                lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
                asset=Currency.BTC,
                original_qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
                remaining_qty=Quantity(amount=Decimal("1"), asset=Currency.ETH),
                cost_basis_per_unit=Money(amount=Decimal("50000"), currency=Currency.EUR),
                strategy_id=_SWING,
                opened_at=datetime(2026, 8, 23, tzinfo=UTC),
            )

    def test_remaining_exceeding_original_raises(self) -> None:
        """remaining_qty cannot exceed original_qty — a nonsensical state."""
        with pytest.raises(ValueError, match="exceeds"):
            Lot(
                lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
                asset=Currency.BTC,
                original_qty=Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
                remaining_qty=Quantity(amount=Decimal("1.0"), asset=Currency.BTC),
                cost_basis_per_unit=Money(amount=Decimal("50000"), currency=Currency.EUR),
                strategy_id=_SWING,
                opened_at=datetime(2026, 8, 23, tzinfo=UTC),
            )

    def test_non_eur_cost_basis_raises(self) -> None:
        """Cost basis must be EUR — ADR-003 §03 requires EUR at write time."""
        with pytest.raises(ValueError, match="EUR"):
            Lot(
                lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
                asset=Currency.BTC,
                original_qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
                remaining_qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
                cost_basis_per_unit=Money(amount=Decimal("50000"), currency=Currency.USDT),
                strategy_id=_SWING,
                opened_at=datetime(2026, 8, 23, tzinfo=UTC),
            )

    def test_naive_opened_at_raises(self) -> None:
        """opened_at must be timezone-aware — code-conventions §9 forbids naive."""
        with pytest.raises(ValueError, match="timezone-aware"):
            Lot(
                lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
                asset=Currency.BTC,
                original_qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
                remaining_qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
                cost_basis_per_unit=Money(amount=Decimal("50000"), currency=Currency.EUR),
                strategy_id=_SWING,
                opened_at=datetime(2026, 8, 23, 14, 22, 3),  # naive on purpose
            )


class TestLotOpenFactory:
    """Lot.open enforces the >0-quantity precondition and returns Result."""

    def test_open_returns_lot_with_remaining_equal_to_qty(self) -> None:
        """A fresh lot has remaining_qty == original_qty (nothing consumed yet)."""
        result = Lot.open(
            lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
            asset=Currency.BTC,
            qty=Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
            cost_basis_per_unit=Money(amount=Decimal("50000"), currency=Currency.EUR),
            strategy_id=_SWING,
            opened_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
        assert isinstance(result, Success)
        lot = result.unwrap()
        assert lot.original_qty.amount == Decimal("0.5")
        assert lot.remaining_qty.amount == Decimal("0.5")

    def test_open_with_zero_qty_returns_failure(self) -> None:
        """Zero-quantity BUYs never happen — refuse the invalid input up front."""
        result = Lot.open(
            lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
            asset=Currency.BTC,
            qty=Quantity(amount=Decimal("0"), asset=Currency.BTC),
            cost_basis_per_unit=Money(amount=Decimal("50000"), currency=Currency.EUR),
            strategy_id=_SWING,
            opened_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
        assert result == Failure(InvalidLot(reason="Lot.open: qty must be > 0"))


class TestLotConsume:
    """consume returns a new frozen Lot with reduced remaining_qty."""

    def test_partial_consumption_reduces_remaining(self) -> None:
        """After consume, remaining_qty == original - consumed; original unchanged."""
        lot = _btc_lot(remaining=Decimal("0.5"), original=Decimal("0.5"))
        result = lot.consume(Quantity(amount=Decimal("0.3"), asset=Currency.BTC))
        assert isinstance(result, Success)
        new_lot = result.unwrap()
        assert new_lot.remaining_qty.amount == Decimal("0.2")
        assert new_lot.original_qty.amount == Decimal("0.5")
        # Original instance unchanged (frozen dataclass semantics).
        assert lot.remaining_qty.amount == Decimal("0.5")

    def test_full_consumption_leaves_lot_closed(self) -> None:
        """Consuming everything zeros remaining_qty and flips is_closed to True."""
        lot = _btc_lot(remaining=Decimal("0.5"), original=Decimal("0.5"))
        result = lot.consume(Quantity(amount=Decimal("0.5"), asset=Currency.BTC))
        assert isinstance(result, Success)
        new_lot = result.unwrap()
        assert new_lot.remaining_qty.amount == Decimal("0")
        assert new_lot.is_closed() is True

    def test_over_consumption_returns_failure(self) -> None:
        """Trying to consume more than remaining returns Failure(InvalidLot)."""
        lot = _btc_lot(remaining=Decimal("0.1"), original=Decimal("0.5"))
        result = lot.consume(Quantity(amount=Decimal("0.3"), asset=Currency.BTC))
        assert result == Failure(
            InvalidLot(
                reason=(f"Lot.consume: cannot consume 0.3 from lot {lot.lot_id} with remaining 0.1")
            )
        )

    def test_asset_mismatch_returns_failure(self) -> None:
        """Consume with a different asset returns Failure(InvalidLot)."""
        lot = _btc_lot()
        result = lot.consume(Quantity(amount=Decimal("0.1"), asset=Currency.ETH))
        assert result == Failure(
            InvalidLot(
                reason=(
                    f"Lot.consume: amount asset {Currency.ETH!r} does not match "
                    f"lot asset {Currency.BTC!r}"
                )
            )
        )


class TestLotIsClosed:
    """is_closed reflects the exact zero-remaining condition."""

    def test_open_lot_is_not_closed(self) -> None:
        """A brand-new lot has remaining > 0 and reports is_closed() == False."""
        lot = _btc_lot(remaining=Decimal("0.5"))
        assert lot.is_closed() is False

    def test_zero_remaining_is_closed(self) -> None:
        """A lot with remaining_qty == 0 reports is_closed() == True."""
        lot = _btc_lot(remaining=Decimal("0"), original=Decimal("0.5"))
        assert lot.is_closed() is True
