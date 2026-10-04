"""Reference and boundary tests for the shadow control charts (#581).

A mutation-testing run over ``hermes.domain.monitoring.control_chart`` showed
that the existing tests checked whether a chart alerts, but not when, with
which signals, or against which baseline. These tests pin all three: every
observation's output must equal a plain re-statement of the EWMA chart and
the two-sided tabular CUSUM (Montgomery, ch. 9) on deterministic sequences,
and every parameter check is tested on both sides of its edge.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import FrozenInstanceError

import pytest

from hermes.domain.monitoring.control_chart import Breach, ChartParameters, ControlChart, Signal
from hermes.domain.statistics.rng import SplitMix64

type Output = tuple[tuple[Signal, ...], float, float, float] | None


def _reference_outputs(params: ChartParameters, values: Sequence[float]) -> list[Output]:
    baseline = list(values[: params.baseline_size])
    outputs: list[Output] = [None] * len(baseline)
    mean = statistics.fmean(baseline)
    sd = statistics.stdev(baseline)
    sigma = sd if sd > 0.0 else max(abs(mean) * 1e-6, 1e-12)
    lam, ewma, high, low, was_out = params.ewma_lambda, mean, 0.0, 0.0, False
    for t, x in enumerate(values[params.baseline_size :], start=1):
        ewma = lam * x + (1.0 - lam) * ewma
        width = (
            params.ewma_l * sigma * math.sqrt(lam / (2.0 - lam) * (1.0 - (1.0 - lam) ** (2 * t)))
        )
        signals: list[Signal] = []
        if ewma > mean + width:
            signals.append(Signal.EWMA_HIGH)
        elif ewma < mean - width:
            signals.append(Signal.EWMA_LOW)
        z = (x - mean) / sigma
        high = max(0.0, high + z - params.cusum_k)
        low = max(0.0, low - z - params.cusum_k)
        if high > params.cusum_h:
            signals.append(Signal.CUSUM_HIGH)
        if low > params.cusum_h:
            signals.append(Signal.CUSUM_LOW)
        is_out = bool(signals)
        outputs.append((tuple(signals), x, mean, sigma) if is_out and not was_out else None)
        was_out = is_out
    return outputs


def _chart_outputs(params: ChartParameters, values: Sequence[float]) -> list[Output]:
    chart = ControlChart(params)
    outputs: list[Output] = []
    for x in values:
        breach = chart.observe(x)
        outputs.append(
            None
            if breach is None
            else (breach.signals, breach.value, breach.baseline_mean, breach.baseline_sigma)
        )
    return outputs


def _noise(seed: int, n: int, scale: float = 1.0) -> list[float]:
    rng = SplitMix64(seed)
    return [scale * (rng.below(2001) - 1000) / 1000.0 for _ in range(n)]


def _episodes(seed: int) -> list[float]:
    """Baseline, then short bursts up and down and a modest step, each followed by calm.

    The CUSUM sums are not reset on a signal, so bursts are kept short enough for
    each episode to end before the next one starts.
    """
    noise = _noise(seed, 220)
    shifts = [0.0] * 30 + [3.0] * 4 + [0.0] * 50 + [-3.0] * 4 + [0.0] * 50 + [1.5] * 12 + [0.0] * 70
    return [10.0 + n + s for n, s in zip(noise, shifts, strict=True)]


class TestMatchesReference:
    """Every observation's output equals the textbook EWMA and tabular CUSUM."""

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_default_parameters(self, seed: int) -> None:
        """Defaults on shifts both ways and a drift: same breaches, at the same steps."""
        values = _episodes(seed)
        expected = _reference_outputs(ChartParameters(), values)
        assert _chart_outputs(ChartParameters(), values) == expected
        assert sum(o is not None for o in expected) >= 3

    def test_custom_parameters(self) -> None:
        """λ = 1, L = 2, k = 0.75, h = 2 on a 10-point baseline: every setting is read."""
        params = ChartParameters(
            baseline_size=10, ewma_lambda=1.0, ewma_l=2.0, cusum_k=0.75, cusum_h=2.0
        )
        values = _episodes(7)
        expected = _reference_outputs(params, values)
        assert _chart_outputs(params, values) == expected
        assert sum(o is not None for o in expected) >= 3

    def test_breach_on_the_first_monitored_point(self) -> None:
        """A jump right after the baseline alerts at once: a chart starts in control."""
        values = [*_noise(11, 30), 50.0]
        outputs = _chart_outputs(ChartParameters(), values)
        assert outputs[-1] == _reference_outputs(ChartParameters(), values)[-1]
        assert outputs[-1] is not None


