"""Unit tests for the ADR-021 §5 backtest funnel (change v2-backtest-funnel).

Every series is synthetic and built so the statistic is exact in binary
floating point: returns ``[3.5, 1.5, 1.5, 1.5]`` have mean 2, sample standard
deviation 1, Sharpe 2 and t-statistic 4. Limits are tested exactly at the
limit and just past it (test rule 4).
"""

from dataclasses import replace
from datetime import date, timedelta
from types import MappingProxyType
from typing import Any

import pytest

from hermes.application.calibration.backtest_funnel import (
    FUNNEL_CHECK_NAMES,
    CostStress,
    FunnelEvidence,
    FunnelEvidenceError,
    FunnelThresholdError,
    FunnelThresholds,
    Regime,
    evaluate_funnel,
    parse_funnel_front_matter,
    validate_evidence,
)
from hermes.application.calibration.certification_gates import GateCheck

_START = date(2020, 12, 31)
_EXACT = (1.0, 4.5, 11.25, 28.125, 70.3125)
"""Returns 3.5, 1.5, 1.5, 1.5: Sharpe 2, t = 4."""


def _thresholds(**overrides: Any) -> FunnelThresholds:
    base = FunnelThresholds(
        train_end=date(2021, 12, 31),
        oos_start=date(2025, 9, 1),
        cscv_blocks=2,
        pbo_max=0.05,
        t_stat_above=3.0,
        trade_count_min=100,
        cost_stress_multiplier=2.0,
        positive_year_share_above=0.5,
        validation_to_train_return_min=0.5,
        neighbour_sharpe_ratio_min=0.5,
        return_correlation_below=0.7,
        regimes=(
            Regime("bull", date(2021, 1, 1), date(2021, 1, 4)),
            Regime("bear", date(2021, 1, 1), date(2021, 1, 4)),
        ),
    )
    return replace(base, **overrides)


def _evidence(equity: tuple[float, ...] = _EXACT, **overrides: Any) -> FunnelEvidence:
    base = FunnelEvidence(
        strategy_id="swing-v2",
        start_date=_START,
        daily_equity=equity,
        trade_count=100,
        cost_stress=CostStress(2.0, equity),
        trials={"a": (0.03, 0.01, 0.02, 0.0), "b": (0.01, -0.01, 0.0, -0.02)},
        neighbour_trial_ids=("b",),
        running_strategies={},
    )
    return replace(base, **overrides)


def _check(checks: tuple[GateCheck, ...], name: str) -> GateCheck:
    (found,) = (c for c in checks if c.name == name)
    return found


def _curve(days: int, jumps: dict[int, float]) -> tuple[float, ...]:
    """Flat curve of ``days + 1`` points starting at 1.0, multiplied at the given indices."""
    value = 1.0
    points = [value]
    for i in range(1, days + 1):
        value *= jumps.get(i, 1.0)
        points.append(value)
    return tuple(points)


class TestReport:
    """The funnel reports every criterion, in a fixed order, with one-token names."""

    def test_check_names_and_order(self) -> None:
        """Ten checks, in the change spec's Design §3 order."""
        names = tuple(c.name for c in evaluate_funnel(_evidence(), _thresholds()))
        assert names == FUNNEL_CHECK_NAMES
        assert FUNNEL_CHECK_NAMES == (
            "pbo",
            "t_stat",
            "trade_count",
            "cost_stress",
            "positive_years",
            "bull_return",
            "bear_return",
            "validation_vs_train",
            "neighbours",
            "correlation",
        )

    # SEV-1: missing statistics must never pass the funnel (ADR-021 §5, fail closed)
    def test_missing_evidence_fails_every_dependent_check(self) -> None:
        """Without trades, stress run, trials, neighbours or running strategies, those fail."""
        bare = _evidence(
            trade_count=None,
            cost_stress=None,
            trials=None,
            neighbour_trial_ids=None,
            running_strategies=None,
        )
        checks = evaluate_funnel(bare, _thresholds())
        failed = {c.name: c.observed for c in checks if not c.passed}
        assert failed == {
            "pbo": "not computed",
            "trade_count": "not computed",
            "cost_stress": "not computed",
            "neighbours": "not computed",
            "correlation": "not computed",
            "validation_vs_train": "not computed",
        }


