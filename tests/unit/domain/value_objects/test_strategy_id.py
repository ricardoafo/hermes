"""Tests for the :class:`StrategyId` value object.

Verifies the class-level invariants documented on :class:`StrategyId`:

- Non-empty string, max 64 chars, printable ASCII, no whitespace.
- Construction succeeds with valid inputs.
- Construction raises ValueError for empty, too-long, whitespace, or
  non-printable inputs.
- Value equality semantics (frozen dataclass).
- __str__ returns the raw value.

Reference: spec-1-domain-aggregates, code-conventions.md §3.
"""

import pytest

from hermes.domain.value_objects.strategy_id import StrategyId


class TestStrategyIdConstruction:
    """Valid inputs are accepted without raising."""

    def test_simple_name_accepted(self) -> None:
        """A plain ASCII strategy name constructs successfully."""
        sid = StrategyId("swing")
        assert sid.value == "swing"

    def test_dotted_name_accepted(self) -> None:
        """Dotted strategy names (e.g. swing.v1) are valid."""
        sid = StrategyId("swing.v1")
        assert sid.value == "swing.v1"

    def test_max_length_accepted(self) -> None:
        """A 64-character value is within bounds."""
        value = "a" * 64
        sid = StrategyId(value)
        assert sid.value == value

    def test_punctuation_accepted(self) -> None:
        """Printable punctuation like hyphens and underscores are valid."""
        sid = StrategyId("swing-v1_btc")
        assert sid.value == "swing-v1_btc"


class TestStrategyIdInvariants:
    """Invalid inputs raise ValueError with a descriptive message."""

    def test_empty_string_raises(self) -> None:
        """Empty StrategyId is meaningless — must be rejected."""
        with pytest.raises(ValueError, match="empty"):
            StrategyId("")

    def test_too_long_raises(self) -> None:
        """Values longer than 64 chars exceed the allowed maximum."""
        with pytest.raises(ValueError, match="64"):
            StrategyId("a" * 65)

    def test_space_raises(self) -> None:
        """Whitespace in strategy ids is disallowed (no whitespace rule)."""
        with pytest.raises(ValueError, match="whitespace"):
            StrategyId("swing v1")

    def test_tab_raises(self) -> None:
        """Tab character is whitespace — must be rejected."""
        with pytest.raises(ValueError, match="whitespace"):
            StrategyId("swing\tv1")

    def test_newline_raises(self) -> None:
        """Newline character is whitespace — must be rejected."""
        with pytest.raises(ValueError, match="whitespace"):
            StrategyId("swing\nv1")

    def test_non_printable_ascii_raises(self) -> None:
        """Control characters (non-printable) are not allowed."""
        with pytest.raises(ValueError):
            StrategyId("swing\x00v1")


class TestStrategyIdEquality:
    """StrategyId is a frozen dataclass — equality is by value."""

    def test_equal_values_compare_equal(self) -> None:
        """Two StrategyId instances with the same value are equal."""
        assert StrategyId("swing") == StrategyId("swing")

    def test_different_values_compare_unequal(self) -> None:
        """Two StrategyId instances with different values are not equal."""
        assert StrategyId("swing") != StrategyId("balancer")

    def test_frozen_mutation_raises(self) -> None:
        """StrategyId is frozen — mutation attempt raises."""
        sid = StrategyId("swing")
        with pytest.raises((AttributeError, TypeError)):
            sid.value = "changed"  # type: ignore[misc]


class TestStrategyIdStr:
    """__str__ exposes the raw value for display and logging."""

    def test_str_returns_value(self) -> None:
        """str(StrategyId('swing')) == 'swing'."""
        assert str(StrategyId("swing")) == "swing"
