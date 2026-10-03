"""Statistical process control: EWMA and tabular CUSUM charts on one metric.

ADR-005 Category B: control charts watch what the engine already did. They
need no training data, unlike learned anomaly models, which ADR-005 defers
until about six months of production data exist.

A chart has two phases:

1. **Phase I (baseline)**: the first ``baseline_size`` observations fix the
   in-control mean and standard deviation. Nothing is alerted.
2. **Phase II (monitoring)**: each observation updates an EWMA statistic and
   a two-sided tabular CUSUM. The chart is "out of control" while either
   signals. :meth:`ControlChart.observe` reports a breach only on the
   transition into out-of-control, so one breach episode gives one alert.

Parameters are textbook defaults, not pre-registered numbers. They never
touch strategy, sizing or kill thresholds:

- EWMA λ = 0.2 with L = 3 limits, and CUSUM k = 0.5 sigma with h = 5 sigma. Both give
  an in-control average run length of roughly 500 and 465 observations
  (Montgomery, *Introduction to Statistical Quality Control*, ch. 9).
- A baseline of 30 observations (Phase I guidance: 20 to 25 subgroups or
  more).

The CUSUM sums are not reset on a signal. An episode lasts while either
statistic is out of its limits and ends when both are back inside, so a
persistent shift stays one episode and gives one alert.

Reference: issue #362; ADR-005 Category B; ADR-012 D-04.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = ["Breach", "ChartParameters", "ControlChart", "Signal"]

_MIN_BASELINE = 2
"""A sample standard deviation needs two points."""


class Signal(StrEnum):
    """Which statistic left its limits."""

    EWMA_HIGH = "ewma_high"
    EWMA_LOW = "ewma_low"
    CUSUM_HIGH = "cusum_high"
    CUSUM_LOW = "cusum_low"


@dataclass(frozen=True, slots=True)
class ChartParameters:
    """EWMA and CUSUM settings, in units of the baseline standard deviation."""

    baseline_size: int = 30
    ewma_lambda: float = 0.2
    ewma_l: float = 3.0
    cusum_k: float = 0.5
    cusum_h: float = 5.0

    def __post_init__(self) -> None:
        """Validate the settings.

        Raises:
            ValueError: A baseline under 2, λ outside (0, 1], or a
                non-positive width.
        """
        if self.baseline_size < _MIN_BASELINE:
            raise ValueError("baseline_size must be at least 2")
        if not 0.0 < self.ewma_lambda <= 1.0:
            raise ValueError("ewma_lambda must be in (0, 1]")
        if min(self.ewma_l, self.cusum_h) <= 0.0 or self.cusum_k < 0.0:
            raise ValueError("ewma_l and cusum_h must be positive, cusum_k non-negative")


@dataclass(frozen=True, slots=True)
class Breach:
    """The start of one out-of-control episode."""

    signals: tuple[Signal, ...]
    value: float
    baseline_mean: float
    baseline_sigma: float


@dataclass
class ControlChart:
    """EWMA plus two-sided CUSUM chart on one metric."""

    params: ChartParameters = field(default_factory=ChartParameters)
    _baseline: list[float] = field(default_factory=list, init=False)
    _mean: float = field(default=0.0, init=False)
    _sigma: float = field(default=0.0, init=False)
    _ewma: float = field(default=0.0, init=False)
    _steps: int = field(default=0, init=False)
    _cusum_high: float = field(default=0.0, init=False)
    _cusum_low: float = field(default=0.0, init=False)
    _out_of_control: bool = field(default=False, init=False)

    @property
    def calibrated(self) -> bool:
        """True once Phase I has fixed the baseline."""
        return len(self._baseline) >= self.params.baseline_size

    def observe(self, value: float) -> Breach | None:
        """Add one observation.

        Args:
            value: The metric's latest value.

        Returns:
            A :class:`Breach` when this observation starts an out-of-control
            episode, else ``None``. Baseline observations never alert.

        Raises:
            ValueError: ``value`` is not finite.
        """
        if not math.isfinite(value):
            raise ValueError(f"observation must be finite, got {value!r}")
        if not self.calibrated:
            self._baseline.append(value)
            if self.calibrated:
                self._fix_baseline()
            return None

        signals = self._update(value)
        entering = bool(signals) and not self._out_of_control
        self._out_of_control = bool(signals)
        if not entering:
            return None
        return Breach(tuple(signals), value, self._mean, self._sigma)

    def _fix_baseline(self) -> None:
        self._mean = statistics.fmean(self._baseline)
        sigma = statistics.stdev(self._baseline)
        # A perfectly flat baseline (a fixed fee rate) would make every later
        # change infinitely significant; floor sigma at a tiny fraction of the
        # mean's scale so a real shift still signals.
        self._sigma = sigma if sigma > 0.0 else max(abs(self._mean) * 1e-6, 1e-12)
        self._ewma = self._mean

    def _update(self, value: float) -> list[Signal]:
        p = self.params
        self._steps += 1
        self._ewma = p.ewma_lambda * value + (1.0 - p.ewma_lambda) * self._ewma
        width = (
            p.ewma_l
            * self._sigma
            * math.sqrt(
                p.ewma_lambda
                / (2.0 - p.ewma_lambda)
                * (1.0 - (1.0 - p.ewma_lambda) ** (2 * self._steps))
            )
        )
        signals: list[Signal] = []
        if self._ewma > self._mean + width:
            signals.append(Signal.EWMA_HIGH)
        elif self._ewma < self._mean - width:
            signals.append(Signal.EWMA_LOW)

        z = (value - self._mean) / self._sigma
        self._cusum_high = max(0.0, self._cusum_high + z - p.cusum_k)
        self._cusum_low = max(0.0, self._cusum_low - z - p.cusum_k)
        if self._cusum_high > p.cusum_h:
            signals.append(Signal.CUSUM_HIGH)
        if self._cusum_low > p.cusum_h:
            signals.append(Signal.CUSUM_LOW)
        return signals