class TestOverflow:
    """Returns too large to square fail the Sharpe-based checks closed (change D-9)."""

    _HUGE = (1.0, 1e200, 1.0, 1.0, 1.0)

    def _checks(self) -> dict[str, GateCheck]:
        evidence = _evidence(
            equity=self._HUGE, trials={"n1": (1.0, 2.0, 1.0, 3.0)}, neighbour_trial_ids=("n1",)
        )
        return {c.name: c for c in evaluate_funnel(evidence, _thresholds())}

    def test_t_stat_is_not_computed(self) -> None:
        """Squaring a 1e200 return overflows; the t-statistic row fails, nothing raises."""
        check = self._checks()["t_stat"]
        assert (check.passed, check.observed) == (False, "not computed")

    def test_neighbours_are_not_computed(self) -> None:
        """The base Sharpe overflows the same way, so the neighbour row fails too."""
        check = self._checks()["neighbours"]
        assert (check.passed, check.observed) == (False, "not computed")


class TestPbo:
    """PBO <= pbo_max, on the CSCV of every logged trial."""

    _HALF = MappingProxyType(
        {
            "a": (0.05, 0.03, -0.03, -0.05),
            "b": (0.04, 0.02, 0.04, 0.02),
            "c": (0.03, 0.01, 0.03, 0.01),
            "d": (0.02, 0.0, 0.02, 0.0),
        }
    )
    """PBO = 0.5 (worked in test_overfitting.py)."""

    def test_at_the_limit_passes(self) -> None:
        """PBO 0.5 with pbo_max 0.5 passes: the ADR says at or below."""
        check = _check(
            evaluate_funnel(_evidence(trials=self._HALF), _thresholds(pbo_max=0.5)), "pbo"
        )
        assert (check.passed, check.observed) == (True, "0.5")

    def test_just_above_the_limit_fails(self) -> None:
        """PBO 0.5 with pbo_max 0.49 fails."""
        check = _check(
            evaluate_funnel(_evidence(trials=self._HALF), _thresholds(pbo_max=0.49)), "pbo"
        )
        assert check.passed is False

    def test_requirement_names_the_blocks(self) -> None:
        """The printed rule names the comparison, the limit and S."""
        check = _check(evaluate_funnel(_evidence(), _thresholds()), "pbo")
        assert check.requirement == "<= 0.05 (CSCV, 2 blocks)"

    def test_unusable_trial_matrix_is_not_computed(self) -> None:
        """One trial cannot be cross-validated, so the row fails."""
        check = _check(
            evaluate_funnel(_evidence(trials={"a": (0.1, 0.2, 0.3, 0.4)}), _thresholds()), "pbo"
        )
        assert (check.passed, check.observed) == (False, "not computed")


class TestTStat:
    """t > t_stat_above on the base run's daily returns."""

    def test_at_the_limit_fails(self) -> None:
        """A t-statistic of 4 with t_stat_above 4 fails: the ADR says above."""
        check = _check(evaluate_funnel(_evidence(), _thresholds(t_stat_above=4.0)), "t_stat")
        assert (check.passed, check.observed) == (False, "4")

    def test_just_below_the_limit_passes(self) -> None:
        """A t-statistic of 4 with t_stat_above 3.99 passes."""
        check = _check(evaluate_funnel(_evidence(), _thresholds(t_stat_above=3.99)), "t_stat")
        assert check.passed is True

    def test_flat_curve_is_not_computed(self) -> None:
        """A flat curve has no t-statistic, so the row fails."""
        check = _check(evaluate_funnel(_evidence(equity=(1.0, 1.0, 1.0)), _thresholds()), "t_stat")
        assert (check.passed, check.observed) == (False, "not computed")


