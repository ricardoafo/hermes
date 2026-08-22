"""Tests for the :class:`LotConsumption` aggregate.

Verifies:

- Realized gain is computed as ``(sell_price - cost_basis) * consumed_qty``.
- ``realized_gain.currency`` must be EUR (ADR-003 §03).
- ``consumed_qty`` must be > 0.
- ``consumed_at`` must be timezone-aware.
- ``record`` rejects non-EUR sell price or non-EUR cost basis.
- Negative gains (losses) survive round-trip.

Reference: ADR-003 §03, code-conventions.md §1 + §3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from returns.result import Failure, Success

from hermes.domain.errors import InvalidLot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity


class TestLotConsumptionConstruction:
    """Constructor enforces EUR-only gain, non-zero qty, tz-aware time."""

    def test_non_eur_realized_gain_raises(self) -> None:
        """realized_gain in a non-EUR currency violates ADR-003 §03."""
        with pytest.raises(ValueError, match="EUR"):
            LotConsumption(
                lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
                consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                realized_gain=Money(amount=Decimal("10"), currency=Currency.USDT),
                consumed_at=datetime(2027, 3, 20, tzinfo=UTC),
            )

    def test_zero_consumed_qty_raises(self) -> None:
        """A zero consumption is meaningless — no row should ever be recorded."""
        with pytest.raises(ValueError, match="must be > 0"):
            LotConsumption(
                lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
                consumed_qty=Quantity(amount=Decimal("0"), asset=Currency.BTC),
                realized_gain=Money(amount=Decimal("0"), currency=Currency.EUR),
                consumed_at=datetime(2027, 3, 20, tzinfo=UTC),
            )

    def test_naive_consumed_at_raises(self) -> None:
        """consumed_at must be timezone-aware — code-conventions §9."""
        with pytest.raises(ValueError, match="timezone-aware"):
            LotConsumption(
                lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
                consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                realized_gain=Money(amount=Decimal("10"), currency=Currency.EUR),
                consumed_at=datetime(2027, 3, 20, 12, 0, 0),  # naive
            )


class TestLotConsumptionRecord:
    """record computes realized gain and enforces EUR pricing."""

    def test_record_computes_gain_from_price_delta(self) -> None:
        """realized_gain = (sell_price - cost_basis) * consumed_qty, in EUR."""
        result = LotConsumption.record(
            lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
            consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            sell_price_per_unit=Money(amount=Decimal("75000"), currency=Currency.EUR),
            lot_cost_basis_per_unit=Money(amount=Decimal("65000"), currency=Currency.EUR),
            consumed_at=datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert isinstance(result, Success)
        consumption = result.unwrap()
        # (75000 - 65000) * 0.001 = 10 EUR — the ADR-003 §03 worked example.
        assert consumption.realized_gain == Money(amount=Decimal("10.000"), currency=Currency.EUR)

    def test_record_supports_losses(self) -> None:
        """A sell below cost basis produces a negative realized_gain."""
        result = LotConsumption.record(
            lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
            consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            sell_price_per_unit=Money(amount=Decimal("60000"), currency=Currency.EUR),
            lot_cost_basis_per_unit=Money(amount=Decimal("65000"), currency=Currency.EUR),
            consumed_at=datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert isinstance(result, Success)
        # (60000 - 65000) * 0.001 = -5 EUR
        assert result.unwrap().realized_gain.amount == Decimal("-5.000")

    def test_record_rejects_non_eur_sell_price(self) -> None:
        """Sell price in USDT/BTC/anything-non-EUR returns Failure(InvalidLot)."""
        result = LotConsumption.record(
            lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
            consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            sell_price_per_unit=Money(amount=Decimal("75000"), currency=Currency.USDT),
            lot_cost_basis_per_unit=Money(amount=Decimal("65000"), currency=Currency.EUR),
            consumed_at=datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), InvalidLot)

    def test_record_rejects_non_eur_cost_basis(self) -> None:
        """Cost basis in a non-EUR currency returns Failure(InvalidLot)."""
        result = LotConsumption.record(
            lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
            consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            sell_price_per_unit=Money(amount=Decimal("75000"), currency=Currency.EUR),
            lot_cost_basis_per_unit=Money(amount=Decimal("65000"), currency=Currency.USDT),
            consumed_at=datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), InvalidLot)
