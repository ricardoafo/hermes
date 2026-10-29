"""Env-gate helper: which runtime tiers a component may be wired in.

Every test protects one rule of :func:`hermes.infrastructure.env_gate.require_env`
(``docs/decisions/2026-08-23-credential-storage-layered-by-env.md``). A
failure here means an adapter could be wired in the wrong runtime tier
without raising, a real-capital risk for mainnet-scoped adapters.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.errors import InfraError
from hermes.infrastructure.env_gate import EnvGateError, require_env

_PASSED = "passed"
_TIERS = ("dev", "sandbox", "staging", "prod")


def _gate(current: str, allowed: tuple[str, ...], component: str = "matrix-test") -> str:
    """Return ``"passed"`` when the gate lets ``current`` through, else the error text."""
    try:
        require_env(current=current, allowed=allowed, component=component)
    except EnvGateError as exc:
        return str(exc)
    return _PASSED


@pytest.mark.parametrize(
    ("current", "allowed"),
    [
        ("prod", ("prod",)),
        ("dev", ("dev", "sandbox")),
        ("sandbox", ("dev", "sandbox")),
        ("dev", ("dev", "sandbox", "prod")),
        ("sandbox", ("dev", "sandbox", "prod")),
        ("prod", ("dev", "sandbox", "prod")),
    ],
)
def test_require_env_lets_a_listed_tier_through(current: str, allowed: tuple[str, ...]) -> None:
    """A tier listed in ``allowed`` passes the gate, wherever it sits in the tuple."""
    assert _gate(current, allowed) == _PASSED


@pytest.mark.parametrize(
    ("current", "allowed", "component", "expected"),
    [
        (
            "dev",
            ("prod",),
            "binance-mainnet",
            "Env gate error: binance-mainnet is only usable in env(s) ('prod',); current env='dev'",
        ),
        (
            "sandbox",
            ("prod",),
            "kraken-mainnet",
            "Env gate error: kraken-mainnet is only usable in env(s) ('prod',); "
            "current env='sandbox'",
        ),
        (
            "staging",
            ("prod",),
            "mainnet-adapter",
            "Env gate error: mainnet-adapter is only usable in env(s) ('prod',); "
            "current env='staging'",
        ),
        (
            "prod",
            ("dev", "sandbox"),
            "fake-clock",
            "Env gate error: fake-clock is only usable in env(s) ('dev', 'sandbox'); "
            "current env='prod'",
        ),
        (
            "",
            ("prod",),
            "mainnet-adapter",
            "Env gate error: mainnet-adapter is only usable in env(s) ('prod',); current env=''",
        ),
        (
            "prod",
            (),
            "broken-adapter",
            "Env gate error: broken-adapter is only usable in env(s) (); current env='prod'",
        ),
    ],
)
def test_require_env_rejects_an_unlisted_tier_naming_component_allowed_and_current(
    current: str, allowed: tuple[str, ...], component: str, expected: str
) -> None:
    """An unlisted tier raises; the text names the component, allowed set and current tier."""
    assert _gate(current, allowed, component) == expected


@given(
    current=st.sampled_from(_TIERS),
    allowed=st.lists(st.sampled_from(_TIERS), unique=True, max_size=4).map(tuple),
)
def test_require_env_passes_exactly_when_the_tier_is_listed(
    current: str, allowed: tuple[str, ...]
) -> None:
    """For any tier and allowed set, the gate passes if and only if the tier is listed."""
    assert (_gate(current, allowed) == _PASSED) is (current in allowed)


def test_env_gate_rejection_is_caught_by_an_infra_error_handler() -> None:
    """An adapter's ``except InfraError`` catch-all also catches an env-gate rejection."""
    with pytest.raises(InfraError, match=r"^Env gate error: binance-mainnet is only usable"):
        require_env(current="dev", allowed=("prod",), component="binance-mainnet")
