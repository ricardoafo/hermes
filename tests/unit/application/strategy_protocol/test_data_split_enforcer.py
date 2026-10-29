"""Unit tests for certification_context and is_certification_mode.

Tests cover:
- certification_context restores False on exit (no context leak).
- is_certification_mode() reflects the context variable correctly.
- A frozen DomainError raised inside the block propagates unchanged.
"""

import pytest

from hermes.application.strategy_protocol.data_split_enforcer import (
    certification_context,
    is_certification_mode,
)
from hermes.domain.errors import OOSDataLeakageError

# ---------------------------------------------------------------------------
# Tests — is_certification_mode
# ---------------------------------------------------------------------------


class TestIsCertificationMode:
    """is_certification_mode() reflects the ContextVar state."""

    def test_false_outside_context(self) -> None:
        """is_certification_mode() must return False when no certification_context is active."""
        assert is_certification_mode() is False, (
            "is_certification_mode() must return False outside certification_context()."
        )

    def test_true_inside_context(self) -> None:
        """is_certification_mode() must return True inside certification_context()."""
        with certification_context():
            assert is_certification_mode() is True, (
                "is_certification_mode() must return True inside certification_context()."
            )

    def test_restored_after_context_exit(self) -> None:
        """certification_context() must restore False on __exit__ (no context leak)."""
        with certification_context():
            pass
        assert is_certification_mode() is False, (
            "is_certification_mode() must be restored to False after certification_context() exits."
        )


def test_frozen_domain_error_raised_inside_certification_context_propagates() -> None:
    """A frozen DomainError raised in the block propagates as itself, not FrozenInstanceError."""
    with pytest.raises(OOSDataLeakageError) as exc, certification_context():
        raise OOSDataLeakageError(attempted_timestamp="t", oos_start="s")
    assert exc.value.attempted_timestamp == "t"
    assert is_certification_mode() is False
