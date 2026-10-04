"""Observation figures: percentiles, histogram, drift verdict and gate constants.

Invariants:

- a percentile lies between the smallest and largest sample, grows with the
  percentile, and p50 is the median;
- every sample lands in exactly one histogram bucket;
- the drift verdict reads p50 of the absolute drift and fails at 30 bps or
  more (the §7 gate passes strictly below 30);
- the console thresholds equal the signed pre-registration v1 values.
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.observation import (
    DRIFT_GATE_BPS,
    DRIFT_SUSTAINED_DAYS,
    SHADOW_SHARPE_DAYS,
    SHADOW_SHARPE_FLOOR,
    SHOCK_FIRES,
    SHOCK_WINDOW_DAYS,
    SLIPPAGE_EDGES_BPS,
    WINDOW_DAYS,
    Distribution,
    DriftVerdict,
    drift_verdict,
    histogram,
    percentile,
    summarize,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration

_ROOT = next(
    p
    for p in [Path(__file__).resolve(), *Path(__file__).resolve().parents]
    if (p / "pyproject.toml").exists()
)
_SAMPLES = st.lists(
    st.decimals(min_value=-1000, max_value=1000, places=2, allow_nan=False, allow_infinity=False),
    min_size=1,
    max_size=40,
)


def _d(*values: str) -> list[Decimal]:
    return [Decimal(v) for v in values]


def test_percentile_interpolates_between_ranks() -> None:
    """p95 of 1..10 is 9.55 under linear interpolation (numpy's default)."""
    assert percentile(_d(*(str(i) for i in range(1, 11))), 95) == Decimal("9.55")


def test_percentile_of_even_count_median_is_the_midpoint() -> None:
    """p50 of four samples is the mean of the middle two, whatever the input order."""
    assert percentile(_d("8", "2", "6", "4"), 50) == Decimal(5)


def test_percentile_of_one_sample_is_that_sample() -> None:
    """A single sample is every percentile."""
    assert percentile(_d("7.3"), 95) == Decimal("7.3")


def test_percentile_of_no_samples_is_refused() -> None:
    """An empty sample set has no percentile."""
    with pytest.raises(ValueError, match="no samples"):
        percentile([], 50)


@pytest.mark.parametrize("pct", [-1, 101])
def test_percentile_outside_0_to_100_is_refused(pct: int) -> None:
    """Percentiles outside 0..100 are a caller bug."""
    with pytest.raises(ValueError, match=r"within 0..100"):
        percentile(_d("1"), pct)


@given(_SAMPLES, st.integers(min_value=0, max_value=100))
def test_percentile_stays_within_the_samples(samples: list[Decimal], pct: int) -> None:
    """Interpolation never leaves the range of the samples."""
    assert min(samples) <= percentile(samples, pct) <= max(samples)


@given(_SAMPLES, st.integers(min_value=0, max_value=99))
def test_percentile_never_falls_as_the_percentile_rises(samples: list[Decimal], pct: int) -> None:
    """A higher percentile is never below a lower one."""
    assert percentile(samples, pct) <= percentile(samples, pct + 1)


@given(_SAMPLES)
def test_p50_is_the_median(samples: list[Decimal]) -> None:
    """p50 equals the statistics module's median."""
    assert percentile(samples, 50) == statistics.median(samples)


def test_summary_of_no_samples_is_none() -> None:
    """No samples, no summary: the panel prints a dash instead of a zero."""
    four = Decimal(4)
    assert (summarize([]), summarize([four])) == (None, Distribution(1, four, four, four))


def test_summary_carries_count_p50_p95_and_max() -> None:
    """The summary reports the four figures the panel shows."""
    summary = summarize(_d("1", "3", "2"))
    assert summary is not None
    assert (summary.count, summary.p50, summary.max) == (3, Decimal(2), Decimal(3))


def test_histogram_buckets_are_half_open() -> None:
    """A sample on an edge goes to the bucket that edge starts."""
    assert histogram(_d("-1", "0", "4.9", "5", "20", "99"), SLIPPAGE_EDGES_BPS) == (1, 2, 1, 0, 2)


@given(_SAMPLES)
def test_histogram_counts_every_sample_once(samples: list[Decimal]) -> None:
    """The bucket counts add up to the number of samples."""
    assert sum(histogram(samples, SLIPPAGE_EDGES_BPS)) == len(samples)


def test_histogram_refuses_unordered_edges() -> None:
    """Edges out of order would double-count; they are refused."""
    with pytest.raises(ValueError, match="ascending"):
        histogram(_d("1"), _d("5", "5"))


def test_no_drift_samples_means_no_live_fills_yet() -> None:
    """Without a live-book fill the verdict is neither pass nor fail."""
    assert drift_verdict([]) is DriftVerdict.NO_FILLS


def test_drift_at_the_gate_fails() -> None:
    """The gate passes strictly below 30 bps, so p50 = 30 fails."""
    assert drift_verdict(_d("30", "30", "30")) is DriftVerdict.FAIL


def test_drift_just_below_the_gate_is_at_risk() -> None:
    """p50 = 29.9 bps still passes the gate but is flagged at risk."""
    assert drift_verdict(_d("29.9")) is DriftVerdict.AT_RISK


def test_drift_at_the_warning_line_is_at_risk() -> None:
    """The at-risk band starts at 24 bps (80% of the gate)."""
    assert drift_verdict(_d("24")) is DriftVerdict.AT_RISK


def test_drift_below_the_warning_line_passes() -> None:
    """p50 = 23.9 bps passes."""
    assert drift_verdict(_d("23.9")) is DriftVerdict.PASS


def test_negative_drift_counts_by_its_size() -> None:
    """A fill 35 bps better than the model is as far off as one 35 bps worse."""
    assert drift_verdict(_d("-35", "-35", "-35")) is DriftVerdict.FAIL


@pytest.fixture(scope="module")
def pre_reg() -> PreRegistration:
    """Return the signed pre-registration of the repository."""
    return PreRegistration(project_root=_ROOT)


def test_drift_gate_equals_the_signed_section_7_value(pre_reg: PreRegistration) -> None:
    """The panel's gate is the §7 SHADOW_CERTIFIED drift threshold."""
    assert float(DRIFT_GATE_BPS) == pre_reg.certification_schedule().shadow_drift_p50_max_bps


def test_window_length_equals_the_signed_shadow_days(pre_reg: PreRegistration) -> None:
    """The window is the §7 90-day shadow run."""
    assert pre_reg.certification_schedule().shadow_min_days == WINDOW_DAYS


def test_sharpe_kill_limits_equal_the_signed_section_10_values(pre_reg: PreRegistration) -> None:
    """The Sharpe kill trigger reads the §10 floor and day count."""
    kill = pre_reg.kill_triggers()
    assert (float(SHADOW_SHARPE_FLOOR), SHADOW_SHARPE_DAYS) == (
        kill.rolling_sharpe_min,
        kill.shadow_sharpe_consecutive_days,
    )


def test_drift_and_shock_kill_limits_match_the_signed_text() -> None:
    """The §10 limits without an accessor match the signed wording."""
    text = (_ROOT / "docs/experiments/swing-mvp1/pre-registration-v1.md").read_text(
        encoding="utf-8"
    )
    expected = (
        f"> {DRIFT_GATE_BPS} bps p50 sustained {DRIFT_SUSTAINED_DAYS} rolling days",
        f"fires {SHOCK_FIRES}+ times in {SHOCK_WINDOW_DAYS} days",
    )
    assert [phrase for phrase in expected if phrase not in text] == []
