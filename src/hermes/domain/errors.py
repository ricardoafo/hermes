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
    "DataManifestFormatError",
    "DatasetHashMismatchError",
    "DatasetNotPinnedError",
    "DomainError",
    "ExchangeError",
    "FxRateError",
    "HealthchecksError",
    "HermesError",
    "InfraError",
    "InvalidLot",
    "InvalidPortfolioTransition",
    "KrakenPublicError",
    "LabNotebookAppendError",
    "LedgerError",
    "NetworkError",
    "OOSDataLeakageError",
    "RiskViolationError",
    "TrialLogError",
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


@dataclass(frozen=True, slots=True)
class InvalidPortfolioTransition(DomainError):
    """A fill cannot be applied to a portfolio snapshot.

    Returned as ``Failure(InvalidPortfolioTransition(...))`` from
    :meth:`hermes.domain.portfolio.snapshot.PortfolioSnapshot.after_fill` when
    the fill would overdraw cash, sell more than the open position, or sell a
    pair that is not held. The snapshot is left unchanged.

    Args:
        reason: Human-readable description of the refused transition.

    Reference: 308-signal-event-portfolio-state change.md D-5.
    """

    reason: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Invalid portfolio transition: {self.reason}"


@dataclass(frozen=True, slots=True)
class RiskViolationError(DomainError):
    """A risk overlay rule fired and blocked execution.

    Returned as ``Failure(RiskViolationError(...))`` from risk-rule
    evaluation when the rule's HALT threshold is crossed. Callers that
    receive this error MUST abort the pending order or position entry and
    log the ``reason`` for audit.

    Args:
        rule: Short identifier for the rule that fired (e.g. ``"RO-04"``).
        reason: Human-readable description of the violation including the
            measured value and the threshold that was exceeded.

    Reference: SA-9 §5.5 — risk overlay rules; code-conventions.md §2.
    """

    rule: str
    reason: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Risk violation [{self.rule}]: {self.reason}"


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
class DataManifestFormatError(InfraError):
    """The data manifest file is missing, unreadable, or not schema version 1.

    Args:
        path: Manifest file path.
        msg: What was wrong with it.

    Reference: openspec/changes/356-data-manifest/change.md Design.
    """

    path: str
    msg: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Data manifest {self.path} is invalid: {self.msg}"


@dataclass(frozen=True, slots=True)
class KrakenPublicError(InfraError):
    """Kraken public-endpoints adapter returned an error response or bad payload.

    Raised by :class:`hermes.adapters.exchanges.kraken_public.KrakenPublicAdapter`
    on:

    - Non-empty ``error`` array in the Kraken response envelope
      (``{"error": [...], "result": {...}}``) — Kraken signals every
      business-level failure through this array.
    - HTTP non-2xx from any public endpoint (fees, klines, depth, ticker).
    - Malformed / unparseable JSON body.
    - Missing pair in the ``result`` mapping (e.g. asked for
      ``XXBTZUSD`` but the response omitted it entirely).

    Extends :class:`InfraError` so callers using ``except InfraError``
    still catch it. Kept separate from :class:`ExchangeError` (Binance
    envelope) because the two exchanges' error shapes are unrelated and
    attribution matters at triage time.

    Args:
        msg: Human-readable context — includes HTTP status, the offending
            payload excerpt, or the Kraken error strings verbatim.

    Reference: code-conventions.md §2,
    docs/decisions/2026-08-24-kraken-public-adapter.md.
    """

    msg: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Kraken public error: {self.msg}"


