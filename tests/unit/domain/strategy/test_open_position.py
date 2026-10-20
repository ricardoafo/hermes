"""Tests for the ``OpenPosition`` value object.

Invariants protected here: a restored position carries a UTC-aware entry time
and a positive entry price, so a bad ledger row cannot seed a nonsense state.

Reference: 309-serve-bar-loop change.md D-6.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.strategy.signal import OpenPosition


def test_open_position_keeps_its_fields() -> None:
    """A valid position exposes the entry time and price it was built with."""
    when = datetime(2026, 10, 2, 16, tzinfo=UTC)

    position = OpenPosition(entry_time=when, entry_price=Decimal("65000.5"))

    assert (position.entry_time, position.entry_price) == (when, Decimal("65000.5"))


def test_open_position_rejects_a_naive_entry_time() -> None:
    """A naive entry time is refused."""
    with pytest.raises(ValueError, match="UTC-aware"):
        OpenPosition(entry_time=datetime(2026, 10, 2, 16), entry_price=Decimal("1"))


@pytest.mark.parametrize("price", ["0", "-1"])
def test_open_position_rejects_a_non_positive_entry_price(price: str) -> None:
    """A zero or negative entry price is refused."""
    with pytest.raises(ValueError, match="entry_price"):
        OpenPosition(entry_time=datetime(2026, 10, 2, 16, tzinfo=UTC), entry_price=Decimal(price))


def test_open_position_has_no_stop_unless_one_is_restored() -> None:
    """Without a stored stop the position carries none; a restored stop is kept as given."""
    when = datetime(2026, 10, 2, 16, tzinfo=UTC)

    plain = OpenPosition(entry_time=when, entry_price=Decimal("65000.5"))
    restored = OpenPosition(
        entry_time=when, entry_price=Decimal("65000.5"), last_stop=Decimal("63000.25")
    )

    assert (plain.last_stop, restored.last_stop) == (None, Decimal("63000.25"))
