"""CalibrationHarness — orchestrates a backtest data window and emits CalibrationReport.

The harness runs a strategy across a data window with backtest ports injected,
collects per-fill metrics (M-01..M-07), and emits a ``CalibrationReport``
that the :class:`~hermes.application.calibration.lifecycle.CertificationLifecycle`
can consume to decide state transitions.

Design notes:

- The harness is a pure orchestrator: it calls the domain metric calculators
  (``compute_m01`` through ``compute_m07``) and assembles the result. No I/O
  of its own.
- Caller injects all fill events, rejections, reference mid-prices, and optional
  regime labels — the harness does not call any ports.
- This matches the composition-root pattern: the harness is wired at startup with
  real or fake ports depending on mode (backtest vs shadow). For MVP1, callers
  provide pre-collected fills from a ``SimulatedExchangeAdapter`` run.

Reference: spec-6-calibration-harness/change.md R-4, D-1.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from hermes.domain.calibration.metrics import (
    FillPriceDeltaBps,
    RegimeLabel,
    RegimeReturns,
    compute_m01_fill_count,
    compute_m02_fill_price_delta_bps,
    compute_m03_latency_ms,
    compute_m04_rejection_rate,
    compute_m05_orphan_positions,
    compute_m06_regime_returns,
    compute_m07_queue_quality,
)
from hermes.domain.trading.fill import Fill, Rejection

__all__ = ["CalibrationHarness", "CalibrationReport"]


# ---------------------------------------------------------------------------
# CalibrationReport value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Immutable record of calibration metrics for one data window.

    Emitted by :meth:`CalibrationHarness.run` after collecting all fill events
    for a calibration window. Consumed by
    :class:`~hermes.application.calibration.lifecycle.CertificationLifecycle`
    to decide state transitions.

    Attributes:
        strategy_id: Identifier for the strategy variant being calibrated
            (e.g. ``"swing-mvp1-v1"``).
        window_start: UTC-aware start timestamp of the calibration window
            (inclusive).
        window_end: UTC-aware end timestamp of the calibration window (inclusive).
        m01_fill_count: M-01 — total number of successful fill events.
        m02_fill_price_delta_bps: M-02 — fill-price delta statistics in bps.
            Primary calibration metric. :class:`FillPriceDeltaBps` with avg,
            p50, p95, p99.
        m03_latency_ms: M-03 — fill latency in milliseconds.
            ``Decimal("0")`` for MVP1 backtest mode.
        m04_rejection_rate: M-04 — fraction of fill attempts rejected by the
            fill model. ``Decimal("0")`` when no attempts were made.
        m05_orphan_position_count: M-05 — SA-4 P0 guard. Count of open positions
            with no preceding fill event. ``0`` when the invariant is satisfied.
        m06_regime_returns: M-06 — cumulative return per regime bucket.
            Each bucket is ``None`` when no fills occurred in that regime.
        m07_queue_quality: M-07 — queue-position quality. Always ``None`` for
            MVP1 per OD-2. Real values available in shadow mode (Spec 6 follow-up).

    Reference: spec-6-calibration-harness/change.md D-1, R-1.
    """

    strategy_id: str
    window_start: datetime
    window_end: datetime
    m01_fill_count: int
    m02_fill_price_delta_bps: FillPriceDeltaBps
    m03_latency_ms: Decimal
    m04_rejection_rate: Decimal
    m05_orphan_position_count: int
    m06_regime_returns: RegimeReturns
    m07_queue_quality: Decimal | None

    def __post_init__(self) -> None:
        """Validate window timestamps are UTC-aware and window_end >= window_start.

        Raises:
            ValueError: On naive ``window_start`` / ``window_end``, or when
                ``window_end < window_start``.
        """
        if self.window_start.tzinfo is None:
            raise ValueError(
                f"CalibrationReport.window_start must be UTC-aware, "
                f"got naive {self.window_start!r}."
            )
        if self.window_end.tzinfo is None:
            raise ValueError(
                f"CalibrationReport.window_end must be UTC-aware, got naive {self.window_end!r}."
            )
        if self.window_end < self.window_start:
            raise ValueError(
                f"CalibrationReport.window_end {self.window_end!r} must be >= "
                f"window_start {self.window_start!r}."
            )
        if not self.strategy_id.strip():
            raise ValueError(
                "CalibrationReport.strategy_id must be a non-empty string — "
                "provide the experiment identifier (e.g. 'swing-mvp1-v1')."
            )


# ---------------------------------------------------------------------------
# CalibrationHarness
# ---------------------------------------------------------------------------


