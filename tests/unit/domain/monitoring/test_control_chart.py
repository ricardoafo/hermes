"""Unit tests for the EWMA + CUSUM control chart (#362, ADR-005 Category B)."""

import math
from collections.abc import Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.monitoring.control_chart import ChartParameters, ControlChart, Signal

_BASELINE = [10.0, 11.0, 9.0, 10.5, 9.5] * 6
"""30 points: mean 10.0, sample sigma about 0.70."""


def _calibrated() -> ControlChart:
    chart = ControlChart()
    for value in _BASELINE:
        assert chart.observe(value) is None
    return chart


def test_baseline_never_alerts_and_then_calibrates() -> None:
    """Phase I takes 30 observations and raises nothing, even for extreme values."""
    chart = ControlChart()
    results = [chart.observe(v) for v in [*_BASELINE[:29], 1_000.0]]
    assert (results.count(None), chart.calibrated) == (30, True)


def test_in_control_values_never_alert() -> None:
    """Values repeating the baseline pattern stay in control."""
    chart = _calibrated()
    breaches = [chart.observe(v) for v in _BASELINE * 10]
    assert breaches.count(None) == 300


def test_step_shift_gives_exactly_one_alert_per_episode() -> None:
    """A sustained jump alerts once on entry, not once per point (issue #362 acceptance)."""
    chart = _calibrated()
    breaches = [chart.observe(20.0) for _ in range(50)]
    alerts = [b for b in breaches if b is not None]
    assert len(alerts) == 1
    assert alerts[0].value == 20.0


def test_second_episode_alerts_again_after_recovery() -> None:
    """After returning in control, a new excursion is a new episode with a new alert."""
    chart = _calibrated()
    first = [chart.observe(20.0) for _ in range(10)]
    recovery = [chart.observe(v) for v in _BASELINE * 20]
    second = [chart.observe(20.0) for _ in range(10)]
    counts = [sum(b is not None for b in part) for part in (first, recovery, second)]
    assert counts == [1, 0, 1]


def test_high_shift_reports_high_signals() -> None:
    """An upward shift reports EWMA_HIGH and/or CUSUM_HIGH, never a low signal."""
    chart = _calibrated()
    alert = next(b for b in (chart.observe(20.0) for _ in range(10)) if b is not None)
    assert set(alert.signals) <= {Signal.EWMA_HIGH, Signal.CUSUM_HIGH}
    assert alert.signals


def test_small_drift_is_caught_by_cusum() -> None:
    """A one-sigma upward drift, too small for most single points, is detected."""
    chart = _calibrated()
    breaches = [chart.observe(10.7) for _ in range(40)]
    first = next(i for i, b in enumerate(breaches) if b is not None)
    assert first < 20


def test_flat_baseline_still_detects_a_change() -> None:
    """A constant fee rate gives zero sigma; a later change must still alert."""
    chart = ControlChart()
    for _ in range(30):
        chart.observe(10.0)
    breach = chart.observe(10.5)
    assert breach is not None
    assert (breach.value, breach.baseline_mean) == (10.5, 10.0)


def test_non_finite_values_are_rejected() -> None:
    """NaN would silently poison the statistics, so it is refused."""
    with pytest.raises(ValueError, match="finite"):
        ControlChart().observe(math.nan)


@pytest.mark.parametrize(
    "build",
    [
        lambda: ChartParameters(baseline_size=1),
        lambda: ChartParameters(ewma_lambda=0.0),
        lambda: ChartParameters(ewma_l=0.0),
        lambda: ChartParameters(cusum_k=-1.0),
    ],
)
def test_invalid_parameters_raise(build: Callable[[], ChartParameters]) -> None:
    """Degenerate chart settings are caller errors."""
    with pytest.raises(ValueError, match="must"):
        build()


@settings(max_examples=50)
@given(st.lists(st.floats(min_value=9.0, max_value=11.0), min_size=1, max_size=200))
def test_values_inside_baseline_range_rarely_alert(values: list[float]) -> None:
    """Values inside the baseline's own range produce at most a handful of episodes."""
    chart = _calibrated()
    alerts = sum(chart.observe(v) is not None for v in values)
    assert alerts <= len(values) // 10 + 1
