"""Unit tests for scripts/backtest/null_check.py, the ADR-031 §4 null check (issue #1311).

Invariants under test:

- The random walk is seeded: one seed always gives the same bars, ordered for
  delivery, with daily bars aggregated from the 4h bars and no drift built in.
- One series runs the workflow's steps: Train judged over every candidate, one
  family winner each to Validation, the champion rule over the winners only.
- A sweep with no edge names no champion; a planted edge is found.
- The verdict is PASS exactly when the false-champion rate is at most q.

The judge is the real one. Most tests give it made-up evidence through a fake
runner, so they need no backtest; one test runs the real engine on a small sweep
and one year of synthetic prices to show its evidence reaches the judge.
"""

import importlib.util
import json
import math
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.calibration.research_batch import Candidate
from hermes.application.calibration.research_sweep import SweepBatch, SweepPoint, parse_sweep
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.support.candidate_evidence import BODY, FUNNEL, START, equity, returns
from tests.support.paths import REPO_ROOT
from tests.support.research_batch import BATCH_PATH, batch_text
from tests.support.research_sweep import family, sweep_front_matter

_SCRIPTS = REPO_ROOT / "scripts" / "backtest"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_TRAIN_DAYS = 365
_VALIDATION_DAYS = 730
_EDGE = 0.002


