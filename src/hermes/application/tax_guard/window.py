"""Rolling-window computation + breach check for the recharacterization guard.

Three pure functions compose the tax guard's public surface:

- :func:`compute_window` — Given a sequence of domain events and an
  ``end`` instant, returns a :class:`WindowStats` snapshot of the
  ``[end - window_days, end]`` interval. No clock is read; ``end`` is
  injected. No I/O.
- :func:`check_breach` — Given a :class:`WindowStats` and a
  :class:`TaxGuardThresholds`, returns an :class:`AnomalyDetected`
  when any configured threshold is crossed, otherwise ``None``.
- :func:`evaluate` — Convenience composition: computes the window and
  returns the anomaly (or ``None``). This is what the future signal
  engine subscribes into.

Design invariants (property-tested):

- **Pure**: no side effects, no clock reads, no logging, no bus writes.
- **UTC**: the injected ``end`` MUST be timezone-aware. Naive datetimes
  raise ``ValueError`` at the boundary (code-conventions §9). Every
  ``occurred_at`` compared against it must also be tz-aware — the
  ``DomainEvent`` contract guarantees this upstream.
- **Distinct assets** derive from ``TradeExecuted.pair.base`` — the
  base currency of the trading pair. This mirrors ADR-002 §6 wording
  ("distinct assets") which counts underlying holdings, not markets.
- **Fee income** is bucketed by currency (``fees_by_currency``). ADR-002
  §6 thresholds are stated in EUR; non-EUR fees are recorded for audit
  but do NOT feed the threshold — proper FX conversion is deferred to
  the ADR-004 EUR pricing source (see PR Risk).
- **Hexagonal purity**: the application layer does NOT import
  :class:`hermes.infrastructure.config.models.TaxGuardConfig` directly
  (import-linter contract ``application-imports-only-domain-and-ports``
  forbids it). Instead we accept a :class:`TaxGuardThresholds`
  ``Protocol`` — the infra ``TaxGuardConfig`` structurally satisfies
  the protocol via matching field names.

Reference: ADR-002 §Decision 6.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.time import require_utc
from hermes.ports.trade_history import TradeHistoryPort

__all__ = [
    "TAX_GUARD_SOURCE",
    "TaxGuardThresholds",
    "WindowStats",
    "check_breach",
    "compute_window",
    "evaluate",
    "evaluate_ledger",
]


class TaxGuardThresholds(Protocol):
    """Structural type for the recharacterization guard threshold surface.

    Duck-typed contract: any object exposing these four attributes with
    the correct types satisfies the protocol. The infrastructure
    :class:`~hermes.infrastructure.config.models.TaxGuardConfig`
    structurally conforms (same field names + types), so composition
    code can pass the loaded config directly without an adapter step.

    Attributes:
        recharacterization_window_days: Rolling-window length in days
            passed to :func:`compute_window`.
        trade_count_90d_max: Inclusive maximum trade count inside the
            window; :func:`check_breach` fires when strictly greater.
        distinct_assets_90d_max: Inclusive maximum count of distinct
            base assets inside the window.
        fee_income_90d_max_eur: Inclusive maximum EUR fee income
            inside the window. Non-EUR fees are not compared against
            this threshold (see module docstring "Fee income" note).
    """

    recharacterization_window_days: int
    trade_count_90d_max: int
    distinct_assets_90d_max: int
    fee_income_90d_max_eur: Decimal


TAX_GUARD_SOURCE: str = "tax_guard.recharacterization"
"""Value stored in ``AnomalyDetected.source`` for every breach event.