class TestTradeCount:
    """Trades across Train + Validation >= trade_count_min."""

    def test_at_the_limit_passes(self) -> None:
        """100 trades with a minimum of 100 passes: the ADR says at least."""
        check = _check(evaluate_funnel(_evidence(trade_count=100), _thresholds()), "trade_count")
        assert (check.passed, check.observed) == (True, "100")

    def test_one_short_fails(self) -> None:
        """99 trades with a minimum of 100 fails."""
        check = _check(evaluate_funnel(_evidence(trade_count=99), _thresholds()), "trade_count")
        assert check.passed is False


class TestCostStress:
    """The stressed run is profitable, at no less than the pre-registered multiplier."""

    def test_profitable_at_the_multiplier_passes(self) -> None:
        """Equity 1 to 1.5 at exactly 2x costs passes."""
        stress = CostStress(2.0, (1.0, 1.25, 1.5))
        check = _check(evaluate_funnel(_evidence(cost_stress=stress), _thresholds()), "cost_stress")
        assert (check.passed, check.observed) == (True, "0.5")

    def test_break_even_fails(self) -> None:
        """Ending where it started is not profitable."""
        stress = CostStress(2.0, (1.0, 1.25, 1.0))
        check = _check(evaluate_funnel(_evidence(cost_stress=stress), _thresholds()), "cost_stress")
        assert check.passed is False

    def test_lower_multiplier_fails_even_when_profitable(self) -> None:
        """A run at 1.5x does not answer a 2x question."""
        stress = CostStress(1.5, (1.0, 1.25, 1.5))
        check = _check(evaluate_funnel(_evidence(cost_stress=stress), _thresholds()), "cost_stress")
        assert (check.passed, check.observed) == (False, "run at 1.5x")

    def test_requirement_names_the_multiplier(self) -> None:
        """The printed rule names the pre-registered multiplier."""
        check = _check(evaluate_funnel(_evidence(), _thresholds()), "cost_stress")
        assert check.requirement == "return > 0 at >= 2x costs"


class TestPositiveYears:
    """The share of calendar years with a positive return is above the limit."""

    _TWO_YEARS = _curve(730, {10: 1.5, 400: 0.5})
    """From 31 Dec 2020: 2021 is +50%, 2022 is -50%: share 0.5."""

    def test_at_the_limit_fails(self) -> None:
        """Share 0.5 with positive_year_share_above 0.5 fails: most means more than half."""
        evidence = _evidence(equity=self._TWO_YEARS, cost_stress=None)
        check = _check(evaluate_funnel(evidence, _thresholds()), "positive_years")
        assert (check.passed, check.observed) == (False, "0.5 (1 of 2)")

    def test_just_below_the_limit_passes(self) -> None:
        """Share 0.5 with positive_year_share_above 0.49 passes."""
        evidence = _evidence(equity=self._TWO_YEARS, cost_stress=None)
        check = _check(
            evaluate_funnel(evidence, _thresholds(positive_year_share_above=0.49)), "positive_years"
        )
        assert check.passed is True

    def test_partial_year_counts(self) -> None:
        """A curve ending on 1 Jan 2022 has a one-day 2022, which counts as a year (D-6)."""
        evidence = _evidence(equity=_curve(366, {5: 1.1, 366: 0.9}), cost_stress=None)
        check = _check(evaluate_funnel(evidence, _thresholds()), "positive_years")
        assert check.observed == "0.5 (1 of 2)"