class CalibrationHarness:
    """Orchestrates calibration metric collection across a backtest data window.

    The harness is a thin orchestrator: it receives pre-collected fill events
    (from a ``SimulatedExchangeAdapter`` run), calls the domain metric calculators,
    and assembles a :class:`CalibrationReport`.

    For MVP1 the caller provides all inputs — fills, rejections, mid-prices,
    regime labels, open-position IDs, and fill-order IDs. The harness does NOT
    call any ports; it is the caller's responsibility to collect this data from
    the appropriate backtest adapters.

    This design matches the composition-root principle: the harness is wired by
    the operator; it does not decide WHERE data comes from.

    Args:
        strategy_id: Identifier for the strategy variant being calibrated.
        backtest_mode: When ``True`` (default), M-03 latency is mocked to 0.

    Reference: spec-6-calibration-harness/change.md R-4.
    """

    def __init__(self, *, strategy_id: str, backtest_mode: bool = True) -> None:
        """Initialise the harness.

        Args:
            strategy_id: Non-empty strategy identifier used in the report.
            backtest_mode: Whether to use backtest-mode M-03 (latency = 0).

        Raises:
            ValueError: When ``strategy_id`` is empty.
        """
        if not strategy_id.strip():
            raise ValueError(
                "CalibrationHarness.strategy_id must be non-empty — "
                "provide the experiment identifier (e.g. 'swing-mvp1-v1')."
            )
        self._strategy_id = strategy_id
        self._backtest_mode = backtest_mode

    def run(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        fills: Sequence[Fill],
        rejections: Sequence[Rejection],
        mid_prices: Sequence[Decimal],
        open_position_ids: Sequence[str] | None = None,
        fill_order_ids: Sequence[str] | None = None,
        regime_labels: Sequence[RegimeLabel | None] | None = None,
        entry_prices: Sequence[Decimal] | None = None,
        exit_prices: Sequence[Decimal] | None = None,
    ) -> CalibrationReport:
        """Run calibration over the provided fill events and return a report.

        Computes all seven metrics (M-01..M-07) and assembles a
        :class:`CalibrationReport`. All metric calculators are pure functions
        called in order.

        Args:
            window_start: UTC-aware start of the calibration window.
            window_end: UTC-aware end of the calibration window.
            fills: All successful fill events for the window. May be empty.
            rejections: All rejection outcomes for the window. May be empty.
            mid_prices: Reference mid-price at each fill time. Must have
                the same length as ``fills``. Each must be > 0.
            open_position_ids: Order IDs of currently open positions (M-05).
                Defaults to empty list when ``None``.
            fill_order_ids: Order IDs of all fills in the window (M-05).
                Defaults to the order IDs extracted from ``fills`` when ``None``.
            regime_labels: One :class:`RegimeLabel` (or ``None``) per fill (M-06).
                Defaults to all-``None`` when not provided.
            entry_prices: Entry price per fill for M-06. Must match ``fills``
                length. Defaults to ``fill.avg_price`` when ``None``.
            exit_prices: Exit price per fill for M-06 (price when position closed).
                Must match ``fills`` length. Defaults to ``fill.avg_price``
                (zero pnl) when ``None``.

        Returns:
            :class:`CalibrationReport` with all 7 metric values populated.

        Raises:
            ValueError: When ``len(fills) != len(mid_prices)``, or when
                ``window_end < window_start``, or when any timestamp is naive.

        Reference: spec-6-calibration-harness/change.md R-4.
        """
        # Resolve optional parameters
        _open_pos_ids: Sequence[str] = open_position_ids if open_position_ids is not None else []
        _fill_order_ids: Sequence[str] = (
            fill_order_ids
            if fill_order_ids is not None
            else [f.order.qty.asset + str(i) for i, f in enumerate(fills)]
        )
        _regime_labels: Sequence[RegimeLabel | None] = (
            regime_labels if regime_labels is not None else [None] * len(fills)
        )
        _entry_prices: Sequence[Decimal] = (
            entry_prices if entry_prices is not None else [f.avg_price for f in fills]
        )
        _exit_prices: Sequence[Decimal] = (
            exit_prices if exit_prices is not None else [f.avg_price for f in fills]
        )

        # M-01
        m01 = compute_m01_fill_count(fills)

        # M-02
        m02 = compute_m02_fill_price_delta_bps(fills, mid_prices)

        # M-03
        m03 = compute_m03_latency_ms(fills, backtest_mode=self._backtest_mode)

        # M-04
        m04 = compute_m04_rejection_rate(fills, rejections)

        # M-05
        m05 = compute_m05_orphan_positions(_open_pos_ids, _fill_order_ids)

        # M-06
        m06 = compute_m06_regime_returns(fills, _regime_labels, _entry_prices, _exit_prices)

        # M-07
        m07 = compute_m07_queue_quality(fills)

        return CalibrationReport(
            strategy_id=self._strategy_id,
            window_start=window_start,
            window_end=window_end,
            m01_fill_count=m01,
            m02_fill_price_delta_bps=m02,
            m03_latency_ms=m03,
            m04_rejection_rate=m04,
            m05_orphan_position_count=m05,
            m06_regime_returns=m06,
            m07_queue_quality=m07,
        )
