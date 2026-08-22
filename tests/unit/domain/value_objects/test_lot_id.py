"""Property and example-based tests for LotId.

Reference: R-5 (A-5.3, A-5.6, A-5.7).
"""

from __future__ import annotations

from hypothesis import given
from returns.result import Failure, Success

import tests.strategies.lot_id  # noqa: F401
from hermes.domain.value_objects.lot_id import LotId
from tests.strategies.lot_id import lot_id_strategy


class TestLotIdNew:
    """LotId.new() generates valid ULIDs."""

    def test_new_produces_26_chars(self) -> None:
        """LotId.new() always produces a 26-character ULID string."""
        lot_id = LotId.new()
        assert len(lot_id.value) == 26

    def test_new_produces_valid_crockford(self) -> None:
        """LotId.new() produces a valid Crockford base32 ULID that passes from_str validation."""
        lot_id = LotId.new()
        result = LotId.from_str(lot_id.value)
        assert isinstance(result, Success)

    def test_new_produces_unique_ids(self) -> None:
        """LotId.new() generates unique IDs — 10 consecutive calls produce 10 distinct values."""
        ids = {LotId.new().value for _ in range(10)}
        assert len(ids) == 10


class TestLotIdFromStr:
    """from_str() validates Crockford base32 format."""

    @given(lot_id_strategy())
    def test_from_str_roundtrip(self, lid: LotId) -> None:
        """from_str(lid.value) round-trips losslessly for all valid LotId values."""
        result = LotId.from_str(lid.value)
        assert isinstance(result, Success)
        assert result.unwrap() == lid

    def test_from_str_valid_ulid(self) -> None:
        """from_str accepts a well-known valid ULID string and returns Success."""
        # Known valid ULID (26 Crockford base32 chars)
        result = LotId.from_str("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        assert isinstance(result, Success)

    def test_from_str_wrong_length_fails(self) -> None:
        """from_str returns Failure when the input is shorter than 26 characters."""
        result = LotId.from_str("TOOSHORT")
        assert isinstance(result, Failure)

    def test_from_str_invalid_chars_fails(self) -> None:
        """from_str returns Failure when the input contains chars absent from Crockford base32."""
        # Contains 'I', 'L', 'O', 'U' — excluded from Crockford base32
        result = LotId.from_str("IIIIIIIIIIIIIIIIIIIIIIIIII")
        assert isinstance(result, Failure)

    def test_from_str_lowercase_accepted(self) -> None:
        """from_str normalises to uppercase before validation."""
        result = LotId.from_str("01arz3ndektsv4rrffq69g5fav")
        assert isinstance(result, Success)

    def test_str_returns_value(self) -> None:
        """str(LotId) returns the raw ULID string value."""
        lid = LotId.new()
        assert str(lid) == lid.value