@dataclass(frozen=True, slots=True)
class OOSDataLeakageError(DomainError):
    """Attempted to read OOS data outside certification mode.

    Raised by :class:`hermes.application.strategy_protocol.data_split_enforcer.DataSplitEnforcer`
    when code attempts to read a bar or trade whose timestamp falls inside the
    OOS test window (``2025-09-01 ≤ ts ≤ 2026-12-31``) while
    ``certification_mode`` context variable is ``False``.

    This is the code-enforced read refusal mandated by
    ``docs/experiments/swing-mvp1/pre-registration-v1.md`` §4 discipline gate.
    OOS data is physically inaccessible until the certification harness explicitly activates
    :func:`hermes.application.strategy_protocol.data_split_enforcer.certification_context`.

    Args:
        attempted_timestamp: The UTC datetime that violated the OOS boundary.
        oos_start: The OOS window start date (``2025-09-01``).

    Reference: pre-registration-v1.md §4, spec-9-strategy-development-protocol change.md D-2.
    """

    attempted_timestamp: str
    oos_start: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return (
            f"OOS data leakage prevented: timestamp {self.attempted_timestamp} "
            f"is inside OOS window (starts {self.oos_start}). "
            f"Activate certification_context() to permit this read."
        )


@dataclass(frozen=True, slots=True)
class DatasetHashMismatchError(DomainError):
    """Loaded bars differ from the dataset pinned in the data manifest.

    A backtest or certification result is only reproducible on the exact bars
    the manifest pins, so a changed dataset stops the run instead of silently
    producing a result on different data.

    Args:
        dataset_id: Manifest id of the dataset (e.g. ``klines/BTCUSDT/4h/train``).
        expected_sha256: Hash pinned in the manifest.
        actual_sha256: Hash of the bars actually loaded.
        expected_rows: Row count pinned in the manifest.
        actual_rows: Row count actually loaded.

    Reference: openspec/changes/356-data-manifest/change.md R-1.
    """

    dataset_id: str
    expected_sha256: str
    actual_sha256: str
    expected_rows: int
    actual_rows: int

    def __str__(self) -> str:
        """Return human-readable description."""
        return (
            f"Dataset {self.dataset_id} does not match the data manifest: "
            f"expected {self.expected_rows} rows sha256={self.expected_sha256}, "
            f"loaded {self.actual_rows} rows sha256={self.actual_sha256}. "
            f"Re-pin only after recording why in the lab notebook."
        )


@dataclass(frozen=True, slots=True)
class DatasetNotPinnedError(DomainError):
    """A requested bar range needs a dataset the data manifest does not pin.

    Args:
        pair: Pair string (``BASE/QUOTE``).
        interval: Interval string (e.g. ``4h``).
        reason: Which split is missing, or that the range lies outside every split.

    Reference: openspec/changes/356-data-manifest/change.md R-3.
    """

    pair: str
    interval: str
    reason: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"No pinned dataset for {self.pair} {self.interval}: {self.reason}"


@dataclass(frozen=True, slots=True)
class LabNotebookAppendError(DomainError):
    """Attempted to overwrite or duplicate an existing lab notebook entry.

    Raised by :class:`hermes.application.strategy_protocol.lab_notebook.LabNotebook`
    when an entry with the same header (timestamp + phase + actor + action) already
    exists as the last entry in the notebook, or when a write would modify existing
    content (overwrite attempt).

    The lab notebook is append-only per
    ``docs/experiments/swing-mvp1/pre-registration-v1.md`` §12 — the
    anti-p-hacking mechanism requires that every action is permanently recorded
    and nothing can be silently erased.

    Args:
        header: The entry header that conflicted.

    Reference: pre-registration-v1.md §12, spec-9-strategy-development-protocol change.md D-5.
    """

    header: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return (
            f"Lab notebook append error: entry with header '{self.header}' "
            f"already exists as the last entry. The notebook is append-only."
        )


@dataclass(frozen=True, slots=True)
class TrialLogError(InfraError):
    """The trial log could not be read or written, or its hash chain is broken.

    A broken chain means a line was edited, removed or reordered, so the trial
    count behind the Deflated Sharpe Ratio can no longer be trusted.

    Args:
        path: Trial log path.
        msg: What went wrong, with the line number when known.

    Reference: openspec/changes/357-trial-log/change.md D-2.
    """

    path: str
    msg: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Trial log {self.path}: {self.msg}"


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
