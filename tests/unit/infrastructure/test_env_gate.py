"""Env-gate helper — dev/sandbox/prod matrix + error shape.

Every test protects one invariant of the env-gate contract established
in :mod:`hermes.infrastructure.env_gate`. Failures here mean an adapter
could be wired in the wrong runtime tier without raising — a real-capital
risk for mainnet-scoped adapters.
"""

from __future__ import annotations

import pytest

from hermes.domain.errors import HermesError, InfraError
from hermes.infrastructure.env_gate import EnvGateError, require_env

# ---------------------------------------------------------------------------
# Happy paths — allowed tier passes without raising
# ---------------------------------------------------------------------------


def test_require_env_passes_when_current_is_in_allowed_singleton() -> None:
    """A singleton allowed tuple containing current returns None (no raise)."""
    # The function returns None on success — we assert the call does not
    # raise. Using `is None` would over-specify the API; the contract is
    # "no exception".
    require_env(current="prod", allowed=("prod",), component="binance-mainnet")


def test_require_env_passes_when_current_is_first_of_multiple_allowed() -> None:
    """Current tier matching the FIRST element of allowed does not raise."""
    require_env(current="dev", allowed=("dev", "sandbox"), component="fake-clock")


def test_require_env_passes_when_current_is_last_of_multiple_allowed() -> None:
    """Current tier matching the LAST element of allowed does not raise."""
    require_env(current="sandbox", allowed=("dev", "sandbox"), component="fake-clock")


# ---------------------------------------------------------------------------
# Rejection paths — mismatched tier raises with a message that names things
# ---------------------------------------------------------------------------


def test_require_env_rejects_dev_when_only_prod_is_allowed() -> None:
    """dev-tier caller against a prod-only component raises EnvGateError."""
    with pytest.raises(EnvGateError) as excinfo:
        require_env(current="dev", allowed=("prod",), component="binance-mainnet")
    msg = str(excinfo.value)
    # The message must name (a) the component, (b) the allowed tuple, and
    # (c) the current tier so the operator can fix the misconfig without
    # reading source. Loose-match all three.
    assert "binance-mainnet" in msg
    assert "'prod'" in msg
    assert "'dev'" in msg


def test_require_env_rejects_sandbox_when_only_prod_is_allowed() -> None:
    """sandbox-tier caller against a prod-only component raises EnvGateError."""
    with pytest.raises(EnvGateError) as excinfo:
        require_env(current="sandbox", allowed=("prod",), component="kraken-mainnet")
    assert "kraken-mainnet" in str(excinfo.value)
    assert "'sandbox'" in str(excinfo.value)


def test_require_env_rejects_prod_when_only_dev_and_sandbox_allowed() -> None:
    """prod-tier caller against a non-prod-only component raises EnvGateError.

    Enforces the reverse direction: adapters explicitly SCOPED to
    non-prod (e.g. a fake-clock adapter) must reject prod wiring so an
    accidental swap in dependency-injection code fails at startup.
    """
    with pytest.raises(EnvGateError) as excinfo:
        require_env(
            current="prod",
            allowed=("dev", "sandbox"),
            component="fake-clock",
        )
    assert "fake-clock" in str(excinfo.value)
    assert "'prod'" in str(excinfo.value)


def test_require_env_rejects_unknown_tier_string() -> None:
    """An arbitrary string not in allowed raises — no implicit allow-any."""
    with pytest.raises(EnvGateError) as excinfo:
        require_env(current="staging", allowed=("prod",), component="mainnet-adapter")
    assert "'staging'" in str(excinfo.value)


def test_require_env_rejects_empty_string_current() -> None:
    """An empty current tier string still raises — no falsy shortcut."""
    with pytest.raises(EnvGateError):
        require_env(current="", allowed=("prod",), component="mainnet-adapter")


def test_require_env_rejects_when_allowed_is_empty_tuple() -> None:
    """An empty allowed tuple rejects every current — no membership possible."""
    # Guards against a misconfigured adapter that accidentally passes
    # allowed=() — the rejection is universal, so the operator sees a
    # loud failure instead of a silently-permitted call.
    with pytest.raises(EnvGateError) as excinfo:
        require_env(current="prod", allowed=(), component="broken-adapter")
    assert "broken-adapter" in str(excinfo.value)


# ---------------------------------------------------------------------------
# EnvGateError shape — subclasses InfraError + HermesError, formats nicely
# ---------------------------------------------------------------------------


def test_env_gate_error_is_infra_error() -> None:
    """EnvGateError sits under InfraError so except-InfraError catches it.

    Adapters commonly catch InfraError to convert to a Result at the
    application-layer boundary. EnvGateError must ride that same
    hierarchy so the boundary translation stays uniform.
    """
    err = EnvGateError(msg="test-component only in prod")
    assert isinstance(err, InfraError)
    assert isinstance(err, HermesError)


def test_env_gate_error_str_includes_prefix() -> None:
    """__str__ prefixes with 'Env gate error:' so log grep is deterministic."""
    err = EnvGateError(msg="binance-mainnet only in ('prod',); got 'dev'")
    assert str(err) == "Env gate error: binance-mainnet only in ('prod',); got 'dev'"


def test_env_gate_error_is_frozen_dataclass() -> None:
    """EnvGateError instances are hashable + immutable — mutation raises."""
    err = EnvGateError(msg="original")
    # frozen=True on the dataclass makes attribute reassignment raise.
    with pytest.raises((AttributeError, TypeError)):
        err.msg = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Full dev/sandbox/prod x allowed-tier matrix — parametric sanity check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "allowed", "expect_raise"),
    [
        # allowed=(prod,) — the mainnet-adapter shape
        ("dev", ("prod",), True),
        ("sandbox", ("prod",), True),
        ("prod", ("prod",), False),
        # allowed=(dev, sandbox) — the testnet/fake-adapter shape
        ("dev", ("dev", "sandbox"), False),
        ("sandbox", ("dev", "sandbox"), False),
        ("prod", ("dev", "sandbox"), True),
        # allowed=(dev, sandbox, prod) — the "anywhere" shape (rare)
        ("dev", ("dev", "sandbox", "prod"), False),
        ("sandbox", ("dev", "sandbox", "prod"), False),
        ("prod", ("dev", "sandbox", "prod"), False),
    ],
)
def test_require_env_dev_sandbox_prod_matrix(
    current: str, allowed: tuple[str, ...], expect_raise: bool
) -> None:
    """Every cell of the 3-tier x 3-allowed-shape matrix behaves as expected."""
    if expect_raise:
        with pytest.raises(EnvGateError):
            require_env(current=current, allowed=allowed, component="matrix-test")
    else:
        require_env(current=current, allowed=allowed, component="matrix-test")
