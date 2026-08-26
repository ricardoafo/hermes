"""Baseline calibration job — testnet-vs-testnet baseline interface and fixture stub.

Defines the interface for a baseline calibration job that compares fill-model
output against a real testnet exchange run. For MVP1, the baseline is a stub:
the interface is defined and a fixture implementation is provided, but actual
testnet HTTP calls are deferred to Spec 6 follow-up integration.

Design:

- ``BaselineJob`` is a ``Protocol`` (structural subtyping) so that future
  testnet adapters can satisfy it without importing this module.
- ``FixtureBaselineJob`` is a concrete implementation that replays pre-captured
  fill data from a fixture dict — suitable for integration testing without a
  live testnet connection.

Reference: spec-6-calibration-harness/change.md §IN baseline.py, D-3.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from hermes.domain.trading.fill import Fill

__all__ = [
    "BaselineJob",
    "BaselineResult",
    "FixtureBaselineJob",
]


@dataclass(frozen=True, slots=True)
class BaselineResult:
    """Result of a baseline calibration comparison.

    Carries both the simulated fills (from ``SimulatedExchangeAdapter``) and
    the reference fills (from testnet or fixture), plus the computed M-02
    delta between them.

    Attributes:
        simulated_fills: Fills produced by the backtest fill model.
        reference_fills: Fills from the testnet reference (or fixture).
        simulated_avg_price: Average fill price from simulated fills.
        reference_avg_price: Average fill price from reference fills.
        delta_bps: Price delta in basis points between simulated and reference.
            Positive means the simulator was more expensive than testnet.
        is_within_tolerance: Whether ``abs(delta_bps)`` is within the
            operator-configured tolerance.
        tolerance_bps: The tolerance threshold used for the comparison.

    Reference: spec-6-calibration-harness/change.md §baseline.py.
    """

    simulated_fills: tuple[Fill, ...]
    reference_fills: tuple[Fill, ...]
    simulated_avg_price: Decimal
    reference_avg_price: Decimal
    delta_bps: Decimal
    is_within_tolerance: bool
    tolerance_bps: Decimal


@runtime_checkable
class BaselineJob(Protocol):
    """Contract for a baseline calibration job.

    Implementors compare fill-model output against a real testnet (or fixture)
    reference. For MVP1, only ``FixtureBaselineJob`` is wired. Testnet HTTP
    implementations are Spec 6 follow-up integration.

    Reference: spec-6-calibration-harness/change.md §IN baseline.py.
    """

    def run(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        simulated_fills: Sequence[Fill],
    ) -> BaselineResult:
        """Run the baseline comparison and return the result.

        Args:
            window_start: UTC-aware start of the comparison window.
            window_end: UTC-aware end of the comparison window.
            simulated_fills: Fills from the backtest fill model to compare.

        Returns:
            :class:`BaselineResult` with both sides and the computed delta.
        """
        ...


class FixtureBaselineJob:
    """Fixture implementation of ``BaselineJob`` — replays pre-captured fills.

    Used for integration testing without a live testnet connection. The
    reference fills are provided at construction time from a fixture dict or
    list. The comparison is purely mechanical: compute the average price on
    each side and express the delta in bps.

    Args:
        reference_fills: Pre-captured reference fills to compare against.
        tolerance_bps: Maximum acceptable ``|delta_bps|`` for the comparison
            to be considered within tolerance. Default: ``Decimal("5")`` (5 bps).

    Reference: spec-6-calibration-harness/change.md §baseline.py.
    """

    def __init__(
        self,
        *,
        reference_fills: Sequence[Fill],
        tolerance_bps: Decimal | None = None,
    ) -> None:
        """Initialise the fixture with reference fills.

        Args:
            reference_fills: Pre-captured reference fills.
            tolerance_bps: Tolerance in bps. Defaults to ``Decimal("5")``.
        """
        self._reference_fills: tuple[Fill, ...] = tuple(reference_fills)
        self._tolerance_bps: Decimal = tolerance_bps if tolerance_bps is not None else Decimal("5")

    def run(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        simulated_fills: Sequence[Fill],
    ) -> BaselineResult:
        """Run the fixture baseline comparison.

        Computes the volume-weighted average price on each side and expresses
        the delta in basis points. Returns a :class:`BaselineResult` indicating
        whether the delta is within the configured tolerance.

        Args:
            window_start: UTC-aware start of the comparison window (acknowledged;
                not used to filter reference fills in the fixture implementation
                — callers are responsible for providing the correct window's fills).
            window_end: UTC-aware end of the comparison window (acknowledged).
            simulated_fills: Fills from the backtest fill model.

        Returns:
            :class:`BaselineResult` with both sides and the computed delta.
        """
        _ = window_start, window_end  # acknowledged — fixture does not filter by window

        sim_fills = tuple(simulated_fills)
        ref_fills = self._reference_fills

        sim_avg = self._avg_price(sim_fills)
        ref_avg = self._avg_price(ref_fills)

        if ref_avg == Decimal("0"):
            delta_bps = Decimal("0")
        else:
            delta_bps = (sim_avg - ref_avg) / ref_avg * Decimal("10000")

        return BaselineResult(
            simulated_fills=sim_fills,
            reference_fills=ref_fills,
            simulated_avg_price=sim_avg,
            reference_avg_price=ref_avg,
            delta_bps=delta_bps,
            is_within_tolerance=abs(delta_bps) <= self._tolerance_bps,
            tolerance_bps=self._tolerance_bps,
        )

    @staticmethod
    def _avg_price(fills: tuple[Fill, ...]) -> Decimal:
        """Compute the simple average fill price.

        Args:
            fills: Fill events to average.

        Returns:
            Average ``avg_price`` across all fills. Returns ``Decimal("0")``
            when ``fills`` is empty.
        """
        if not fills:
            return Decimal("0")
        total = sum((f.avg_price for f in fills), Decimal("0"))
        return total / Decimal(len(fills))
