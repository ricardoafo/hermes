"""M-01..M-07 calibration metric calculators — pure functions.

Implements all seven metrics defined in pre-registration-v1.md §Calibration
and SA-5. Every calculator is a pure function: no I/O, no mutable state,
no side effects. The ``CalibrationHarness`` (application layer) is responsible
for collecting fill events and calling these calculators.

Metrics:

- **M-01**: Total fill count — basic event count.
- **M-02**: Fill-price delta bps — the PRIMARY calibration metric; validates
  ``OrderbookWalkFillModel`` accuracy. Returns avg + p50/p95/p99 across all fills.
- **M-03**: Latency simulation — mocked to 0 for MVP1 backtest; real values
  come in shadow mode (Spec 6 follow-up integration).
- **M-04**: Rejection rate — fraction of ``Rejection`` outcomes over all fill
  attempts (fills + rejections).
- **M-05**: Orphan position count — SA-4 P0: every open position must trace to
  a fill event. Returns the count of open positions with no preceding fill.
- **M-06**: Regime-tagged returns — cumulative return per regime bucket
  (bull x quiet, bull x vol, bear x quiet, bear x vol). ``None`` for a bucket when
  no fills occurred in that regime.
- **M-07**: Queue-position quality — OD-2 MVP2 placeholder; always returns
  ``None`` for MVP1.

Domain purity: this module imports only :mod:`hermes.domain.trading` and
stdlib/Decimal. No pydantic, no httpx, no ports.

Reference: spec-6-calibration-harness/change.md D-4, pre-reg §Calibration, SA-5.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import OrderSide

__all__ = [
    "FillPriceDeltaBps",
    "RegimeLabel",
    "RegimeReturns",
    "compute_m01_fill_count",
    "compute_m02_fill_price_delta_bps",
    "compute_m03_latency_ms",
    "compute_m04_rejection_rate",
    "compute_m05_orphan_positions",
    "compute_m06_regime_returns",
    "compute_m07_queue_quality",
]

_BPS_SCALE: Decimal = Decimal("10000")
_ZERO: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Value objects for metric results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FillPriceDeltaBps:
    """M-02 result: fill-price delta in basis points across all fills.

    The delta for each fill is:
        ``(avg_price - mid_price) / mid_price * 10000``

    For BUY orders a positive delta means the fill price was above mid (slippage).
    For SELL orders the sign is inverted so positive still means slippage.

    All arithmetic is Decimal to preserve precision per code-conventions.md §6.

    Attributes:
        avg_bps: Mean fill-price delta across all fills in bps.
        p50_bps: 50th percentile (median) fill-price delta in bps.
        p95_bps: 95th percentile fill-price delta in bps.
        p99_bps: 99th percentile fill-price delta in bps.

    Reference: spec-6-calibration-harness change.md D-4, pre-reg §Calibration M-02.
    """

    avg_bps: Decimal
    p50_bps: Decimal
    p95_bps: Decimal
    p99_bps: Decimal


class RegimeLabel(StrEnum):
    """Four market-regime buckets used by M-06.

    Regime detection notes (``docs/experiments/swing-mvp1/regime-detection-notes.md``)
    define the two axes:

    - Trend: ``bull`` (positive momentum) vs ``bear`` (negative momentum).
    - Volatility: ``quiet`` (ATR below threshold) vs ``vol`` (ATR above threshold).

    Reference: pre-reg §Calibration M-06.
    """

    BULL_QUIET = "bull_quiet"
    BULL_VOL = "bull_vol"
    BEAR_QUIET = "bear_quiet"
    BEAR_VOL = "bear_vol"


@dataclass(frozen=True, slots=True)
class RegimeReturns:
    """M-06 result: cumulative return per regime bucket.

    ``None`` for a bucket when no fills occurred in that regime during the
    calibration window — explicitly not zero, which would imply a measured
    zero return rather than an absence of data.

    Attributes:
        bull_quiet: Cumulative return for bull x quiet regime, or ``None``.
        bull_vol: Cumulative return for bull x volatile regime, or ``None``.
        bear_quiet: Cumulative return for bear x quiet regime, or ``None``.
        bear_vol: Cumulative return for bear x volatile regime, or ``None``.

    Reference: pre-reg §Calibration M-06, regime-detection-notes.md.
    """

    bull_quiet: Decimal | None
    bull_vol: Decimal | None
    bear_quiet: Decimal | None
    bear_vol: Decimal | None


# ---------------------------------------------------------------------------
# M-01: Total fill count
# ---------------------------------------------------------------------------


def compute_m01_fill_count(fills: Sequence[Fill]) -> int:
    """M-01: Return the total number of successful fill events.

    This is the simplest metric — a raw count of ``Fill`` objects. It excludes
    ``Rejection`` outcomes; use :func:`compute_m04_rejection_rate` for the
    combined fill+rejection picture.

    Args:
        fills: Sequence of successful :class:`~hermes.domain.trading.fill.Fill`
            events collected during the calibration window.

    Returns:
        Non-negative integer count of fills. Returns ``0`` for an empty sequence.

    Reference: pre-reg §Calibration M-01.
    """
    return len(fills)


# ---------------------------------------------------------------------------
# M-02: Fill-price delta bps
# ---------------------------------------------------------------------------


def _fill_delta_bps(fill: Fill, mid_price: Decimal) -> Decimal:
    """Compute the fill-price delta in bps for a single fill.

    For BUY orders: ``(avg_price - mid_price) / mid_price * 10000``.
        Positive → fill above mid (slippage). Negative → fill below mid (improvement).

    For SELL orders: ``(mid_price - avg_price) / mid_price * 10000``.
        Positive → fill below mid (slippage for seller). Negative → improvement.

    Args:
        fill: The fill event.
        mid_price: Reference mid-price at fill time. Must be > 0.

    Returns:
        Decimal bps delta. Positive means adverse slippage.

    Raises:
        ValueError: When ``mid_price <= 0``.
    """
    if mid_price <= _ZERO:
        raise ValueError(
            f"compute_m02: mid_price must be > 0, got {mid_price!r}. "
            "A non-positive mid-price cannot appear in a real order book."
        )
    raw_delta = (fill.avg_price - mid_price) / mid_price * _BPS_SCALE
    # Invert for SELL: slippage means fill below mid, which would be negative raw.
    if fill.order.side == OrderSide.SELL:
        return -raw_delta
    return raw_delta


def _percentile_decimal(values: list[Decimal], pct: float) -> Decimal:
    """Compute a percentile from a list of Decimal values.

    Uses linear interpolation (same as numpy's default method) implemented
    with stdlib ``statistics.quantiles`` on float intermediates, then
    converts back to Decimal rounded to 8 significant decimal places.

    Args:
        values: Non-empty list of Decimal values.
        pct: Percentile in [0, 100].

    Returns:
        Decimal percentile value.

    Raises:
        ValueError: When ``values`` is empty or ``pct`` is out of range.
    """
    if not values:
        raise ValueError(
            "compute_m02: cannot compute percentile of an empty list. "
            "Ensure fills is non-empty before calling."
        )
    if not (0 <= pct <= 100):  # noqa: PLR2004
        raise ValueError(f"compute_m02: percentile pct must be in [0, 100], got {pct!r}.")
    if len(values) == 1:
        return values[0]

    floats = [float(v) for v in values]
    # statistics.quantiles returns n-1 cut points for n quantiles.
    # We need arbitrary percentile so use the sorted-interpolation approach.
    n = len(floats)
    floats_sorted = sorted(floats)
    idx = pct / 100 * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    interpolated = floats_sorted[lo] * (1 - frac) + floats_sorted[hi] * frac
    return Decimal(str(interpolated)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)


def compute_m02_fill_price_delta_bps(
    fills: Sequence[Fill],
    mid_prices: Sequence[Decimal],
) -> FillPriceDeltaBps:
    """M-02: Compute fill-price delta bps across all fills — the PRIMARY calibration metric.

    Validates ``OrderbookWalkFillModel`` accuracy by measuring how far the
    simulated fill price deviates from the mid-price at fill time. The closer
    to zero, the more accurate the fill model.

    Args:
        fills: Sequence of :class:`~hermes.domain.trading.fill.Fill` events.
            Must be non-empty for meaningful results. Empty sequence returns all-zero
            :class:`FillPriceDeltaBps`.
        mid_prices: Sequence of reference mid-prices at fill time, one per fill.
            Must have the same length as ``fills``. Each mid-price must be > 0.

    Returns:
        :class:`FillPriceDeltaBps` with ``avg_bps``, ``p50_bps``, ``p95_bps``,
        ``p99_bps``. All fields are ``Decimal(0)`` when ``fills`` is empty.

    Raises:
        ValueError: When ``len(fills) != len(mid_prices)`` or any ``mid_price <= 0``.

    Reference: spec-6-calibration-harness change.md R-2, pre-reg §Calibration M-02.
    """
    if len(fills) != len(mid_prices):
        raise ValueError(
            f"compute_m02: fills and mid_prices must have the same length, "
            f"got fills={len(fills)}, mid_prices={len(mid_prices)}."
        )
    if not fills:
        return FillPriceDeltaBps(
            avg_bps=_ZERO,
            p50_bps=_ZERO,
            p95_bps=_ZERO,
            p99_bps=_ZERO,
        )

    deltas = [_fill_delta_bps(f, mp) for f, mp in zip(fills, mid_prices, strict=True)]
    avg_bps = sum(deltas, _ZERO) / Decimal(len(deltas))
    return FillPriceDeltaBps(
        avg_bps=avg_bps.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN),
        p50_bps=_percentile_decimal(deltas, 50),
        p95_bps=_percentile_decimal(deltas, 95),
        p99_bps=_percentile_decimal(deltas, 99),
    )


# ---------------------------------------------------------------------------
# M-03: Latency simulation
# ---------------------------------------------------------------------------


def compute_m03_latency_ms(fills: Sequence[Fill], *, backtest_mode: bool = True) -> Decimal:
    """M-03: Return fill latency in milliseconds.

    For MVP1 backtest mode, latency is a synthetic zero — the backtest clock
    has no real wall-clock latency. This is an explicit placeholder per OD-2:
    shadow mode will wire real exchange round-trip timing here.

    Args:
        fills: Sequence of fill events (unused for MVP1 backtest — parameter
            exists so the signature is stable for shadow-mode wiring).
        backtest_mode: When ``True`` (default), returns ``Decimal("0")``.
            When ``False`` (shadow/live — future), would measure real latency.

    Returns:
        ``Decimal("0")`` for backtest mode. Always non-negative.

    Reference: spec-6-calibration-harness change.md §IN M-03, pre-reg §Calibration M-03.
    """
    if backtest_mode:
        return _ZERO
    # Shadow / live mode — not implemented for MVP1. Returns zero as a safe
    # default so integration tests can proceed. Real implementation is Spec 6
    # follow-up integration task.
    _ = fills  # acknowledged: fills are the input for real latency measurement
    return _ZERO


# ---------------------------------------------------------------------------
# M-04: Rejection rate
# ---------------------------------------------------------------------------


def compute_m04_rejection_rate(
    fills: Sequence[Fill],
    rejections: Sequence[Rejection],
) -> Decimal:
    """M-04: Compute the rejection rate as a Decimal fraction [0, 1].

    Rejection rate = ``len(rejections) / (len(fills) + len(rejections))``.
    Returns ``Decimal("0")`` when there are no fill attempts at all (empty
    both sequences) — no attempts means no rejections by definition.

    Args:
        fills: Sequence of successful fill events.
        rejections: Sequence of rejection outcomes from the fill model.

    Returns:
        Decimal fraction in ``[0, 1]``. ``0`` when no fill attempts were made
        or when all attempts succeeded. ``1`` when all attempts were rejected.

    Reference: pre-reg §Calibration M-04.
    """
    total = len(fills) + len(rejections)
    if total == 0:
        return _ZERO
    return (Decimal(len(rejections)) / Decimal(total)).quantize(
        Decimal("0.00000001"), rounding=ROUND_HALF_EVEN
    )


# ---------------------------------------------------------------------------
# M-05: Orphan position count
# ---------------------------------------------------------------------------


def compute_m05_orphan_positions(
    open_position_ids: Sequence[str],
    fill_order_ids: Sequence[str],
) -> int:
    """M-05: Count open positions with no preceding fill event — SA-4 P0 guard.

    An "orphan position" is a position ID present in ``open_position_ids``
    but absent from ``fill_order_ids``. SA-4 P0 requires that every position
    traces to a fill event. This metric counts violations.

    The typical caller maps positions to the order IDs that opened them, and
    maps fills to their order IDs, then calls this function. Implementation
    uses set difference — O(n) average case.

    Args:
        open_position_ids: Sequence of order IDs for all currently open
            positions. Duplicates are collapsed (same position opened twice
            is still one position).
        fill_order_ids: Sequence of order IDs for all fill events in the
            calibration window. Duplicates are collapsed.

    Returns:
        Non-negative integer count of orphan positions. ``0`` when every
        position has a fill (SA-4 P0 satisfied).

    Reference: spec-6-calibration-harness change.md R-5, pre-reg §Calibration M-05, SA-4 P0.
    """
    open_set = set(open_position_ids)
    fill_set = set(fill_order_ids)
    return len(open_set - fill_set)


# ---------------------------------------------------------------------------
# M-06: Regime-tagged returns
# ---------------------------------------------------------------------------


def compute_m06_regime_returns(
    fills: Sequence[Fill],
    regime_labels: Sequence[RegimeLabel | None],
    entry_prices: Sequence[Decimal],
    exit_prices: Sequence[Decimal],
) -> RegimeReturns:
    """M-06: Compute cumulative returns per regime bucket.

    Accumulates returns for each fill into one of four regime buckets
    (bull_quiet, bull_vol, bear_quiet, bear_vol) based on the ``regime_labels``
    tag provided by the caller. A ``None`` label means the regime was not
    detected for that bar — such fills are excluded from all buckets.

    Return for one fill = ``(exit_price - entry_price) / entry_price``
    (sign matches the fill side: positive for profitable BUY, positive for
    profitable SELL when exit < entry).

    Args:
        fills: Sequence of fill events. Must have the same length as
            ``regime_labels``, ``entry_prices``, and ``exit_prices``.
        regime_labels: One :class:`RegimeLabel` (or ``None``) per fill.
        entry_prices: Entry price per fill. Must be > 0.
        exit_prices: Exit price per fill (i.e. the close of the position
            that was opened by this fill). Must be > 0.

    Returns:
        :class:`RegimeReturns` with each bucket set to the sum of per-fill
        returns in that regime, or ``None`` when no fills occurred in that regime.

    Raises:
        ValueError: When sequence lengths are inconsistent.

    Reference: pre-reg §Calibration M-06, regime-detection-notes.md.
    """
    n = len(fills)
    if not (len(regime_labels) == len(entry_prices) == len(exit_prices) == n):
        raise ValueError(
            f"compute_m06: fills, regime_labels, entry_prices, exit_prices must all have "
            f"the same length. Got fills={n}, labels={len(regime_labels)}, "
            f"entry={len(entry_prices)}, exit={len(exit_prices)}."
        )

    buckets: dict[RegimeLabel, Decimal] = {}
    counts: dict[RegimeLabel, int] = {}

    for fill, label, entry, exit_ in zip(
        fills, regime_labels, entry_prices, exit_prices, strict=True
    ):
        if label is None:
            continue
        if entry <= _ZERO:
            raise ValueError(
                f"compute_m06: entry_price must be > 0, got {entry!r}. "
                f"Fill order id: {fill.order.side} {fill.order.pair}."
            )
        ret = (exit_ - entry) / entry
        buckets[label] = buckets.get(label, _ZERO) + ret
        counts[label] = counts.get(label, 0) + 1

    def _bucket(label: RegimeLabel) -> Decimal | None:
        return buckets.get(label)

    return RegimeReturns(
        bull_quiet=_bucket(RegimeLabel.BULL_QUIET),
        bull_vol=_bucket(RegimeLabel.BULL_VOL),
        bear_quiet=_bucket(RegimeLabel.BEAR_QUIET),
        bear_vol=_bucket(RegimeLabel.BEAR_VOL),
    )


# ---------------------------------------------------------------------------
# M-07: Queue-position quality (MVP2 placeholder)
# ---------------------------------------------------------------------------


def compute_m07_queue_quality(fills: Sequence[Fill]) -> Decimal | None:
    """M-07: Queue-position quality — MVP2 placeholder, returns None for MVP1.

    This function is a stable interface stub per OD-2. Queue-position quality
    measures how well the fill model predicts queue-position priority for
    limit orders — a metric that requires real exchange order queue data not
    available in MVP1 backtest mode.

    Args:
        fills: Sequence of fill events (acknowledged but unused for MVP1).

    Returns:
        ``None`` always for MVP1. Real implementation is Spec 6 follow-up
        integration task when shadow mode is active.

    Reference: spec-6-calibration-harness change.md D-2, pre-reg §Calibration M-07, OD-2.
    """
    _ = fills  # acknowledged: parameter exists for stable MVP2 interface
    return None