Downstream routers filter on this identifier to trigger the engine-pause
behavior mandated by ADR-002 §6 (deferred to #25 for the engine work).
"""


@dataclass(frozen=True, slots=True)
class WindowStats:
    """Immutable snapshot of trade activity inside a rolling window.

    Every metric here maps 1:1 to an ADR-002 §6 threshold. The struct is
    a pure data carrier — no methods, no derived properties, so callers
    can reason about it without side effects.

    Attributes:
        trade_count: Number of ``TradeExecuted`` events observed inside
            the window (both BUY and SELL count).
        distinct_assets: Count of unique ``TradeExecuted.pair.base``
            currencies observed. Same base traded on multiple quote
            markets counts once.
        fees_by_currency: Aggregate fee amount per fee currency across
            every ``TradeExecuted`` in the window. Keys are the union
            of ``fee.currency`` values encountered; missing keys mean
            zero. Immutable — treat like a frozen ``Mapping``.
        window_start: UTC instant at which the window begins (inclusive).
        window_end: UTC instant at which the window ends (inclusive).
            Injected by the caller — not read from a wall clock inside
            this module.
    """

    trade_count: int
    distinct_assets: int
    fees_by_currency: Mapping[Currency, Decimal] = field(default_factory=dict)
    window_start: datetime | None = None
    window_end: datetime | None = None

    def eur_fee_income(self) -> Decimal:
        """Return the EUR-denominated fee total (zero if no EUR fees observed).

        Returns:
            The sum of fee amounts whose currency was EUR. ADR-002 §6
            thresholds live in EUR — non-EUR fees are audit-preserved
            in :attr:`fees_by_currency` but do not feed the guard.
        """
        return self.fees_by_currency.get(Currency.EUR, Decimal("0"))


def compute_window(
    events: Sequence[DomainEvent],
    now: datetime,
    *,
    window_days: int,
) -> WindowStats:
    """Return the :class:`WindowStats` snapshot for ``[now - window_days, now]``.

    Only :class:`TradeExecuted` events contribute to the counters — other
    domain events are silently ignored (the tax guard cares about trade
    activity, not lot closures or rebalance triggers). Events with an
    ``occurred_at`` strictly older than the window boundary are excluded;
    events exactly at either boundary are INCLUDED (closed interval).

    Args:
        events: Sequence of domain events to observe. Order is irrelevant
            — the function does not mutate or re-sort the sequence.
        now: The UTC-aware end instant of the window. MUST have ``tzinfo``.
        window_days: Rolling-window length in days. From
            :attr:`TaxGuardThresholds.recharacterization_window_days`.
            Must be strictly positive.

    Returns:
        A :class:`WindowStats` populated with counts and per-currency fee
        totals across the window.

    Raises:
        ValueError: If ``now`` is naive or ``window_days`` is not positive.
    """
    require_utc(now, name="now")
    if window_days <= 0:
        raise ValueError(
            f"tax_guard.compute_window: window_days must be positive, got {window_days}."
        )

    window_start = now - timedelta(days=window_days)
    trade_count = 0
    assets: set[Currency] = set()
    fees: dict[Currency, Decimal] = {}

    for event in events:
        if not isinstance(event, TradeExecuted):
            continue
        require_utc(event.occurred_at, name="event.occurred_at")
        if event.occurred_at < window_start or event.occurred_at > now:
            continue
        trade_count += 1
        assets.add(event.pair.base)
        fees[event.fee.currency] = fees.get(event.fee.currency, Decimal("0")) + event.fee.amount

    return WindowStats(
        trade_count=trade_count,
        distinct_assets=len(assets),
        fees_by_currency=fees,
        window_start=window_start,
        window_end=now,
    )


def _describe_breach(
    *,
    trade_over: bool,
    assets_over: bool,
    fee_over: bool,
    stats: WindowStats,
    config: TaxGuardThresholds,
) -> str:
    """Return a comma-joined description of every crossed threshold.

    Args:
        trade_over: Whether the trade-count threshold was crossed.
        assets_over: Whether the distinct-assets threshold was crossed.
        fee_over: Whether the EUR fee-income threshold was crossed.
        stats: The window snapshot that produced the breach.
        config: The config carrying the threshold values.

    Returns:
        A single human-readable line naming every crossed threshold with
        its observed value and configured ceiling. Stored verbatim in
        :attr:`AnomalyDetected.reason` — never sensitive data
        (code-conventions §12).
    """
    parts: list[str] = []
    if trade_over:
        parts.append(f"trade_count={stats.trade_count} > {config.trade_count_90d_max}")
    if assets_over:
        parts.append(f"distinct_assets={stats.distinct_assets} > {config.distinct_assets_90d_max}")
    if fee_over:
        parts.append(f"fee_income_eur={stats.eur_fee_income()} > {config.fee_income_90d_max_eur}")
    return "recharacterization threshold breached: " + "; ".join(parts)


def check_breach(
    stats: WindowStats,
    config: TaxGuardThresholds,
    *,
    now: datetime,
    correlation_id: str,
) -> AnomalyDetected | None:
    """Return an :class:`AnomalyDetected` if any threshold is crossed, else ``None``.

    A breach fires when any of the ADR-002 §6 thresholds is crossed —
    strictly greater than the configured ceiling. Values equal to the
    ceiling do NOT fire (the ADR wording says ``> N``, not ``>= N``).

    Args:
        stats: Snapshot returned by :func:`compute_window`.
        config: An object satisfying :class:`TaxGuardThresholds` — the
            infra ``TaxGuardConfig`` is a valid caller.
        now: UTC-aware timestamp stamped onto the emitted event. Must
            match the ``now`` passed to :func:`compute_window` for a
            consistent audit trail.
        correlation_id: ULID tying this evaluation to the caller's
            request / scheduled cycle. Never invented inside the guard.

    Returns:
        ``AnomalyDetected(severity=CRITICAL, source="tax_guard.recharacterization",
        reason=<description>)`` when at least one threshold is crossed;
        ``None`` when the window is clean.

    Raises:
        ValueError: If ``now`` is naive.
    """
    require_utc(now, name="now")

    trade_over = stats.trade_count > config.trade_count_90d_max
    assets_over = stats.distinct_assets > config.distinct_assets_90d_max
    fee_over = stats.eur_fee_income() > config.fee_income_90d_max_eur

    if not (trade_over or assets_over or fee_over):
        return None

    reason = _describe_breach(
        trade_over=trade_over,
        assets_over=assets_over,
        fee_over=fee_over,
        stats=stats,
        config=config,
    )
    return AnomalyDetected(
        occurred_at=now,
        correlation_id=correlation_id,
        source=TAX_GUARD_SOURCE,
        severity=AnomalySeverity.CRITICAL,
        reason=reason,
    )


def evaluate(
    events: Sequence[DomainEvent],
    config: TaxGuardThresholds,
    *,
    now: datetime,
    correlation_id: str,
) -> AnomalyDetected | None:
    """Compose :func:`compute_window` + :func:`check_breach` in one call.

    This is the entry point future callers (the paper-trading engine in
    #25) will subscribe into. The engine takes the returned event, pauses
    new signal generation, and publishes the event to the bus.

    Args:
        events: Sequence of domain events to observe.
        config: An object satisfying :class:`TaxGuardThresholds`.
        now: UTC-aware end instant of the rolling window.
        correlation_id: ULID tying the evaluation cycle to the caller's
            request context.

    Returns:
        The :class:`AnomalyDetected` event on breach, or ``None`` when
        every threshold holds.

    Raises:
        ValueError: If ``now`` is naive or the config window is invalid.
    """
    stats = compute_window(events, now, window_days=config.recharacterization_window_days)
    return check_breach(stats, config, now=now, correlation_id=correlation_id)


def evaluate_ledger(
    history: TradeHistoryPort,
    config: TaxGuardThresholds,
    *,
    now: datetime,
    correlation_id: str,
) -> AnomalyDetected | None:
    """Run :func:`evaluate` over the trades the ledger holds for the rolling window.

    Reads only ``[now - recharacterization_window_days, now]`` from
    ``history``, so the guard sees every recorded trade without the caller
    assembling an in-memory event list (#135).

    Args:
        history: Read-only trade source, normally the SQLite ledger.
        config: An object satisfying :class:`TaxGuardThresholds`.
        now: UTC-aware end instant of the rolling window.
        correlation_id: ULID tying the evaluation cycle to the caller's
            request context.

    Returns:
        The :class:`AnomalyDetected` event on breach, or ``None``.

    Raises:
        ValueError: If ``now`` is naive or the config window is invalid.
    """
    require_utc(now, name="now")
    if config.recharacterization_window_days <= 0:
        raise ValueError(
            "recharacterization_window_days must be positive, "
            f"got {config.recharacterization_window_days}"
        )
    start = now - timedelta(days=config.recharacterization_window_days)
    trades = history.trades_between(start, now)
    return evaluate(trades, config, now=now, correlation_id=correlation_id)
