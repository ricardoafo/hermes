"""Unit tests for CalibrationReport and CalibrationHarness validation.

Tests cover:
  - CalibrationReport.__post_init__ raises ValueError on naive window_start
  - CalibrationReport.__post_init__ raises ValueError on naive window_end
  - CalibrationReport.__post_init__ raises ValueError when window_end < window_start
  - CalibrationReport.__post_init__ raises ValueError on blank strategy_id
  - CalibrationHarness.__init__ raises ValueError on blank strategy_id

Reference: spec-6-calibration-harness/change.md R-4.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.application.calibration.harness import CalibrationHarness, CalibrationReport
from hermes.domain.calibration.metrics import FillPriceDeltaBps

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WIN_START = datetime(2024, 1, 1, tzinfo=UTC)
_WIN_END = datetime(2024, 1, 31, tzinfo=UTC)

_ZERO_BPS = FillPriceDeltaBps(
    avg_bps=Decimal("0"),
    p50_bps=Decimal("0"),
    p95_bps=Decimal("0"),
    p99_bps=Decimal("0"),
)


def _make_report(**overrides: object) -> CalibrationReport:
    """Build a minimal valid CalibrationReport with overrideable fields."""
    defaults: dict[str, object] = {
        "strategy_id": "swing-v1",
        "window_start": _WIN_START,
        "window_end": _WIN_END,
        "m01_fill_count": 0,
        "m02_fill_price_delta_bps": _ZERO_BPS,
        "m03_latency_ms": Decimal("0"),
        "m04_rejection_rate": Decimal("0"),
        "m05_orphan_position_count": 0,
        "m06_regime_returns": None,
        "m07_queue_quality": None,
    }
    defaults.update(overrides)
    return CalibrationReport(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CalibrationReport validation
# ---------------------------------------------------------------------------


class TestCalibrationReportValidation:
    """CalibrationReport.__post_init__ rejects invalid timestamp and strategy_id inputs."""

    def test_naive_window_start_raises_value_error(self) -> None:
        """CalibrationReport must raise ValueError when window_start has no tzinfo."""
        naive_start = datetime(2024, 1, 1)  # timezone-naive
        with pytest.raises(ValueError, match="window_start"):
            _make_report(window_start=naive_start)

    def test_naive_window_end_raises_value_error(self) -> None:
        """CalibrationReport must raise ValueError when window_end has no tzinfo."""
        naive_end = datetime(2024, 1, 31)  # timezone-naive
        with pytest.raises(ValueError, match="window_end"):
            _make_report(window_end=naive_end)

    def test_inverted_window_raises_value_error(self) -> None:
        """CalibrationReport must raise ValueError when window_end < window_start."""
        with pytest.raises(ValueError, match="window_end"):
            _make_report(
                window_start=datetime(2024, 1, 31, tzinfo=UTC),
                window_end=datetime(2024, 1, 1, tzinfo=UTC),
            )

    def test_blank_strategy_id_raises_value_error(self) -> None:
        """CalibrationReport must raise ValueError when strategy_id is whitespace."""
        with pytest.raises(ValueError, match="strategy_id"):
            _make_report(strategy_id="   ")

    def test_empty_strategy_id_raises_value_error(self) -> None:
        """CalibrationReport must raise ValueError when strategy_id is empty string."""
        with pytest.raises(ValueError, match="strategy_id"):
            _make_report(strategy_id="")


# ---------------------------------------------------------------------------
# CalibrationHarness validation
# ---------------------------------------------------------------------------


class TestCalibrationHarnessValidation:
    """CalibrationHarness.__init__ rejects blank strategy_id."""

    def test_empty_strategy_id_raises_value_error(self) -> None:
        """CalibrationHarness must raise ValueError when strategy_id is empty."""
        with pytest.raises(ValueError, match="strategy_id"):
            CalibrationHarness(strategy_id="")

    def test_whitespace_strategy_id_raises_value_error(self) -> None:
        """CalibrationHarness must raise ValueError when strategy_id is whitespace."""
        with pytest.raises(ValueError, match="strategy_id"):
            CalibrationHarness(strategy_id="   ")