class TestRegimes:
    """Compounded returns over all bull, and over all bear, stretches are positive."""

    def test_bull_and_bear_are_compounded_separately(self) -> None:
        """Bull days 1-2 gain 50%, bear days 3-4 lose 20%: bull passes, bear fails."""
        equity = (1.0, 1.25, 1.5, 1.2, 1.2)
        regimes = (
            Regime("bull", date(2021, 1, 1), date(2021, 1, 2)),
            Regime("bear", date(2021, 1, 3), date(2021, 1, 4)),
        )
        checks = evaluate_funnel(_evidence(equity=equity), _thresholds(regimes=regimes))
        assert (_check(checks, "bull_return").observed, _check(checks, "bull_return").passed) == (
            "0.5",
            True,
        )
        assert (_check(checks, "bear_return").observed, _check(checks, "bear_return").passed) == (
            "-0.2",
            False,
        )

    def test_two_stretches_of_one_kind_compound(self) -> None:
        """Bull +50% then -20% compounds to +20%, not the +30% sum."""
        equity = (1.0, 1.5, 1.5, 1.2, 1.2)
        regimes = (
            Regime("bull", date(2021, 1, 1), date(2021, 1, 1)),
            Regime("bull", date(2021, 1, 3), date(2021, 1, 3)),
            Regime("bear", date(2021, 1, 2), date(2021, 1, 2)),
        )
        check = _check(
            evaluate_funnel(_evidence(equity=equity), _thresholds(regimes=regimes)), "bull_return"
        )
        assert check.observed == "0.2"

    def test_flat_stretch_fails(self) -> None:
        """A zero return is not positive."""
        regimes = (
            Regime("bull", date(2021, 1, 1), date(2021, 1, 1)),
            Regime("bear", date(2021, 1, 2), date(2021, 1, 2)),
        )
        equity = (1.0, 1.0, 1.5)
        check = _check(
            evaluate_funnel(_evidence(equity=equity), _thresholds(regimes=regimes)), "bull_return"
        )
        assert (check.passed, check.observed) == (False, "0")

    def test_stretch_outside_the_window_is_not_computed(self) -> None:
        """A stretch past the last day cannot be measured, so the row fails."""
        regimes = (
            Regime("bull", date(2021, 1, 1), date(2021, 2, 1)),
            Regime("bear", date(2021, 1, 1), date(2021, 1, 2)),
        )
        check = _check(evaluate_funnel(_evidence(), _thresholds(regimes=regimes)), "bull_return")
        assert (check.passed, check.observed) == (False, "not computed")


class TestValidationVsTrain:
    """Annualised Validation return >= ratio x annualised Train return, Train positive."""

    _CURVE = _curve(730, {150: 1.25, 500: 1.125})
    """From 31 Dec 2020, 365 Train days at +25% and 365 Validation days at +12.5%: ratio 0.5."""

    def test_at_the_limit_passes(self) -> None:
        """Ratio 0.5 with a minimum of 0.5 passes: the ADR says at least half."""
        evidence = _evidence(equity=self._CURVE, cost_stress=None)
        check = _check(evaluate_funnel(evidence, _thresholds()), "validation_vs_train")
        assert (check.passed, check.observed) == (True, "0.5")

    def test_just_past_the_limit_fails(self) -> None:
        """Ratio 0.5 with a minimum of 0.51 fails."""
        evidence = _evidence(equity=self._CURVE, cost_stress=None)
        check = _check(
            evaluate_funnel(evidence, _thresholds(validation_to_train_return_min=0.51)),
            "validation_vs_train",
        )
        assert check.passed is False

    def test_losing_train_fails_whatever_validation_does(self) -> None:
        """Train -20%, Validation flat: 0 >= 0.5 x -0.2 holds, but a losing Train fails."""
        evidence = _evidence(equity=_curve(730, {150: 0.8}), cost_stress=None)
        check = _check(evaluate_funnel(evidence, _thresholds()), "validation_vs_train")
        assert (check.passed, check.observed) == (False, "Train -0.2")

    # SEV-1: an overflowing statistic must fail closed, never crash or pass (ADR-021 §5)
    def test_overflowing_annualised_return_is_not_computed(self) -> None:
        """A one-day Validation stretch at +1.3e10 annualises past the float range."""
        evidence = _evidence(equity=(1.0, 1.1, 1.2, 1.3, 1.3e10))
        check = _check(
            evaluate_funnel(evidence, _thresholds(train_end=date(2021, 1, 3))),
            "validation_vs_train",
        )
        assert (check.passed, check.observed) == (False, "not computed")

    def test_split_day_outside_the_window_is_not_computed(self) -> None:
        """A curve that ends on train_end has no Validation days."""
        check = _check(
            evaluate_funnel(_evidence(), _thresholds(train_end=date(2021, 1, 4))),
            "validation_vs_train",
        )
        assert (check.passed, check.observed) == (False, "not computed")


