"""Centralised env-flag validation for adapters.

Adapters that touch a scoped surface (mainnet exchange endpoints, prod
notification channels, etc.) call :func:`require_env` at construction
time to fail-fast when they're wired in the wrong runtime tier. The
canonical example: a Binance mainnet adapter accepts only
``allowed=("prod",)``; wiring it in dev or sandbox raises immediately.

Design notes:

- Lives in ``infrastructure`` (NOT ``domain``) so the domain layer stays
  free of runtime-tier concepts. Adapters + application-layer wiring
  code import from here; domain code MUST NOT.
- Uses ``EnvGateError`` (extends :class:`InfraError`) so an adapter's
  ``except InfraError`` catch-all also handles env-gate rejections.
- The helper is a pure function of ``(current, allowed)`` — no I/O, no
  logging, no side effects. Callers own how to react to the raise.

Reference:
``docs/decisions/2026-08-23-credential-storage-layered-by-env.md``
"""

from __future__ import annotations

from dataclasses import dataclass

from hermes.domain.errors import InfraError

__all__ = ["EnvGateError", "require_env"]


@dataclass(frozen=True, slots=True)
class EnvGateError(InfraError):
    """Raised when an adapter is wired in a forbidden runtime tier.

    Fail-fast: the raise happens at adapter construction (or first-use),
    not at the point real capital would move. That means the operator
    sees the misconfig at startup — inside the systemd log — instead of
    at first trade.

    Args:
        msg: Human-readable description including the component name,
            the tier that was actually active, and the tiers that would
            have been acceptable.
    """

    msg: str

    def __str__(self) -> str:
        """Return the wrapped human-readable message with a fixed prefix."""
        return f"Env gate error: {self.msg}"


def require_env(*, current: str, allowed: tuple[str, ...], component: str) -> None:
    """Raise :class:`EnvGateError` if ``current`` env is not in ``allowed``.

    Args:
        current: The active env (from ``HermesConfig.env``).
        allowed: Non-empty tuple of env names this component is permitted
            in (e.g. ``("prod",)`` for a mainnet adapter, or
            ``("dev", "sandbox")`` for a testnet-only adapter).
        component: Human-readable component name for the error message —
            surfaces in logs and helps the operator identify the
            misconfigured adapter without a stack-trace hunt.

    Raises:
        EnvGateError: When ``current`` is not present in ``allowed``. The
            error message names the component, the current tier, and the
            allowed set so operators can fix the misconfig without
            reading source.
    """
    if current not in allowed:
        raise EnvGateError(
            f"{component} is only usable in env(s) {allowed!r}; current env={current!r}"
        )
