"""Execution-quality monitor: control charts on fees, slippage and latency.

Feeds each executed order's fee (bps of notional), slippage (bps from the
decision price) and order latency (ms) into its own
:class:`~hermes.domain.monitoring.control_chart.ControlChart`. The start of a
breach episode becomes an ``AnomalyDetected`` event with ``WARNING``
severity: a human looks, and nothing pauses automatically. Any new pause
rule during v1 would change the pre-registered protocol (§10, §11).

The inputs carry no P&L, equity or position value, only per-order execution
costs in basis points and milliseconds (ruling 1, ADR-012 D-06).

``hermes serve`` feeds every executed shadow fill through
:func:`observation_of` (#1181): the fee from the fill, the slippage and
latency from its ``FillQualityRecorded`` row (#353, #30). At start-up it
replays the ledger's past fills with :meth:`ExecutionQualityMonitor.prime`, so
a restart keeps the baseline and does not alert an episode again. Serve
records and alerts the returned events; nothing reads them to trade.

A metric a fill did not measure (no latency when the clock put the fill
before the bar close, no slippage on a one-sided book, a fee in another
currency) is ``None``: that chart does not move, the others still do.

Reference: issue #362; issue #1181; ADR-005 Category B; ADR-012 D-04.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.fill_quality_recorded import FillQualityRecorded
from hermes.domain.monitoring.control_chart import Breach, ChartParameters, ControlChart
from hermes.domain.value_objects.money import Money

__all__ = ["SOURCE_PREFIX", "ExecutionObservation", "ExecutionQualityMonitor", "observation_of"]

SOURCE_PREFIX = "execution_quality"
"""``AnomalyDetected.source`` is ``execution_quality.<metric>``."""

_UNITS = {"fee_bps": "bps", "slippage_bps": "bps", "latency_ms": "ms"}
_BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class ExecutionObservation:
    """Execution costs of one order. No P&L, equity or position value.

    ``None`` means the order did not measure that metric; its chart skips it.
    """

    fee_bps: float | None
    slippage_bps: float | None
    latency_ms: float | None


def observation_of(quality: FillQualityRecorded, fee: Money) -> ExecutionObservation:
    """Return the execution costs of one recorded shadow fill.

    Args:
        quality: The fill's quality row: price, quantity, slippage, latency.
        fee: The fee the fill paid.

    Returns:
        The fee in bps of the fill's notional (``None`` when the fee is not in
        the quote currency, so it cannot be set against the notional), the
        slippage from the book mid in bps (``None`` without a mid) and the
        decision-to-fill latency in ms (``None`` when not measured).
    """
    fee_bps = None
    if fee.currency == quality.pair.quote:
        fee_bps = float(fee.amount / (quality.fill_price * quality.qty) * _BPS)
    return ExecutionObservation(
        fee_bps=fee_bps,
        slippage_bps=None if quality.slippage is None else float(quality.slippage * _BPS),
        latency_ms=None if quality.delta_ms is None else float(quality.delta_ms),
    )


@dataclass
class ExecutionQualityMonitor:
    """One control chart per execution metric."""

    params: ChartParameters = field(default_factory=ChartParameters)
    _charts: dict[str, ControlChart] = field(init=False)

    def __post_init__(self) -> None:
        """Create one chart per metric with the shared parameters."""
        self._charts = {name: ControlChart(self.params) for name in _UNITS}

    def prime(self, observations: Iterable[ExecutionObservation]) -> None:
        """Replay past orders, oldest first, and alert nothing.

        The charts end in the state the live run left them in: the same
        baseline, and an episode still open at the last order stays open, so
        the next order alerts only if a live run would have.

        Args:
            observations: The past orders' costs in the order they executed.
        """
        for observation in observations:
            self._feed(observation)

    def observe(
        self, observation: ExecutionObservation, *, at: datetime, correlation_id: str
    ) -> list[AnomalyDetected]:
        """Feed one order's execution costs into the charts.

        Args:
            observation: The order's fee, slippage and latency.
            at: When the order executed (UTC).
            correlation_id: Correlation id of the order, for the event.

        Returns:
            One ``AnomalyDetected`` per metric whose breach episode starts on
            this order; empty otherwise.

        Raises:
            ValueError: A measured value is not finite.
        """
        return [
            AnomalyDetected(
                occurred_at=at,
                correlation_id=correlation_id,
                source=f"{SOURCE_PREFIX}.{name}",
                severity=AnomalySeverity.WARNING,
                reason=_reason(name, breach),
            )
            for name, breach in self._feed(observation)
        ]

    def _feed(self, observation: ExecutionObservation) -> list[tuple[str, Breach]]:
        values = {
            "fee_bps": observation.fee_bps,
            "slippage_bps": observation.slippage_bps,
            "latency_ms": observation.latency_ms,
        }
        breaches: list[tuple[str, Breach]] = []
        for name, value in values.items():
            if value is None:
                continue
            breach = self._charts[name].observe(value)
            if breach is not None:
                breaches.append((name, breach))
        return breaches


def _reason(name: str, breach: Breach) -> str:
    unit = _UNITS[name]
    signals = ", ".join(s.value for s in breach.signals)
    return (
        f"{name} out of control ({signals}): {breach.value:.2f} {unit} against a baseline "
        f"of {breach.baseline_mean:.2f} ± {breach.baseline_sigma:.2f} {unit}"
    )