def _import_script() -> ModuleType:
    """Import the script; it imports its sibling driver modules by name, as when run directly."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("null_check_script", _SCRIPTS / "null_check.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["null_check_script"] = module
    spec.loader.exec_module(module)
    return module


NC = _import_script()


def _sweep() -> SweepBatch:
    """Return the fixture sweep, two points a family, its funnel fitted to Train 2021."""
    fm = sweep_front_matter()
    fm["backtest_funnel"] = dict(FUNNEL)
    fm["families"][0]["grid"] = {"lookback": [15, 20]}
    return parse_sweep(BATCH_PATH, batch_text(fm, BODY))


class _FakeRunner:
    """Made-up evidence: flat noise for every candidate, a steady edge for the planted ones.

    It stands in for :class:`EngineRunner`, whose seam to the judge is covered by
    ``test_the_engine_runner_feeds_the_judge_from_real_replays``.
    """

    def __init__(self, planted: Sequence[str] = ()) -> None:
        self.planted = set(planted)
        self.calls: list[tuple[str, int]] = []

    def __call__(
        self,
        point: SweepPoint,
        candidate: Candidate,
        bars: Sequence[Bar],
        start: datetime,
        end: datetime,
    ) -> Any:
        days = (
            _TRAIN_DAYS
            if end.date() <= date.fromisoformat(FUNNEL["train_end"])
            else _VALIDATION_DAYS
        )
        self.calls.append((candidate.strategy_id, days))
        drift = _EDGE if candidate.strategy_id in self.planted else 0.0
        replays = candidate.funnel_replays()
        series = {
            r.trial_id: returns(drift * (0.5 if r.cost_multiplier > 1 else 1.0), days)
            for r in replays
        }
        curve = equity(series["base"])
        stress = next(r.trial_id for r in replays if r.cost_multiplier > 1)
        trades = [{"gross_pnl": 50.0, "fees": 2.0}] * 90 + [{"gross_pnl": -20.0, "fees": 2.0}] * 50
        return NC.CandidateRun(
            performance={
                "window": "train_val",
                "daily_equity": curve,
                "trades": trades,
                "trials": {"count": 1, "sharpe_variance": 0.0},
                "bootstrap_seed": 11,
            },
            evidence={"backtest": {"calibration_fill_count": 40}},
            funnel={
                "strategy_id": candidate.strategy_id,
                "start_date": START.isoformat(),
                "daily_equity": curve,
                "trade_count": 140,
                "cost_stress": {"multiplier": 2.0, "daily_equity": equity(series[stress])},
                "trials": series,
                "neighbour_trial_ids": list(candidate.neighbour_trial_ids()),
                "running_strategies": {},
            },
            sharpes=tuple((r.trial_id, 0.01 * i) for i, r in enumerate(replays)),
        )


def _series(runner: _FakeRunner, seed: int = 0) -> Any:
    return NC.run_series(
        _sweep(),
        seed,
        runner,
        lambda _: [],
        train_start=START,
        validation_end=START + timedelta(days=_VALIDATION_DAYS),
    )


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------


def test_one_seed_always_gives_the_same_bars_and_another_seed_others() -> None:
    """The check is reproducible: a seed names its prices exactly."""
    first = NC.random_walk(7, (_BTC, _ETH), date(2021, 1, 1), date(2021, 1, 10))

    again = NC.random_walk(7, (_BTC, _ETH), date(2021, 1, 1), date(2021, 1, 10))
    other = NC.random_walk(8, (_BTC, _ETH), date(2021, 1, 1), date(2021, 1, 10))

    assert (first == again, first == other) == (True, False)


def test_bars_come_in_delivery_order_with_six_4h_bars_and_one_daily_bar_per_day() -> None:
    """The replay reads bars as they close, the daily bar first on a tie, as the driver does."""
    bars = NC.random_walk(1, (_BTC, _ETH), date(2021, 1, 1), date(2021, 1, 3))

    keys = [(b.close_time, b.interval != KlineInterval.D1, str(b.pair)) for b in bars]
    counts = {i: sum(b.interval is i for b in bars) for i in (KlineInterval.H4, KlineInterval.D1)}
    assert (keys == sorted(keys), counts) == (True, {KlineInterval.H4: 36, KlineInterval.D1: 6})


def test_a_daily_bar_spans_its_six_4h_bars() -> None:
    """Daily bars are the 4h bars aggregated, so the two intervals never disagree."""
    bars = NC.random_walk(3, (_BTC,), date(2021, 1, 1), date(2021, 1, 1))

    h4 = [b for b in bars if b.interval is KlineInterval.H4]
    (d1,) = [b for b in bars if b.interval is KlineInterval.D1]
    assert (d1.open, d1.close, d1.high, d1.low) == (
        h4[0].open,
        h4[-1].close,
        max(b.high for b in h4),
        min(b.low for b in h4),
    )


@pytest.mark.slow
def test_the_walk_has_no_drift_over_many_seeds() -> None:
    """No edge is built in: the mean 4h log return is zero within sampling error."""
    moves = [
        math.log(float(b.close) / float(b.open))
        for seed in range(20)
        for b in NC.random_walk(seed, (_BTC,), date(2021, 1, 1), date(2021, 3, 1))
        if b.interval is KlineInterval.H4
    ]
    mean = sum(moves) / len(moves)
    standard_error = NC.VOLATILITY_4H / math.sqrt(len(moves))
    assert abs(mean) < 4 * standard_error


# ---------------------------------------------------------------------------
# One series
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_a_sweep_with_no_edge_names_no_champion() -> None:
    """On noise every candidate fails Train, so nothing reaches Validation."""
    result = _series(_FakeRunner())

    assert (result.train_passed, result.validated, result.champion) == (0, 0, False)


@pytest.mark.slow
def test_a_planted_edge_is_found_as_the_champion() -> None:
    """Issue #1311: the check must find a champion that is really there."""
    result = _series(_FakeRunner(planted=["pullback@lookback=10"]))

    assert (result.train_passed, result.validated, result.champion) == (1, 1, True)


@pytest.mark.slow
def test_only_one_candidate_per_family_runs_validation() -> None:
    """ADR-031 §3: two passers in one family send one of them, the stronger, to Validation."""
    runner = _FakeRunner(planted=["breakout@lookback=15", "breakout@lookback=20"])

    result = _series(runner)

    validated = [sid for sid, days in runner.calls if days == _VALIDATION_DAYS]
    assert (result.train_passed, len(validated), result.champion) == (2, 1, True)


