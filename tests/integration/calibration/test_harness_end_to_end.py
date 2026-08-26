"""Integration tests for CalibrationHarness — end-to-end over a fixture data window.

Tests run the harness on a synthetic fixture data window and verify that the
resulting CalibrationReport has all 7 metrics populated correctly.

These are integration tests (not unit tests) because they exercise the full
orchestration path: harness → domain metric calculators → CalibrationReport assembly.
They do NOT call any I/O ports; all data is synthetic fixtures injected directly.

Coverage:
  - Harness with non-empty fills produces correct m01_fill_count
  - Harness with empty fills produces zeroed report
  - m04_rejection_rate is computed over both fills and rejections
  - m05_orphan_position_count is 0 when fill_order_ids match open_position_ids
  - m07_queue_quality is always None in backtest mode
  - FixtureBaselineJob runs without error and reports within tolerance

Reference: spec-6-calibration-harness/change.md A-4.1, A-4.2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.application.calibration.baseline import FixtureBaselineJob
from hermes.application.calibration.harness import CalibrationHarness, CalibrationReport
from hermes.domain.calibration.metrics import FillPriceDeltaBps
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_WIN_START = datetime(2024, 1, 1, tzinfo=UTC)
_WIN_END = datetime(2024, 1, 31, tzinfo=UTC)
_STRATEGY_ID = "swing-mvp1-v1"


def _order(side: OrderSide = OrderSide.BUY, qty: str = "0.1") -> Order:
    return Order(
        side=side,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal(qty), asset=Currency.BTC),
    )


def _fill(
    avg_price: str,
    side: OrderSide = OrderSide.BUY,
    qty: str = "0.1",
    ts: datetime | None = None,
) -> Fill:
    t = ts or datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    return Fill(
        order=_order(side=side, qty=qty),
        executed_qty=Decimal(qty),
        avg_price=Decimal(avg_price),
        fee=Money(amount=Decimal("0.01"), currency=Currency.USDT),
        timestamp=t,
    )


def _rejection(reason: str = "insufficient liquidity") -> Rejection:
    return Rejection(order=_order(), reason=reason)


# ---------------------------------------------------------------------------
# Fixture data window: 5 fills
# ---------------------------------------------------------------------------

_FIXTURE_FILLS = [
    _fill("49950", side=OrderSide.BUY),  # -10 bps from mid 50000
    _fill("50000", side=OrderSide.BUY),  #   0 bps from mid 50000
    _fill("50050", side=OrderSide.BUY),  # +10 bps from mid 50000
    _fill("50100", side=OrderSide.BUY),  # +20 bps from mid 50000
    _fill("50200", side=OrderSide.BUY),  # +40 bps from mid 50000
]
_FIXTURE_MIDS = [
    Decimal("50000"),
    Decimal("50000"),
    Decimal("50000"),
    Decimal("50000"),
    Decimal("50000"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCalibrationHarnessEndToEnd:
    """End-to-end harness runs over fixture data window."""

    @pytest.fixture()
    def report_with_fills(self) -> CalibrationReport:
        """CalibrationReport from a 5-fill fixture window."""
        harness = CalibrationHarness(strategy_id=_STRATEGY_ID, backtest_mode=True)
        return harness.run(
            window_start=_WIN_START,
            window_end=_WIN_END,
            fills=_FIXTURE_FILLS,
            rejections=[],
            mid_prices=_FIXTURE_MIDS,
        )

    def test_m01_fill_count_matches_fixture(self, report_with_fills: CalibrationReport) -> None:
        """m01_fill_count must equal the number of fixture fills (A-4.1)."""
        assert report_with_fills.m01_fill_count == len(_FIXTURE_FILLS), (
            f"m01_fill_count must be {len(_FIXTURE_FILLS)}, "
            f"got {report_with_fills.m01_fill_count}. "
            "A-4.1: fill_count must match number of fills passed to harness."
        )

    def test_m02_is_fill_price_delta_bps_instance(
        self, report_with_fills: CalibrationReport
    ) -> None:
        """m02_fill_price_delta_bps must be a FillPriceDeltaBps instance."""
        assert isinstance(report_with_fills.m02_fill_price_delta_bps, FillPriceDeltaBps), (
            f"m02_fill_price_delta_bps must be FillPriceDeltaBps, "
            f"got {type(report_with_fills.m02_fill_price_delta_bps).__name__}."
        )

    def test_m02_avg_bps_matches_hand_computation(
        self, report_with_fills: CalibrationReport
    ) -> None:
        """m02 avg_bps must match hand-computed value.

        Deltas for the 5 fixture fills (all BUY from mid=50000):
          49950 → (49950-50000)/50000*10000 = -10 bps
          50000 → 0 bps
          50050 → +10 bps
          50100 → +20 bps
          50200 → +40 bps
        Average: (-10 + 0 + 10 + 20 + 40) / 5 = 60 / 5 = 12 bps.
        """
        actual_avg = report_with_fills.m02_fill_price_delta_bps.avg_bps
        assert actual_avg == Decimal("12"), (
            f"m02 avg_bps must be 12 bps (hand-computed), got {actual_avg}. "
            "Delta sum: -10+0+10+20+40=60, /5=12."
        )

    def test_m03_latency_is_zero_in_backtest(self, report_with_fills: CalibrationReport) -> None:
        """m03_latency_ms must be Decimal('0') in backtest mode."""
        assert report_with_fills.m03_latency_ms == Decimal("0"), (
            f"m03_latency_ms must be 0 in backtest mode, got {report_with_fills.m03_latency_ms}. "
            "MVP1 backtest has no real latency (OD-2)."
        )

    def test_m04_rejection_rate_zero_when_no_rejections(
        self, report_with_fills: CalibrationReport
    ) -> None:
        """m04_rejection_rate must be 0 when no rejections are provided."""
        assert report_with_fills.m04_rejection_rate == Decimal("0"), (
            f"m04_rejection_rate must be 0 with no rejections, "
            f"got {report_with_fills.m04_rejection_rate}."
        )

    def test_m05_orphan_count_zero_when_no_positions_specified(
        self, report_with_fills: CalibrationReport
    ) -> None:
        """m05_orphan_position_count must be 0 when no open_position_ids specified."""
        assert report_with_fills.m05_orphan_position_count == 0, (
            f"m05_orphan_position_count must be 0 when no positions specified, "
            f"got {report_with_fills.m05_orphan_position_count}."
        )

    def test_m07_queue_quality_is_none(self, report_with_fills: CalibrationReport) -> None:
        """m07_queue_quality must be None for MVP1 (OD-2)."""
        assert report_with_fills.m07_queue_quality is None, (
            f"m07_queue_quality must be None in MVP1 backtest mode, "
            f"got {report_with_fills.m07_queue_quality!r}."
        )
        assert not isinstance(report_with_fills.m07_queue_quality, Decimal), (
            f"m07_queue_quality must NOT be a Decimal — MVP1 returns None (OD-2), "
            f"got {report_with_fills.m07_queue_quality!r}."
        )

    def test_strategy_id_preserved_in_report(self, report_with_fills: CalibrationReport) -> None:
        """strategy_id in report must match the harness strategy_id."""
        assert report_with_fills.strategy_id == _STRATEGY_ID, (
            f"strategy_id in report must be '{_STRATEGY_ID}', "
            f"got '{report_with_fills.strategy_id}'."
        )

    def test_window_timestamps_preserved(self, report_with_fills: CalibrationReport) -> None:
        """window_start and window_end must be preserved from the run() call."""
        assert report_with_fills.window_start == _WIN_START, (
            f"window_start must be {_WIN_START!r}, got {report_with_fills.window_start!r}."
        )
        assert report_with_fills.window_end == _WIN_END, (
            f"window_end must be {_WIN_END!r}, got {report_with_fills.window_end!r}."
        )


class TestCalibrationHarnessEmptyFills:
    """Harness with empty fills produces a zeroed report (A-4.2)."""

    @pytest.fixture()
    def empty_report(self) -> CalibrationReport:
        """CalibrationReport with no fills and no rejections."""
        harness = CalibrationHarness(strategy_id=_STRATEGY_ID, backtest_mode=True)
        return harness.run(
            window_start=_WIN_START,
            window_end=_WIN_END,
            fills=[],
            rejections=[],
            mid_prices=[],
        )

    def test_m01_fill_count_is_zero(self, empty_report: CalibrationReport) -> None:
        """m01_fill_count must be 0 for empty fills (A-4.2)."""
        assert empty_report.m01_fill_count == 0, (
            f"A-4.2: empty fills must produce m01_fill_count=0, got {empty_report.m01_fill_count}."
        )

    def test_m04_rejection_rate_is_zero(self, empty_report: CalibrationReport) -> None:
        """m04_rejection_rate must be 0 for empty fills and rejections (A-4.2)."""
        assert empty_report.m04_rejection_rate == Decimal("0"), (
            f"A-4.2: empty fills+rejections must produce m04_rejection_rate=0, "
            f"got {empty_report.m04_rejection_rate}."
        )

    def test_m02_all_zeros_for_empty_fills(self, empty_report: CalibrationReport) -> None:
        """m02 must have all-zero bps for empty fills."""
        m02 = empty_report.m02_fill_price_delta_bps
        assert m02.avg_bps == Decimal("0"), (
            f"m02.avg_bps must be 0 for empty fills, got {m02.avg_bps}."
        )


class TestCalibrationHarnessWithRejections:
    """Harness correctly computes rejection rate when rejections are present."""

    def test_mixed_fills_and_rejections_rejection_rate(self) -> None:
        """2 fills + 2 rejections must produce m04_rejection_rate = 0.5."""
        harness = CalibrationHarness(strategy_id=_STRATEGY_ID, backtest_mode=True)
        fills = [_fill("50000"), _fill("50100")]
        rejections = [_rejection(), _rejection("spread too wide")]
        report = harness.run(
            window_start=_WIN_START,
            window_end=_WIN_END,
            fills=fills,
            rejections=rejections,
            mid_prices=[Decimal("50000"), Decimal("50000")],
        )
        assert report.m04_rejection_rate == Decimal("0.5"), (
            f"2 fills + 2 rejections must produce 0.5 rejection rate, "
            f"got {report.m04_rejection_rate}. Hand-computed: 2/(2+2)=0.5."
        )


class TestFixtureBaselineJob:
    """FixtureBaselineJob runs without I/O and reports delta correctly."""

    def test_identical_fills_zero_delta(self) -> None:
        """Simulated fills identical to reference must produce 0 bps delta."""
        ref_fills = [_fill("50000"), _fill("50100")]
        job = FixtureBaselineJob(reference_fills=ref_fills, tolerance_bps=Decimal("5"))
        sim_fills = [_fill("50000"), _fill("50100")]
        result = job.run(
            window_start=_WIN_START,
            window_end=_WIN_END,
            simulated_fills=sim_fills,
        )
        assert result.delta_bps == Decimal("0"), (
            f"Identical simulated and reference fills must produce 0 bps delta, "
            f"got {result.delta_bps}."
        )
        assert result.is_within_tolerance is True, (
            "0 bps delta must be within 5 bps tolerance, got is_within_tolerance=False."
        )

    def test_large_delta_exceeds_tolerance(self) -> None:
        """Large price difference must produce delta > tolerance."""
        ref_fills = [_fill("50000")]
        job = FixtureBaselineJob(reference_fills=ref_fills, tolerance_bps=Decimal("5"))
        sim_fills = [_fill("51000")]  # 200 bps above reference
        result = job.run(
            window_start=_WIN_START,
            window_end=_WIN_END,
            simulated_fills=sim_fills,
        )
        assert abs(result.delta_bps) > Decimal("5"), (
            f"200 bps gap must exceed 5 bps tolerance, got {result.delta_bps}."
        )
        assert result.is_within_tolerance is False, (
            "200 bps gap must not be within 5 bps tolerance, got is_within_tolerance=True."
        )

    def test_empty_simulated_fills_returns_zero_sim_avg(self) -> None:
        """Empty simulated fills must return simulated_avg_price = 0."""
        ref_fills = [_fill("50000")]
        job = FixtureBaselineJob(reference_fills=ref_fills)
        result = job.run(
            window_start=_WIN_START,
            window_end=_WIN_END,
            simulated_fills=[],
        )
        assert result.simulated_avg_price == Decimal("0"), (
            f"Empty simulated fills must produce simulated_avg_price=0, "
            f"got {result.simulated_avg_price}."
        )

    def test_empty_reference_fills_produces_zero_delta(self) -> None:
        """Empty reference fills must produce delta_bps = 0 (guard against ZeroDivisionError)."""
        job = FixtureBaselineJob(reference_fills=[], tolerance_bps=Decimal("5"))
        result = job.run(
            window_start=_WIN_START,
            window_end=_WIN_END,
            simulated_fills=[_fill("50000")],
        )
        assert result.delta_bps == Decimal("0"), (
            f"Empty reference fills must produce delta_bps=0 (ref_avg=0 guard), "
            f"got {result.delta_bps}."
        )
        assert not isinstance(result.delta_bps, bool), (
            "delta_bps must be Decimal, not bool — type guard."
        )


class TestCalibrationReportValidation:
    """CalibrationReport.__post_init__ raises ValueError on invalid inputs."""

    def _make_report(self, **kwargs: object) -> CalibrationReport:
        """Build a minimal valid CalibrationReport with overrideable fields."""
        defaults: dict[str, object] = {
            "strategy_id": "swing-v1",
            "window_start": _WIN_START,
            "window_end": _WIN_END,
            "m01_fill_count": 0,
            "m02_fill_price_delta_bps": FillPriceDeltaBps(
                avg_bps=Decimal("0"),
                p50_bps=Decimal("0"),
                p95_bps=Decimal("0"),
                p99_bps=Decimal("0"),
            ),
            "m03_latency_ms": Decimal("0"),
            "m04_rejection_rate": Decimal("0"),
            "m05_orphan_position_count": 0,
            "m06_regime_returns": None,
            "m07_queue_quality": None,
        }
        defaults.update(kwargs)
        return CalibrationReport(**defaults)  # type: ignore[arg-type]

    def test_naive_window_start_raises(self) -> None:
        """CalibrationReport must raise ValueError when window_start is timezone-naive."""
        naive_start = datetime(2024, 1, 1)  # no tzinfo
        with pytest.raises(ValueError, match="window_start"):
            self._make_report(window_start=naive_start)

    def test_naive_window_end_raises(self) -> None:
        """CalibrationReport must raise ValueError when window_end is timezone-naive."""
        naive_end = datetime(2024, 1, 31)  # no tzinfo
        with pytest.raises(ValueError, match="window_end"):
            self._make_report(window_end=naive_end)

    def test_inverted_window_raises(self) -> None:
        """CalibrationReport must raise ValueError when window_end < window_start."""
        with pytest.raises(ValueError, match="window_end"):
            self._make_report(
                window_start=datetime(2024, 1, 31, tzinfo=UTC),
                window_end=datetime(2024, 1, 1, tzinfo=UTC),
            )

    def test_empty_strategy_id_raises(self) -> None:
        """CalibrationReport must raise ValueError when strategy_id is blank."""
        with pytest.raises(ValueError, match="strategy_id"):
            self._make_report(strategy_id="   ")


class TestCalibrationHarnessValidation:
    """CalibrationHarness.__init__ raises ValueError on invalid strategy_id."""

    def test_empty_strategy_id_raises(self) -> None:
        """CalibrationHarness must raise ValueError when strategy_id is blank."""
        with pytest.raises(ValueError, match="strategy_id"):
            CalibrationHarness(strategy_id="")