class TestNeighbours:
    """Every neighbour's Sharpe is at least the ratio times the base run's."""

    _HALF_SHARPE = (2.5, 0.5, 0.5, 0.5)
    """Mean 1, standard deviation 1: Sharpe 1, half the base run's 2."""

    def _run(self, neighbours: dict[str, tuple[float, ...]], ratio: float) -> GateCheck:
        evidence = _evidence(trials=neighbours, neighbour_trial_ids=tuple(neighbours))
        return _check(
            evaluate_funnel(evidence, _thresholds(neighbour_sharpe_ratio_min=ratio)), "neighbours"
        )

    def test_at_the_limit_passes(self) -> None:
        """Ratio 0.5 with a minimum of 0.5 passes."""
        check = self._run({"n1": self._HALF_SHARPE}, 0.5)
        assert (check.passed, check.observed) == (True, "0.5")

    def test_just_past_the_limit_fails(self) -> None:
        """Ratio 0.5 with a minimum of 0.51 fails."""
        assert self._run({"n1": self._HALF_SHARPE}, 0.51).passed is False

    def test_the_worst_neighbour_decides(self) -> None:
        """One neighbour at Sharpe 2 and one at Sharpe 1: the worst ratio, 0.5, is reported."""
        check = self._run({"n1": (3.5, 1.5, 1.5, 1.5), "n2": self._HALF_SHARPE}, 0.4)
        assert check.observed == "0.5"

    def test_flat_neighbour_fails(self) -> None:
        """A neighbour with no Sharpe cannot pass."""
        check = self._run({"n1": (0.0, 0.0, 0.0, 0.0)}, 0.0)
        assert (check.passed, check.observed) == (False, "neighbour n1 not computed")

    def test_no_neighbours_is_not_computed(self) -> None:
        """An empty neighbour list proves nothing about robustness."""
        evidence = _evidence(neighbour_trial_ids=())
        check = _check(evaluate_funnel(evidence, _thresholds()), "neighbours")
        assert (check.passed, check.observed) == (False, "not computed")

    def test_losing_base_run_fails(self) -> None:
        """With a negative base Sharpe the ratio has no meaning, so the row fails."""
        equity = (1.0, 0.9, 0.85, 0.8)
        evidence = _evidence(
            equity=equity, trials={"n1": self._HALF_SHARPE}, neighbour_trial_ids=("n1",)
        )
        check = _check(evaluate_funnel(evidence, _thresholds()), "neighbours")
        assert (check.passed, check.observed) == (False, "base Sharpe <= 0")