@pytest.mark.slow
def test_every_candidate_runs_train_once_in_expansion_order() -> None:
    """Every trial counts, so every grid point is run on Train before anyone is judged."""
    runner = _FakeRunner()

    _series(runner)

    sweep = _sweep()
    assert runner.calls == [(p.candidate_id, _TRAIN_DAYS) for p in sweep.points]


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@given(st.integers(min_value=20, max_value=400), st.data())
def test_the_check_passes_exactly_when_the_rate_is_at_most_q(
    series: int, data: st.DataObject
) -> None:
    """ADR-031 §4: the family-winners m stands while the false-champion rate is at most q."""
    champions = data.draw(st.integers(min_value=0, max_value=series))

    check = NC.NullCheck(series, champions, 0.05)

    assert check.passed == (champions * 20 <= series)


@pytest.mark.parametrize(("q", "fewest"), [(0.05, 20), (0.04, 25), (0.01, 100)])
def test_the_fewest_series_is_the_count_whose_one_champion_is_a_rate_of_q(
    q: float, fewest: int
) -> None:
    """Fewer series than 1/q cannot show a rate at or under q with even one champion."""
    assert NC.minimum_series(q) == fewest


def test_the_record_carries_counts_and_the_verdict_only() -> None:
    """The draft records PASS or FAIL and the m rule that follows from it (ADR-031 §4)."""
    sweep = _sweep()

    body = NC.record(NC.NullCheck(40, 3, 0.05), sweep, "text", 0)

    assert {k: body[k] for k in ("series", "seeds", "champions", "verdict", "m_rule")} == {
        "series": 40,
        "seeds": [0, 39],
        "champions": 3,
        "verdict": "FAIL",
        "m_rule": "every_candidate",
    }


# ---------------------------------------------------------------------------
# The real engine
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_the_engine_runner_feeds_the_judge_from_real_replays() -> None:
    """One year of random walk through the driver's replay reaches a verdict, with no data read."""
    fm = sweep_front_matter()
    fm["backtest_funnel"] = dict(FUNNEL)
    fm["families"] = [
        family("breakout", "swing-alpha", "C1", {"sma_period": [50, 100]}),
        family("pullback", "swing-beta", "C2", {"sma_period": [80, 120]}),
    ]
    for f in fm["families"]:
        f["template"] = "swing-b1-daily-ma"
    sweep = parse_sweep(BATCH_PATH, batch_text(fm, BODY))
    pairs = (_BTC, _ETH)
    end = START + timedelta(days=_VALIDATION_DAYS)

    result = NC.run_series(
        sweep,
        1,
        NC.EngineRunner(REPO_ROOT / "config", pairs),
        lambda s: NC.random_walk(s, pairs, START, end),
        train_start=START,
        validation_end=end,
    )

    assert (result.seed, result.champion) == (1, False)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _sweep_file(repo: Path) -> Path:
    target = repo / BATCH_PATH
    target.parent.mkdir(parents=True)
    target.write_text(batch_text(sweep_front_matter()), encoding="utf-8")
    return target


def test_fewer_series_than_one_over_q_is_a_usage_error(tmp_path: Path) -> None:
    """A rate over 19 series cannot show q = 0.05, so the run is refused before it starts."""
    sweep = _sweep_file(tmp_path)
    out = tmp_path / "record.json"

    code = NC.main(
        ["--sweep", str(sweep), "--repo", str(tmp_path), "--series", "19", "--out", str(out)]
    )

    assert (code, out.exists()) == (2, False)


def test_a_missing_sweep_exits_3(tmp_path: Path) -> None:
    """Exit 3 is a file that cannot be read."""
    assert NC.main(["--sweep", str(tmp_path / "nope.md"), "--repo", str(tmp_path)]) == 3


def test_a_sweep_outside_the_repository_exits_3(tmp_path: Path) -> None:
    """The sweep is named by its repository path, as the batch check names it."""
    sweep = _sweep_file(tmp_path / "elsewhere")

    assert NC.main(["--sweep", str(sweep), "--repo", str(tmp_path / "repo")]) == 3


def test_the_record_round_trips_as_json() -> None:
    """The record is plain JSON the draft can embed."""
    body = NC.record(NC.NullCheck(20, 0, 0.05), _sweep(), "text", 5)

    assert json.loads(json.dumps(body))["verdict"] == "PASS"
