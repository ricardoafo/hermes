"""Tests that pin FIFO-lot invariants the weekly mutation run showed were unguarded (#406).

ADR-007 asks for every mutant of the FIFO calculator to be killed: it is the
tax-critical path. Each test below names the invariant it protects; together
they kill the mutants that survived the first cosmic-ray run on
``src/hermes/domain/lots`` (comparisons tested in one sort order only, an
exact-inventory sell, the keyword-only API, immutability, and the
ValueError-to-Failure conversion).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from returns.result import Failure, Success

from hermes.domain.errors import InvalidLot
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.lots.selection import InsufficientInventory, select_lots_fifo
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_SWING = StrategyId("swing")
_ID_A = LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1")
_ID_B = LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH2")


def _eur(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.EUR)


def _lot(
    asset: Currency,
    remaining: str,
    *,
    lot_id: LotId = _ID_A,
    original: str | None = None,
    opened_at: datetime = _T0,
) -> Lot:
    return Lot(
        lot_id=lot_id,
        asset=asset,
        original_qty=Quantity(amount=Decimal(original or remaining), asset=asset),
        remaining_qty=Quantity(amount=Decimal(remaining), asset=asset),
        cost_basis_per_unit=_eur("100"),
        strategy_id=_SWING,
        opened_at=opened_at,
    )


# ---------------------------------------------------------------------------
# Asset and currency checks must reject a mismatch in either sort order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lot_asset", "other"),
    [(Currency.ETH, Currency.BTC), (Currency.BTC, Currency.ETH)],
)
class TestAssetMismatchBothOrders:
    """A different asset is rejected whether it sorts before or after the lot's."""

    # SEV-1: FIFO tax lots must never mix assets (ADR-003 §03)
    def test_original_qty_in_another_asset_raises(
        self, lot_asset: Currency, other: Currency
    ) -> None:
        """original_qty must be in the lot's asset."""
        with pytest.raises(ValueError, match="original_qty asset"):
            Lot(
                lot_id=_ID_A,
                asset=lot_asset,
                original_qty=Quantity(amount=Decimal("1"), asset=other),
                remaining_qty=Quantity(amount=Decimal("1"), asset=lot_asset),
                cost_basis_per_unit=_eur("1"),
                strategy_id=_SWING,
                opened_at=_T0,
            )

    # SEV-1: FIFO tax lots must never mix assets (ADR-003 §03)
    def test_remaining_qty_in_another_asset_raises(
        self, lot_asset: Currency, other: Currency
    ) -> None:
        """remaining_qty must be in the lot's asset."""
        with pytest.raises(ValueError, match="remaining_qty asset"):
            Lot(
                lot_id=_ID_A,
                asset=lot_asset,
                original_qty=Quantity(amount=Decimal("1"), asset=lot_asset),
                remaining_qty=Quantity(amount=Decimal("1"), asset=other),
                cost_basis_per_unit=_eur("1"),
                strategy_id=_SWING,
                opened_at=_T0,
            )

    # SEV-1: consuming another asset would corrupt the FIFO ledger (ADR-003 §03)
    def test_consume_in_another_asset_fails(self, lot_asset: Currency, other: Currency) -> None:
        """Lot.consume refuses an amount in a different asset."""
        result = _lot(lot_asset, "1").consume(Quantity(amount=Decimal("0.1"), asset=other))
        assert isinstance(result, Failure)
        assert "does not match" in str(result.failure())


@pytest.mark.parametrize("currency", [Currency.BTC, Currency.USDT])
class TestEurOnlyBothOrders:
    """EUR checks reject currencies sorting before ("BTC") and after ("USDT") "EUR"."""

    # SEV-1: Spanish FIFO gains must be computed in EUR (ADR-002, ADR-003 §03)
    def test_cost_basis_must_be_eur(self, currency: Currency) -> None:
        """A lot's cost basis is EUR."""
        with pytest.raises(ValueError, match="EUR"):
            Lot(
                lot_id=_ID_A,
                asset=Currency.SOL,
                original_qty=Quantity(amount=Decimal("1"), asset=Currency.SOL),
                remaining_qty=Quantity(amount=Decimal("1"), asset=Currency.SOL),
                cost_basis_per_unit=Money(amount=Decimal("1"), currency=currency),
                strategy_id=_SWING,
                opened_at=_T0,
            )

    # SEV-1: Spanish FIFO gains must be computed in EUR (ADR-002, ADR-003 §03)
    def test_realized_gain_must_be_eur(self, currency: Currency) -> None:
        """A consumption's realized gain is EUR."""
        with pytest.raises(ValueError, match="EUR"):
            LotConsumption(
                lot_id=_ID_A,
                consumed_qty=Quantity(amount=Decimal("1"), asset=Currency.SOL),
                realized_gain=Money(amount=Decimal("1"), currency=currency),
                consumed_at=_T0,
            )

    # SEV-1: Spanish FIFO gains must be computed in EUR (ADR-002, ADR-003 §03)
    def test_sell_price_must_be_eur(self, currency: Currency) -> None:
        """record() refuses a non-EUR sell price."""
        result = LotConsumption.record(
            lot_id=_ID_A,
            consumed_qty=Quantity(amount=Decimal("1"), asset=Currency.SOL),
            sell_price_per_unit=Money(amount=Decimal("1"), currency=currency),
            lot_cost_basis_per_unit=_eur("1"),
            consumed_at=_T0,
        )
        assert isinstance(result, Failure)
        assert "sell_price_per_unit must be EUR" in str(result.failure())

    # SEV-1: Spanish FIFO gains must be computed in EUR (ADR-002, ADR-003 §03)
    def test_cost_basis_input_must_be_eur(self, currency: Currency) -> None:
        """record() refuses a non-EUR cost basis."""
        result = LotConsumption.record(
            lot_id=_ID_A,
            consumed_qty=Quantity(amount=Decimal("1"), asset=Currency.SOL),
            sell_price_per_unit=_eur("1"),
            lot_cost_basis_per_unit=Money(amount=Decimal("1"), currency=currency),
            consumed_at=_T0,
        )
        assert isinstance(result, Failure)
        assert "lot_cost_basis_per_unit must be EUR" in str(result.failure())


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


