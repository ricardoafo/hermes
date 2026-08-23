"""Domain and infrastructure error hierarchy.

Defines the sealed ``HermesError`` root and its two subtrees:

- ``DomainError`` — expected outcomes returned as ``Failure`` payloads in domain
  ``Result`` returns (§2 code-conventions).
- ``InfraError`` — exceptional I/O failures raised by adapters (§2 code-conventions).

Reference: code-conventions.md §2 — Error handling — hybrid Result/exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CurrencyMismatch",
    "DomainError",
    "ExchangeError",
    "FxRateError",
    "HealthchecksError",
    "HermesError",
    "InfraError",
    "InvalidLot",
    "LedgerError",
    "NetworkError",
]


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


class HermesError(Exception):
    """Root of the hermes exception hierarchy.

    All custom exceptions in the project extend this class so callers can
    catch everything-hermes with a single ``except HermesError``.
    """


class DomainError(HermesError):
    """Base for domain-layer expected-outcome errors.

    These are NOT bugs — they represent invalid business states (e.g. currency
    mismatch, invalid lot). Domain functions return them as ``Failure`` payloads
    inside ``Result[T, DomainError]``. Never raise them directly in domain code.

    Reference: code-conventions.md §2.
    """


class InfraError(HermesError):
    """Base for infrastructure-layer exceptional errors.

    These represent I/O failures (network drops, exchange 5xx, disk full).
    Adapters raise them; the application layer catches and converts to ``Result``.

    Reference: code-conventions.md §2.
    """


# ---------------------------------------------------------------------------
# DomainError subclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CurrencyMismatch(DomainError):
    """Binary operation attempted on Money values with different currencies.

    Returned as ``Failure(CurrencyMismatch(...))`` from ``Money.add`` / ``Money.sub``.

    Args:
        expected: Currency that was required for the operation.
        actual: Currency that was actually provided.

    Reference: code-conventions.md §2 and §4.
    """

    expected: str
    actual: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Currency mismatch: expected {self.expected}, got {self.actual}"


@dataclass(frozen=True, slots=True)
class InvalidLot(DomainError):
    """A trading lot is in an invalid state.

    Args:
        reason: Human-readable description of what constraint was violated.

    Reference: code-conventions.md §2.
    """

    reason: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Invalid lot: {self.reason}"


# ---------------------------------------------------------------------------
# InfraError subclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NetworkError(InfraError):
    """Transport-level failure (DNS, TCP, TLS, timeout).

    Raised by adapters when the wire-level call fails before receiving any
    response. The application layer converts this to a ``Failure`` result.

    Args:
        msg: Context from the underlying transport exception.

    Reference: code-conventions.md §2.
    """

    msg: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Network error: {self.msg}"


@dataclass(frozen=True, slots=True)
class ExchangeError(InfraError):
    """Exchange returned an unexpected or 5xx response.

    Raised by exchange adapters when the HTTP call succeeds at the transport
    level but the exchange signals an error (5xx, unexpected schema).

    Args:
        msg: Context from the exchange response.

    Reference: code-conventions.md §2.
    """

    msg: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Exchange error: {self.msg}"


@dataclass(frozen=True, slots=True)
class LedgerError(InfraError):
    """SQLite ledger write or read failure.

    Raised by the ledger adapter when a database operation fails.

    Args:
        msg: Context from the underlying SQLite exception.

    Reference: code-conventions.md §2.
    """

    msg: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Ledger error: {self.msg}"


@dataclass(frozen=True, slots=True)
class HealthchecksError(InfraError):
    """Healthchecks.io ping failed at transport or HTTP layer.

    Wraps wire-level (:class:`httpx.HTTPError`) exceptions and non-2xx HTTP
    responses from the ping URL into one exception surface. Healthchecks
    is a dead-man's-switch / heartbeat monitor — the ping URL is
    stateless, no auth, no JSON body — so this error type is deliberately
    thin (no ``ok=false`` footgun like Slack, no ``error_code`` field
    like Telegram).

    Args:
        msg: Human-readable context — includes HTTP status, response body
            excerpt, or transport error message. The operator needs
            enough context to distinguish "wrong URL" from "Healthchecks
            outage" from "our network is down".

    Reference: code-conventions.md §2, https://healthchecks.io/docs/http_api/.
    """

    msg: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Healthchecks error: {self.msg}"


@dataclass(frozen=True, slots=True)
class FxRateError(InfraError):
    """FX rate provider (Frankfurter, ECB, ...) failed to deliver a usable rate.

    Raised by FX adapters when the wire call succeeds at the transport
    level but the response is unusable — HTTP non-2xx, unparseable JSON,
    or a schema shape that does not contain the requested currency.

    Args:
        msg: Context from the response (status code, offending payload
            excerpt, missing currency name, etc.).

    Reference: code-conventions.md §2,
    docs/decisions/2026-08-23-fx-rate-source-frankfurter.md.
    """

    msg: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"FX rate error: {self.msg}"
