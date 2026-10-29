"""Property and example-based tests for LotId.

Reference: R-5 (A-5.3, A-5.6, A-5.7).
"""

import pytest
from hypothesis import given
from returns.result import Success

import tests.strategies.lot_id  # noqa: F401
from hermes.domain.errors import InvalidLot
from hermes.domain.value_objects.lot_id import LotId
from tests.strategies.lot_id import lot_id_strategy


class TestLotIdNew:
    """LotId.new() generates valid ULIDs."""

    def test_new_produces_26_crockford_chars(self) -> None:
        """LotId.new() yields 26 characters, all from the Crockford alphabet (no I, L, O, U)."""
        value = LotId.new().value
        assert len(value) == 26
        assert set(value) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")

    def test_new_ids_sort_in_creation_order(self) -> None:
        """IDs minted back to back, mostly in the same millisecond, sort in the order made.

        FIFO lot selection orders lots by ID, so two lots opened in one
        millisecond must not swap places.
        """
        ids = [LotId.new().value for _ in range(1000)]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)

    def test_id_minted_by_the_old_generator_keeps_its_value(self) -> None:
        """A lot ID already in a ledger, minted by the former ULID library, parses unchanged."""
        stored = "01K6V3J0A4ZQ7X9M2C8D5E1F0G"
        assert LotId.from_str(stored).unwrap().value == stored


class TestLotIdFromStr:
    """from_str() validates Crockford base32 format."""

    @given(lot_id_strategy())
    def test_from_str_roundtrip(self, lid: LotId) -> None:
        """from_str(lid.value) round-trips losslessly for all valid LotId values."""
        result = LotId.from_str(lid.value)
        assert isinstance(result, Success)
        assert result.unwrap() == lid

    def test_from_str_valid_ulid(self) -> None:
        """from_str accepts a well-known valid ULID and keeps its value unchanged."""
        assert LotId.from_str("01ARZ3NDEKTSV4RRFFQ69G5FAV").unwrap().value == (
            "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        )

    @pytest.mark.parametrize(
        "text", ["TOOSHORT", "01ARZ3NDEKTSV4RRFFQ69G5FA", "01ARZ3NDEKTSV4RRFFQ69G5FAVX"]
    )
    def test_from_str_wrong_length_fails(self, text: str) -> None:
        """from_str rejects 8, 25 and 27 characters: only exactly 26 is a ULID."""
        error = LotId.from_str(text).failure()
        assert isinstance(error, InvalidLot)
        assert "must be 26 characters" in error.reason

    def test_from_str_invalid_chars_fails(self) -> None:
        """from_str rejects 26 x "I" (not in Crockford base32) and names the alphabet."""
        error = LotId.from_str("IIIIIIIIIIIIIIIIIIIIIIIIII").failure()
        assert isinstance(error, InvalidLot)
        assert "invalid Crockford base32 characters" in error.reason

    def test_from_str_lowercase_accepted(self) -> None:
        """from_str normalises to uppercase: the stored value is the upper-case form."""
        assert LotId.from_str("01arz3ndektsv4rrffq69g5fav").unwrap().value == (
            "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        )

    def test_from_str_strips_surrounding_whitespace(self) -> None:
        """A ULID read from a padded ledger field parses to the bare value."""
        assert LotId.from_str("  01ARZ3NDEKTSV4RRFFQ69G5FAV\n").unwrap().value == (
            "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        )

    def test_str_returns_value(self) -> None:
        """str(LotId) returns the raw ULID string value."""
        lid = LotId.new()
        assert str(lid) == lid.value