class TestCorrelation:
    """The largest signed return correlation with a running strategy is below the limit."""

    _BASE = (3.5, 1.5, 1.5, 1.5)

    def test_at_the_limit_fails(self) -> None:
        """Correlation exactly 1 with a limit of 1 fails: the ADR says below."""
        running = {"v1": tuple(2.0 * r for r in self._BASE)}
        check = _check(
            evaluate_funnel(
                _evidence(running_strategies=running), _thresholds(return_correlation_below=1.0)
            ),
            "correlation",
        )
        assert (check.passed, check.observed) == (False, "1")

    def test_correlation_is_signed(self) -> None:
        """A mirror strategy (correlation -1) diversifies and passes a 0.7 limit."""
        running = {"v1": tuple(-r for r in self._BASE)}
        check = _check(
            evaluate_funnel(_evidence(running_strategies=running), _thresholds()), "correlation"
        )
        assert (check.passed, check.observed) == (True, "-1")

    def test_just_below_the_limit_passes(self) -> None:
        """Correlation -1 with a limit of -0.99 passes."""
        running = {"v1": tuple(-r for r in self._BASE)}
        check = _check(
            evaluate_funnel(
                _evidence(running_strategies=running), _thresholds(return_correlation_below=-0.99)
            ),
            "correlation",
        )
        assert check.passed is True

    def test_the_most_correlated_strategy_decides(self) -> None:
        """With one mirror and one copy, the copy's 1 is reported."""
        running = {"m": tuple(-r for r in self._BASE), "c": self._BASE}
        check = _check(
            evaluate_funnel(_evidence(running_strategies=running), _thresholds()), "correlation"
        )
        assert (check.passed, check.observed) == (False, "1")

    def test_no_running_strategy_passes(self) -> None:
        """An explicit empty list means nothing runs, so there is nothing to correlate with."""
        check = _check(
            evaluate_funnel(_evidence(running_strategies={}), _thresholds()), "correlation"
        )
        assert (check.passed, check.observed) == (True, "none running")

    def test_flat_running_strategy_is_not_computed(self) -> None:
        """A flat series has no correlation, so the row fails."""
        running = {"v1": (0.0, 0.0, 0.0, 0.0)}
        check = _check(
            evaluate_funnel(_evidence(running_strategies=running), _thresholds()), "correlation"
        )
        assert (check.passed, check.observed) == (False, "not computed")


def _front_matter(**funnel: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "train_end": date(2024, 6, 30),
        "oos_start": date(2025, 9, 1),
        "cscv_blocks": 16,
        "pbo_max": 0.05,
        "t_stat_above": 3,
        "trade_count_min": 100,
        "cost_stress_multiplier": 2.0,
        "positive_year_share_above": 0.5,
        "validation_to_train_return_min": 0.5,
        "neighbour_sharpe_ratio_min": 0.5,
        "return_correlation_below": 0.7,
        "regimes": [
            {"kind": "bull", "start": date(2020, 10, 1), "end": date(2021, 11, 10)},
            {"kind": "bear", "start": "2021-11-11", "end": "2022-12-31"},
        ],
    }
    block.update(funnel)
    return {
        "experiment_id": "swing-v2",
        "strategy_id": "swing-v2",
        "status": "SIGNED",
        "backtest_funnel": block,
    }


_REQUIRED = (
    "train_end",
    "oos_start",
    "cscv_blocks",
    "pbo_max",
    "t_stat_above",
    "trade_count_min",
    "cost_stress_multiplier",
    "positive_year_share_above",
    "validation_to_train_return_min",
    "neighbour_sharpe_ratio_min",
    "return_correlation_below",
    "regimes",
)


