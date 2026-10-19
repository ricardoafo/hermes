"""Tests for the Position value object.

Invariants protected here: a position is always open (quantity > 0), always
valued at a positive mark with a UTC-aware time, and its value and PnL are
exact products and differences of its fields.

Reference: 308-signal-event-portfolio-state change.md D-5.
"""

from datetime import datetime
from decimal import Decimal

import pytest

from hermes.domain.portfolio.position import Position
from tests.strategies.portfolio import BTC_USDT, T0


def _position(
    *,
    quantity: str = "0.5",
    cost_basis: str = "20000",
    mark_price: str = "50000",
    marked_at: datetime = T0,
) -> Position:
    """Return a BTC/USDT position with the given fields."""
    return Position(
        pair=BTC_USDT,
        quantity=Decimal(quantity),
        cost_basis=Decimal(cost_basis),
        mark_price=Decimal(mark_price),
        marked_at=marked_at,
    )


class TestPositionValuation:
    """Market value and unrealized PnL are exact Decimal arithmetic on the fields."""

    def test_market_value_is_quantity_times_mark(self) -> None:
        """0.5 BTC marked at 50000 is worth exactly 25000."""
        assert _position().market_value == Decimal("25000")

    def test_unrealized_pnl_is_market_value_minus_cost(self) -> None:
        """A 25000 holding bought for 20000 shows 5000 unrealized profit."""
        assert _position().unrealized_pnl == Decimal("5000")

    def test_unrealized_pnl_is_negative_below_cost(self) -> None:
        """A holding marked below its cost shows a negative unrealized PnL."""
        assert _position(mark_price="30000").unrealized_pnl == Decimal("-5000")

    def test_market_value_keeps_digits_beyond_the_default_decimal_precision(self) -> None:
        """A 34-digit product is not rounded to the default 28 digits, so sums stay exact."""
        position = _position(quantity="123456789.12345678", mark_price="987654321.87654321")
        # 123456789.12345678 * 987654321.87654321, written out in full (34 digits).
        assert position.market_value == Decimal("121932631342783101.4583142722374638")


class TestPositionValidation:
    """Invalid positions are refused at construction."""

    @pytest.mark.parametrize("quantity", ["0", "-0.1"])
    def test_non_positive_quantity_is_rejected(self, quantity: str) -> None:
        """A position with no quantity is closed and must not exist."""
        with pytest.raises(ValueError, match="quantity must be > 0"):
            _position(quantity=quantity)

    @pytest.mark.parametrize("mark_price", ["0", "-1"])
    def test_non_positive_mark_is_rejected(self, mark_price: str) -> None:
        """A mark of zero or below cannot value a position."""
        with pytest.raises(ValueError, match="mark_price must be > 0"):
            _position(mark_price=mark_price)

    def test_negative_cost_basis_is_rejected(self) -> None:
        """Cost paid cannot be negative."""
        with pytest.raises(ValueError, match="cost_basis must be >= 0"):
            _position(cost_basis="-1")

    def test_zero_cost_basis_is_accepted(self) -> None:
        """A zero cost is valid (a full-fee rebate case), only negative is refused."""
        assert _position(cost_basis="0").cost_basis == Decimal("0")

    def test_naive_marked_at_is_rejected(self) -> None:
        """A naive mark time raises ValueError naming the UTC requirement."""
        with pytest.raises(ValueError, match="UTC-aware"):
            _position(marked_at=datetime(2026, 10, 1, 0, 0, 0))