# SEV-1: a SELL must only draw lots of its own asset (ADR-003 §03)
def test_selection_ignores_lots_of_an_asset_sorting_lower() -> None:
    """Selling ETH never touches BTC lots, even though "BTC" sorts before "ETH"."""
    btc = _lot(Currency.BTC, "5", lot_id=_ID_A)
    eth = _lot(Currency.ETH, "1", lot_id=_ID_B, opened_at=_T0 + timedelta(days=1))
    result = select_lots_fifo(
        [btc, eth],
        Quantity(amount=Decimal("1"), asset=Currency.ETH),
        _eur("150"),
        _T0 + timedelta(days=1),
    )
    assert isinstance(result, Success)
    assert [c.lot_id for c in result.unwrap()] == [_ID_B]


def test_selling_exactly_the_inventory_succeeds() -> None:
    """Total inventory equal to the request is enough (the check is strict <)."""
    result = select_lots_fifo(
        [_lot(Currency.BTC, "2")], Quantity(amount=Decimal("2"), asset=Currency.BTC), _eur("1"), _T0
    )
    assert isinstance(result, Success)
    assert result.unwrap()[0].consumed_qty.amount == Decimal("2")


def test_exact_drain_stops_before_later_lots() -> None:
    """When the first lot covers the sell exactly, later lots are not touched."""
    first = _lot(Currency.BTC, "1", lot_id=_ID_A)
    later = _lot(Currency.BTC, "1", lot_id=_ID_B, opened_at=_T0 + timedelta(days=1))
    result = select_lots_fifo(
        [first, later], Quantity(amount=Decimal("1"), asset=Currency.BTC), _eur("1"), _T0
    )
    assert isinstance(result, Success)
    assert [c.lot_id for c in result.unwrap()] == [_ID_A]


def test_insufficient_inventory_reports_the_shortfall() -> None:
    """A request above inventory fails with both quantities."""
    result = select_lots_fifo(
        [_lot(Currency.BTC, "1")],
        Quantity(amount=Decimal("1.5"), asset=Currency.BTC),
        _eur("1"),
        _T0,
    )
    assert isinstance(result, Failure)
    error = result.failure()
    assert isinstance(error, InsufficientInventory)
    assert (error.requested.amount, error.available.amount) == (Decimal("1.5"), Decimal("1"))


# ---------------------------------------------------------------------------
# Lot arithmetic, factories and API shape
# ---------------------------------------------------------------------------


def test_consume_subtracts_from_remaining() -> None:
    """Consuming 0.4 of 1.0 leaves 0.6 (subtraction, not remainder or anything else)."""
    result = _lot(Currency.BTC, "1.0").consume(Quantity(amount=Decimal("0.4"), asset=Currency.BTC))
    assert isinstance(result, Success)
    assert result.unwrap().remaining_qty.amount == Decimal("0.6")


def test_open_converts_invariant_errors_to_failure() -> None:
    """Lot.open returns Failure, not an exception, when the constructor rejects input."""
    result = Lot.open(
        lot_id=_ID_A,
        asset=Currency.BTC,
        qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
        cost_basis_per_unit=Money(amount=Decimal("1"), currency=Currency.USDT),
        strategy_id=_SWING,
        opened_at=_T0,
    )
    assert result == Failure(
        InvalidLot(
            reason=(f"Lot.cost_basis_per_unit must be in EUR (ADR-003 §03), got {Currency.USDT!r}")
        )
    )


def test_record_converts_invariant_errors_to_failure() -> None:
    """LotConsumption.record returns Failure, not an exception, for a zero quantity."""
    result = LotConsumption.record(
        lot_id=_ID_A,
        consumed_qty=Quantity(amount=Decimal("0"), asset=Currency.BTC),
        sell_price_per_unit=_eur("2"),
        lot_cost_basis_per_unit=_eur("1"),
        consumed_at=_T0,
    )
    assert isinstance(result, Failure)
    assert "consumed_qty must be > 0" in str(result.failure())


# SEV-1: positional prices could be swapped silently and invert a taxable gain
def test_record_takes_keyword_arguments_only() -> None:
    """record() rejects positional arguments, so sell price and cost basis can't be swapped."""
    record: Callable[..., object] = LotConsumption.record
    with pytest.raises(TypeError):
        record(
            _ID_A,
            Quantity(amount=Decimal("1"), asset=Currency.BTC),
            _eur("2"),
            _eur("1"),
            _T0,
        )


def test_open_takes_keyword_arguments_only() -> None:
    """Lot.open rejects positional arguments."""
    open_lot: Callable[..., object] = Lot.open
    with pytest.raises(TypeError):
        open_lot(
            _ID_A,
            Currency.BTC,
            Quantity(amount=Decimal("1"), asset=Currency.BTC),
            _eur("1"),
            _SWING,
            _T0,
        )