class TestParseFrontMatter:
    """Every threshold comes from the signed pre-registration; nothing is defaulted."""

    def test_valid_block_parses_every_field(self) -> None:
        """Dates arrive as dates or ISO strings; integers are accepted for floats."""
        thresholds = parse_funnel_front_matter(_front_matter(), strategy_id="swing-v2")
        assert thresholds == FunnelThresholds(
            train_end=date(2024, 6, 30),
            oos_start=date(2025, 9, 1),
            cscv_blocks=16,
            pbo_max=0.05,
            t_stat_above=3.0,
            trade_count_min=100,
            cost_stress_multiplier=2.0,
            positive_year_share_above=0.5,
            validation_to_train_return_min=0.5,
            neighbour_sharpe_ratio_min=0.5,
            return_correlation_below=0.7,
            regimes=(
                Regime("bull", date(2020, 10, 1), date(2021, 11, 10)),
                Regime("bear", date(2021, 11, 11), date(2022, 12, 31)),
            ),
        )

    # SEV-1: a missing threshold must refuse, never default (issue #957)
    @pytest.mark.parametrize("key", _REQUIRED)
    def test_missing_threshold_refuses(self, key: str) -> None:
        """Each required key, when absent, refuses with its name."""
        front = _front_matter()
        del front["backtest_funnel"][key]
        with pytest.raises(FunnelThresholdError, match=f"^backtest_funnel.{key} is missing$"):
            parse_funnel_front_matter(front, strategy_id="swing-v2")

    def test_missing_block_refuses(self) -> None:
        """A pre-registration without the block (v1) cannot run the funnel."""
        front = _front_matter()
        del front["backtest_funnel"]
        with pytest.raises(FunnelThresholdError, match="has no backtest_funnel block"):
            parse_funnel_front_matter(front, strategy_id="swing-v2")

    def test_unsigned_refuses(self) -> None:
        """Only a signed pre-registration binds thresholds."""
        front = {**_front_matter(), "status": "DRAFT"}
        with pytest.raises(FunnelThresholdError, match="status is 'DRAFT', not SIGNED"):
            parse_funnel_front_matter(front, strategy_id="swing-v2")

    def test_unknown_key_refuses(self) -> None:
        """A misspelt threshold must not be silently ignored."""
        with pytest.raises(FunnelThresholdError, match=r"unknown keys: \['pbo_maximum'\]"):
            parse_funnel_front_matter(_front_matter(pbo_maximum=0.05), strategy_id="swing-v2")

    @pytest.mark.parametrize(
        ("key", "value", "reason"),
        [
            ("cscv_blocks", 15, "an even integer from 2 to 20"),
            ("cscv_blocks", 22, "an even integer from 2 to 20"),
            ("cscv_blocks", True, "an even integer from 2 to 20"),
            ("pbo_max", 1.5, "from 0 to 1"),
            ("pbo_max", "0.05", "must be a number"),
            ("trade_count_min", 0, "a positive integer"),
            ("trade_count_min", 100.0, "a positive integer"),
            ("cost_stress_multiplier", 0.5, "at least 1"),
            ("positive_year_share_above", 1.0, "from 0 to below 1"),
            ("return_correlation_below", -1.0, "above -1 and at most 1"),
            ("t_stat_above", float("nan"), "must be a number"),
            ("t_stat_above", 0, "above 0"),
            ("validation_to_train_return_min", 0, "above 0 and at most 1"),
            ("validation_to_train_return_min", 1.01, "above 0 and at most 1"),
            ("neighbour_sharpe_ratio_min", 0, "above 0 and at most 1"),
            ("neighbour_sharpe_ratio_min", 1.01, "above 0 and at most 1"),
            ("train_end", "30/06/2024", "must be a date"),
        ],
    )
    def test_out_of_range_value_refuses(self, key: str, value: object, reason: str) -> None:
        """Values a threshold cannot take refuse with the rule they break."""
        with pytest.raises(FunnelThresholdError, match=f"backtest_funnel.{key} .*{reason}"):
            parse_funnel_front_matter(_front_matter(**{key: value}), strategy_id="swing-v2")

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("t_stat_above", 0.01),
            ("validation_to_train_return_min", 1.0),
            ("validation_to_train_return_min", 0.01),
            ("neighbour_sharpe_ratio_min", 1.0),
            ("neighbour_sharpe_ratio_min", 0.01),
        ],
    )
    def test_value_inside_the_bound_is_accepted(self, key: str, value: float) -> None:
        """The edge of each range parses: t above 0, the two ratios in (0, 1]."""
        thresholds = parse_funnel_front_matter(
            _front_matter(**{key: value}), strategy_id="swing-v2"
        )
        assert getattr(thresholds, key) == value

    def test_missing_strategy_id_refuses(self) -> None:
        """A pre-registration that names no strategy cannot bind one."""
        front = _front_matter()
        del front["strategy_id"]
        with pytest.raises(
            FunnelThresholdError, match=r"^pre-registration swing-v2 names no strategy_id$"
        ):
            parse_funnel_front_matter(front, strategy_id="swing-v2")

    def test_other_strategy_refuses(self) -> None:
        """Thresholds signed for one strategy never judge another."""
        with pytest.raises(
            FunnelThresholdError,
            match=r"^pre-registration swing-v2 is for strategy 'swing-v2', not 'swing-v3'$",
        ):
            parse_funnel_front_matter(_front_matter(), strategy_id="swing-v3")

    def test_train_end_must_precede_oos(self) -> None:
        """Train cannot end inside the sealed window."""
        with pytest.raises(FunnelThresholdError, match="train_end must be before oos_start"):
            parse_funnel_front_matter(
                _front_matter(train_end=date(2025, 9, 1)), strategy_id="swing-v2"
            )

    @pytest.mark.parametrize(
        ("regimes", "reason"),
        [
            (
                [{"kind": "bull", "start": date(2021, 1, 1), "end": date(2021, 2, 1)}],
                "needs at least one bear",
            ),
            (
                [{"kind": "bear", "start": date(2021, 1, 1), "end": date(2021, 2, 1)}],
                "needs at least one bull",
            ),
            (
                [{"kind": "flat", "start": date(2021, 1, 1), "end": date(2021, 2, 1)}],
                "kind must be bull or bear",
            ),
            (
                [{"kind": "bull", "start": date(2021, 2, 1), "end": date(2021, 1, 1)}],
                "start after its end",
            ),
            (
                [{"kind": "bull", "start": date(2025, 8, 1), "end": date(2025, 9, 1)}],
                "reaches oos_start",
            ),
            ([{"kind": "bull", "start": date(2021, 1, 1)}], "needs exactly kind, start and end"),
            ("bull", "must be a list"),
        ],
    )
    def test_bad_regimes_refuse(self, regimes: object, reason: str) -> None:
        """Every stretch is a dated bull or bear inside Train + Validation, one of each at least."""
        with pytest.raises(FunnelThresholdError, match=reason):
            parse_funnel_front_matter(_front_matter(regimes=regimes), strategy_id="swing-v2")