class TestFlatBaselineSigma:
    """A flat baseline floors sigma so a later change still signals."""

    def test_floor_scales_with_the_mean(self) -> None:
        """A baseline flat at 0.001 floors sigma at 1e-6 of the mean, 1e-9."""
        chart = ControlChart(ChartParameters(baseline_size=2))
        chart.observe(0.001)
        chart.observe(0.001)
        breach = chart.observe(0.002)
        assert breach is not None
        assert breach.baseline_sigma == pytest.approx(1e-9, rel=1e-12)

    def test_floor_has_an_absolute_minimum(self) -> None:
        """A baseline flat at 0 floors sigma at 1e-12."""
        chart = ControlChart(ChartParameters(baseline_size=2))
        chart.observe(0.0)
        chart.observe(0.0)
        breach = chart.observe(1.0)
        assert breach is not None
        assert breach.baseline_sigma == (1e-12)


class TestParameters:
    """Defaults and validation edges."""

    def test_defaults_are_the_textbook_values(self) -> None:
        """30-point baseline, λ 0.2 with L 3, CUSUM k 0.5 and h 5."""
        p = ChartParameters()
        assert (p.baseline_size, p.ewma_lambda, p.ewma_l, p.cusum_k, p.cusum_h) == (
            30,
            0.2,
            3.0,
            0.5,
            5.0,
        )

    @pytest.mark.parametrize(
        "build",
        [
            lambda: ChartParameters(baseline_size=2),
            lambda: ChartParameters(ewma_lambda=1.0),
            lambda: ChartParameters(cusum_k=0.0),
            lambda: ChartParameters(ewma_l=0.5, cusum_h=0.5),
        ],
    )
    def test_edge_values_are_valid(self, build: Callable[[], ChartParameters]) -> None:
        """A 2-point baseline, λ = 1, k = 0 and widths under 1 are all valid."""
        assert isinstance(build(), ChartParameters)

    @pytest.mark.parametrize(
        "build",
        [
            lambda: ChartParameters(baseline_size=1),
            lambda: ChartParameters(ewma_lambda=0.0),
            lambda: ChartParameters(ewma_lambda=-0.5),
            lambda: ChartParameters(ewma_lambda=1.5),
            lambda: ChartParameters(ewma_l=0.0),
            lambda: ChartParameters(ewma_l=-1.0),
            lambda: ChartParameters(cusum_h=0.0),
            lambda: ChartParameters(cusum_k=-0.1),
        ],
    )
    def test_out_of_range_values_raise(self, build: Callable[[], ChartParameters]) -> None:
        """A 1-point baseline, λ outside (0, 1], a zero width or a negative k is refused."""
        with pytest.raises(ValueError, match="must be"):
            build()

    def test_parameters_are_frozen(self) -> None:
        """Settings cannot change under a running chart."""
        params = ChartParameters()
        with pytest.raises(FrozenInstanceError):
            params.cusum_h = 1.0  # type: ignore[misc]

    def test_parameters_have_slots(self) -> None:
        """ChartParameters is slotted, like every value object in the domain."""
        assert not hasattr(ChartParameters(), "__dict__")


class TestBreachRecord:
    """A breach is an immutable record."""

    def test_breach_is_frozen(self) -> None:
        """An alert's figures cannot be edited after the fact."""
        breach = Breach((Signal.CUSUM_HIGH,), 1.0, 0.0, 1.0)
        with pytest.raises(FrozenInstanceError):
            breach.value = 2.0  # type: ignore[misc]

    def test_breach_has_slots(self) -> None:
        """Breach is slotted, like every value object in the domain."""
        assert not hasattr(Breach((Signal.CUSUM_HIGH,), 1.0, 0.0, 1.0), "__dict__")


