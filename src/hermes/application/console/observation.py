"""Figures of the 90-day shadow observation window, computed from raw samples.

The observation view carries one sample per fill or decision; this module turns
them into the percentiles, histogram and drift verdict every surface shows. The
same functions serve fixture scenarios and the ledger read model, so the panel,
the JSON and the weekly report can never disagree.

The thresholds are the signed pre-registration v1 values (§7 SHADOW_CERTIFIED
gate, §10 shadow-phase kill criteria). They live here because an installed
wheel cannot read the signed file; a unit test compares them with it.

Reference: openspec/changes/observation-dashboard/change.md (D-2, D-3, D-4);
openspec/changes/353-fill-kernel-in-serve D-4 (``p50(|drift_bps|)``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Final

__all__ = [
    "DRIFT_AT_RISK_BPS",
    "DRIFT_GATE_BPS",
    "DRIFT_SUSTAINED_DAYS",
    "SHADOW_SHARPE_DAYS",
    "SHADOW_SHARPE_FLOOR",
    "SHOCK_FIRES",
    "SHOCK_WINDOW_DAYS",
    "SLIPPAGE_EDGES_BPS",
    "WINDOW_DAYS",
    "Distribution",
    "DriftVerdict",
    "drift_verdict",
    "histogram",
    "percentile",
    "summarize",
]

WINDOW_DAYS: Final = 90
DRIFT_GATE_BPS: Final = Decimal(30)
# Display-only early warning at 80% of the gate; it never changes a state.
DRIFT_AT_RISK_BPS: Final = Decimal(24)
DRIFT_SUSTAINED_DAYS: Final = 7
SHOCK_FIRES: Final = 3
SHOCK_WINDOW_DAYS: Final = 30
SHADOW_SHARPE_FLOOR: Final = Decimal("0.3")
SHADOW_SHARPE_DAYS: Final = 60
SLIPPAGE_EDGES_BPS: Final[tuple[Decimal, ...]] = (
    Decimal(0),
    Decimal(5),
    Decimal(10),
    Decimal(20),
)

_HUNDRED: Final = Decimal(100)


def percentile(samples: Sequence[Decimal], pct: int) -> Decimal:
    """Return the ``pct`` percentile of ``samples`` by linear interpolation.

    The method matches numpy's default (``linear``), computed in exact
    ``Decimal`` so no float rounding enters a gated figure.

    Args:
        samples: Values in any order; at least one.
        pct: Percentile from 0 to 100.

    Returns:
        The interpolated value; ``min`` at 0 and ``max`` at 100.

    Raises:
        ValueError: ``samples`` is empty or ``pct`` is outside 0..100.
    """
    if not samples:
        msg = "percentile of no samples"
        raise ValueError(msg)
    if not 0 <= pct <= 100:  # noqa: PLR2004
        msg = f"percentile must be within 0..100, got {pct}"
        raise ValueError(msg)
    ordered = sorted(samples)
    rank = Decimal(pct) * (len(ordered) - 1) / _HUNDRED
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


@dataclass(frozen=True, slots=True)
class Distribution:
    """Summary of one sample set.

    Attributes:
        count: Number of samples.
        p50: Median.
        p95: 95th percentile.
        max: Largest sample.
    """

    count: int
    p50: Decimal
    p95: Decimal
    max: Decimal


def summarize(samples: Sequence[Decimal]) -> Distribution | None:
    """Return p50, p95 and max of ``samples``, or ``None`` when there are none.

    Args:
        samples: Values in any order.

    Returns:
        The summary, or ``None`` for an empty sequence.
    """
    if not samples:
        return None
    return Distribution(
        count=len(samples),
        p50=percentile(samples, 50),
        p95=percentile(samples, 95),
        max=max(samples),
    )


def histogram(samples: Sequence[Decimal], edges: Sequence[Decimal]) -> tuple[int, ...]:
    """Count ``samples`` into the buckets the ascending ``edges`` bound.

    Bucket ``i`` holds values in ``[edges[i-1], edges[i])``; the first bucket
    is everything below ``edges[0]`` and the last everything from
    ``edges[-1]`` up, so every sample lands in exactly one bucket.

    Args:
        samples: Values in any order.
        edges: Strictly ascending bucket boundaries.

    Returns:
        ``len(edges) + 1`` counts, lowest bucket first.

    Raises:
        ValueError: ``edges`` is not strictly ascending.
    """
    if any(a >= b for a, b in pairwise(edges)):
        msg = "histogram edges must be strictly ascending"
        raise ValueError(msg)
    counts = [0] * (len(edges) + 1)
    for value in samples:
        counts[sum(1 for edge in edges if value >= edge)] += 1
    return tuple(counts)


class DriftVerdict(StrEnum):
    """How the sim-vs-live drift stands against the §7 gate right now."""

    NO_FILLS = "no live fills yet"
    PASS = "pass"  # noqa: S105 - a verdict label, not a credential
    AT_RISK = "at risk"
    FAIL = "fail"


def drift_verdict(drift_bps: Sequence[Decimal]) -> DriftVerdict:
    """Return the verdict for signed per-fill drift samples.

    The gate reads p50 of the absolute drift and passes strictly below
    :data:`DRIFT_GATE_BPS` (pre-registration §7; #353 D-4).

    Args:
        drift_bps: Signed drift of each live-book fill, in basis points.

    Returns:
        ``NO_FILLS`` without samples, ``FAIL`` at or above the gate,
        ``AT_RISK`` at or above :data:`DRIFT_AT_RISK_BPS`, else ``PASS``.
    """
    if not drift_bps:
        return DriftVerdict.NO_FILLS
    p50 = percentile([abs(value) for value in drift_bps], 50)
    if p50 >= DRIFT_GATE_BPS:
        return DriftVerdict.FAIL
    if p50 >= DRIFT_AT_RISK_BPS:
        return DriftVerdict.AT_RISK
    return DriftVerdict.PASS