class TestValidateEvidence:
    """Evidence that is malformed or reaches the OOS window is refused before any check."""

    # SEV-1: no evidence from the sealed OOS window may be evaluated (ADR-023 §4)
    def test_evidence_reaching_oos_is_refused(self) -> None:
        """A last day equal to oos_start is refused."""
        start = date(2025, 8, 28)
        with pytest.raises(FunnelEvidenceError, match="reaches the OOS start 2025-09-01"):
            validate_evidence(_evidence(start_date=start), _thresholds())

    def test_evidence_ending_the_day_before_oos_is_accepted(self) -> None:
        """A last day of 31 August 2025 is the edge of Validation, not OOS."""
        start = date(2025, 8, 27)
        evidence = _evidence(start_date=start)
        assert start + timedelta(days=4) == date(2025, 8, 31)
        assert validate_evidence(evidence, _thresholds()) is evidence

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            (
                {"cost_stress": CostStress(2.0, (1.0, 2.0))},
                "cost_stress.daily_equity has 2 points, expected 5",
            ),
            ({"trials": {"a": (0.1, 0.2)}}, "trials.a has 2 returns, expected 4"),
            (
                {"running_strategies": {"v1": (0.1,)}},
                "running_strategies.v1 has 1 returns, expected 4",
            ),
            ({"neighbour_trial_ids": ("zz",)}, "neighbour zz is not in trials"),
            ({"daily_equity": (1.0, 0.0, 2.0, 3.0, 4.0)}, "daily_equity values must be positive"),
            ({"daily_equity": (1.0,)}, "daily_equity needs at least 2 points"),
        ],
    )
    def test_malformed_evidence_is_refused(self, overrides: dict[str, Any], reason: str) -> None:
        """Series that do not cover the same days, or unknown neighbours, are refused."""
        evidence = _evidence(**overrides)
        if "trials" in overrides:
            evidence = replace(evidence, neighbour_trial_ids=())
        with pytest.raises(FunnelEvidenceError, match=reason):
            validate_evidence(evidence, _thresholds())