class TestChartState:
    """A chart's running state is not a constructor argument."""

    def test_state_cannot_be_injected(self) -> None:
        """Only the parameters are accepted; the baseline and sums start empty."""
        build: Callable[..., object] = ControlChart
        with pytest.raises(TypeError):
            build(ChartParameters(), [1.0, 2.0])


_UNIT_BASELINE = (-1.0, 0.0, 1.0)
"""Mean exactly 0 and sample sigma exactly 1, so an observation's z-score is its value."""


def _signals_after_unit_baseline(
    values: Sequence[float], **settings: float
) -> list[tuple[Signal, ...] | None]:
    chart = ControlChart(ChartParameters(baseline_size=3, **settings))
    for x in _UNIT_BASELINE:
        chart.observe(x)
    out: list[tuple[Signal, ...] | None] = []
    for x in values:
        breach = chart.observe(x)
        out.append(None if breach is None else breach.signals)
    return out


class TestExactLimits:
    """The EWMA's time-varying limit and the CUSUM thresholds, at their exact edges.

    With λ = 0.2 and L = 3 the first EWMA limit is 3 * sqrt(0.2/1.8 * (1 - 0.8^2)) = 0.6
    sigma, and the EWMA after one point is 0.2 z, so the first point alerts only above
    z = 3. By the third point the limit has widened to about 0.859 sigma, z = 4.3.
    """

    def test_first_point_above_its_narrow_limit_alerts(self) -> None:
        """A z-score of 3.2 clears the first-step EWMA limit of z = 3 but not the CUSUM."""
        assert _signals_after_unit_baseline([3.2]) == [(Signal.EWMA_HIGH,)]

    def test_first_point_below_its_narrow_limit_is_quiet(self) -> None:
        """A z-score of 2.8 stays inside the first-step EWMA limit of z = 3."""
        assert _signals_after_unit_baseline([2.8]) == [None]

    def test_third_point_uses_the_third_step_limit(self) -> None:
        """After two on-target points, z = 4.4 clears the third-step limit of about 4.3."""
        assert _signals_after_unit_baseline([0.0, 0.0, 4.4]) == [None, None, (Signal.EWMA_HIGH,)]

    def test_cusum_starts_at_zero_high(self) -> None:
        """A z-score of 5.6 lifts the upper CUSUM from 0 to 5.1, above h = 5."""
        assert _signals_after_unit_baseline([5.6]) == [(Signal.EWMA_HIGH, Signal.CUSUM_HIGH)]

    def test_cusum_starts_at_zero_low(self) -> None:
        """A z-score of -5.4 lowers the CUSUM to 4.9, still inside h = 5."""
        assert _signals_after_unit_baseline([-5.4]) == [(Signal.EWMA_LOW,)]

    def test_lower_cusum_starts_at_zero(self) -> None:
        """A z-score of -5.6 lowers the CUSUM from 0 to 5.1, past h = 5."""
        assert _signals_after_unit_baseline([-5.6]) == [(Signal.EWMA_LOW, Signal.CUSUM_LOW)]

    def test_cusum_exactly_at_h_is_inside(self) -> None:
        """A z-score of 5.5 puts the upper CUSUM at exactly h = 5: the limit is strict."""
        assert _signals_after_unit_baseline([5.5]) == [(Signal.EWMA_HIGH,)]

    def test_lower_cusum_exactly_at_h_is_inside(self) -> None:
        """A z-score of -5.5 puts the lower CUSUM at exactly h = 5: the limit is strict."""
        assert _signals_after_unit_baseline([-5.5]) == [(Signal.EWMA_LOW,)]

    def test_ewma_exactly_at_the_upper_limit_is_inside(self) -> None:
        """λ = 1, L = 2: the EWMA is the value and the limit is exactly 2 sigma."""
        assert _signals_after_unit_baseline([2.0, 2.0001], ewma_lambda=1.0, ewma_l=2.0) == [
            None,
            (Signal.EWMA_HIGH,),
        ]

    def test_ewma_exactly_at_the_lower_limit_is_inside(self) -> None:
        """λ = 1, L = 2: a value of exactly -2 sigma sits on the limit, not past it."""
        assert _signals_after_unit_baseline([-2.0], ewma_lambda=1.0, ewma_l=2.0) == [None]
