#!/usr/bin/env python3
"""Random-walk null check for a sweep's champion rule (ADR-031 §4).

Runs a whole sweep of the signed shape (same families, grids, funnel and
selection) on many seeded random-walk price series that hold no edge, and
counts how often a champion comes out. ADR-031 §4 keeps BH's m at the count of
candidates sent to Validation only while that false-champion rate is at most
the sweep's ``fdr_q``; above it, sweeps use m = every candidate tested.

Each series runs the steps the backtest workflow runs, in process:

1. Every candidate's Train replays (base, each neighbour, the cost stress)
   through the backtest driver's own replay and fill kernel.
2. The Train judge (``judge_stage``) with one trial count for the series.
3. One candidate per family to Validation (``family_winners``).
4. Their Validation replays, then the Validation judge over the winners only,
   so BH's m is their count (``validation_batch``).

Only generated prices are used: no dataset, manifest data or trial log is read,
and nothing is written outside a temporary directory. The trial count is the
series' own trial lines, which is lower than a real sweep's (the real log also
holds every earlier trial), so a series can name a champion more easily here
than in a real run; the check errs toward FAIL.

Prices: each pair's 4h log return is Gaussian with zero drift, built from one
market factor plus the pair's own noise, so pairs move together as crypto
majors do; daily bars are aggregated from the 4h bars.

Usage:
  uv run python scripts/backtest/null_check.py --sweep FILE [--series N]
      [--first-seed S] [--jobs J] [--repo DIR] [--out FILE]

Output: one line per series (``series <seed>: champion`` or ``no champion``),
then ``NULL CHECK PASSED`` or ``NULL CHECK FAILED`` with the count. ``--out``
writes the record the sweep's draft carries before signing (contract: counts
and the verdict only).

Exit codes:
  0  passed: the false-champion rate is at most q
  1  failed: the rate is above q
  2  usage error, or fewer series than 1/q (the rate could not show q)
  3  the sweep, the roster or a run failed

Reference: ADR-031 §4; issue #1311.
"""

import argparse
import contextlib
import hashlib
import io
import json
import logging
import math
import os
import random
import statistics
import sys
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import structlog
from funnel_evidence import funnel_evidence
from run_calibration_full import _select
from run_portfolio_backtest import FEE_HISTORY_CSV, _kernel, _merge, _parse_pair, _replay_one

from hermes.adapters.fees.binance_history import load_binance_fee_history
from hermes.adapters.files import LocalFiles
from hermes.application.backtest.funnel_replay import FunnelReplay
from hermes.application.backtest.performance import performance_file, round_trips
from hermes.application.calibration.batch_stage import Stage, judge_stage
from hermes.application.calibration.research_batch import Candidate
from hermes.application.calibration.research_sweep import SweepBatch, SweepPoint, parse_sweep
from hermes.application.calibration.sweep_selection import family_winners, validation_batch
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord, TrialSummary
from hermes.domain.fees.fee_schedule_selector import select_fee_schedule
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure.project_root import load_pre_registration

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
TRAIN_START: Final = date(2020, 9, 1)
"""ADR-031 Context: Train starts 2020-09-01 (the experiment's manifest split)."""
VALIDATION_END: Final = date(2025, 8, 31)
"""The last Validation day; the sealed window starts the day after (ADR-023 §4)."""
BARS_PER_DAY: Final = 6
VOLATILITY_4H: Final = 0.012
"""About 3% a day, 56% a year: the order of BTC's realised volatility since 2020."""
PAIR_CORRELATION: Final = 0.7
"""Majors move together; independent pairs would make the shared portfolio look safer."""
START_PRICE: Final = 100.0
_WICK: Final = 0.003
_TICK: Final = Decimal("0.00000001")
_VOLUME: Final = Decimal(1_000_000)
_SYNTHETIC_SHA: Final = "0" * 40
"""The git sha on a synthetic trial line: no commit ran it."""
_BOOTSTRAP_SEED: Final = 0
RECORD_SCHEMA: Final = "hermes.null-check/1"


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------


def _price(value: float) -> Decimal:
    return Decimal(f"{value:.8f}").quantize(_TICK)


def random_walk(seed: int, pairs: Sequence[Pair], start: date, end: date) -> list[Bar]:
    """Seeded zero-drift 4h and daily bars for ``pairs``, ``start`` to ``end``, in delivery order.

    The same seed, pairs and dates always give the same bars.
    """
    rng = random.Random(f"null-check:{seed}")  # noqa: S311 — reproducible synthetic prices, not a secret
    own = math.sqrt(1.0 - PAIR_CORRELATION)
    shared = math.sqrt(PAIR_CORRELATION)
    closes = dict.fromkeys(pairs, START_PRICE)
    bars: list[Bar] = []
    day = datetime(start.year, start.month, start.day, tzinfo=UTC)
    last = datetime(end.year, end.month, end.day, tzinfo=UTC)
    while day <= last:
        legs: dict[Pair, list[Bar]] = {p: [] for p in pairs}
        for k in range(BARS_PER_DAY):
            market = rng.gauss(0.0, 1.0)
            opened = day + timedelta(hours=4 * k)
            for pair in pairs:
                shock = VOLATILITY_4H * (shared * market + own * rng.gauss(0.0, 1.0))
                previous = closes[pair]
                close = previous * math.exp(shock - VOLATILITY_4H**2 / 2)
                closes[pair] = close
                high = max(previous, close) * (1 + rng.uniform(0, _WICK))
                low = min(previous, close) * (1 - rng.uniform(0, _WICK))
                legs[pair].append(
                    Bar(
                        pair=pair,
                        interval=KlineInterval.H4,
                        open_time=opened,
                        open=_price(previous),
                        high=_price(high),
                        low=_price(low),
                        close=_price(close),
                        volume=_VOLUME,
                        close_time=opened + timedelta(hours=4, milliseconds=-1),
                    )
                )
        for pair, h4 in legs.items():
            bars += h4
            bars.append(
                Bar(
                    pair=pair,
                    interval=KlineInterval.D1,
                    open_time=day,
                    open=h4[0].open,
                    high=max(b.high for b in h4),
                    low=min(b.low for b in h4),
                    close=h4[-1].close,
                    volume=_VOLUME * BARS_PER_DAY,
                    close_time=day + timedelta(days=1, milliseconds=-1),
                )
            )
        day += timedelta(days=1)
    return _merge(bars)


# ---------------------------------------------------------------------------
# One candidate's run
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateRun:
    """One candidate's run at one stage: its three evidence files and its trial Sharpes."""

    performance: dict[str, Any]
    evidence: dict[str, Any]
    funnel: dict[str, Any]
    sharpes: tuple[tuple[str, float | None], ...]
    """One per replay, by trial id, in replay order."""


Runner = Callable[[SweepPoint, Candidate, Sequence[Bar], datetime, datetime], CandidateRun]
"""Runs one candidate's replays on ``bars`` over ``[start, end]``."""


@dataclass(frozen=True, slots=True)
class EngineRunner:
    """Runs a candidate through the backtest driver's own replay, as the workflow does.

    Args:
        config_dir: Holds ``strategies.yaml``; each family's template is an entry.
        pairs: The signed universe the portfolio trades.
        depth_frac: The driver's default book depth fraction.
    """

    config_dir: Path
    pairs: tuple[Pair, ...]
    depth_frac: Decimal = Decimal("0.1")

    def __call__(
        self,
        point: SweepPoint,
        candidate: Candidate,
        bars: Sequence[Bar],
        start: datetime,
        end: datetime,
    ) -> CandidateRun:
        """Replay every funnel replay of ``candidate`` on ``bars``."""
        roster, model = _select(self.config_dir, point.template, self.depth_frac)
        fee_rows = load_binance_fee_history(FEE_HISTORY_CSV)

        def fees_at(at: datetime, pair: Pair) -> Any:
            return select_fee_schedule(fee_rows, at, pair)

        kind = roster.strategy_kind()
        point_parameters = {**kind.default_parameters(), **dict(point.values)}
        replays: tuple[FunnelReplay, ...] = candidate.funnel_replays()
        cash = Money(amount=roster.capital_budget, currency=roster.quote)
        window = [b for b in bars if b.close_time <= end]
        results = [
            _replay_one(
                spec,
                window,
                kind=kind,
                parameters=spec.parameters(point_parameters),
                model=spec.model(model),
                kernel=_kernel(StrategyId(roster.name), cash, roster.sizing_policy()),
                fees_at=spec.fees(fees_at),
                pairs=self.pairs,
                start=start,
                end=end,
            )
            for spec in replays
        ]
        primary = results[0]
        sharpes = tuple((r.replay.trial_id, r.sharpe) for r in results)
        # The judge replaces this with the series-wide count; the file only needs a valid block.
        known = [x for _, x in sharpes if x is not None]
        variance = statistics.variance(known) if len(known) > 1 else 0.0
        trials = TrialSummary(len(sharpes), 0, variance)
        return CandidateRun(
            performance=performance_file(
                primary.run, trials=trials, bootstrap_seed=_BOOTSTRAP_SEED
            ),
            evidence={"backtest": {"calibration_fill_count": len(primary.run.fills)}},
            funnel=funnel_evidence(
                candidate.strategy_id,
                {r.replay.trial_id: r.run.daily_equity for r in results},
                trade_count=len(round_trips(primary.run.fills)),
                replays=replays,
            ),
            sharpes=sharpes,
        )


# ---------------------------------------------------------------------------
# One series
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeriesResult:
    """What one random-walk series gave: how many went on, and whether a champion came out."""

    seed: int
    train_passed: int
    validated: int
    champion: bool


def _records(
    batch_id: str,
    candidate_id: str,
    run: CandidateRun,
    split: DataSplit,
    start: datetime,
    end: datetime,
    seed: int,
) -> list[TrialRecord]:
    dataset = DatasetRef(
        f"synthetic/random-walk/{seed}", hashlib.sha256(f"null-check:{seed}".encode()).hexdigest()
    )
    return [
        TrialRecord(
            recorded_at=end,
            strategy_id=candidate_id,
            parameters={"batch.id": batch_id, "trial": trial_id},
            split=split,
            window_start=start,
            window_end=end + timedelta(days=1),
            datasets=(dataset,),
            metrics={},
            sharpe=sharpe,
            git_sha=_SYNTHETIC_SHA,
            git_dirty=False,
        )
        for trial_id, sharpe in run.sharpes
    ]


def _write(folder: Path, run: CandidateRun) -> None:
    folder.mkdir(parents=True)
    for name, body in (
        ("performance.json", run.performance),
        ("evidence.json", run.evidence),
        ("funnel-evidence.json", run.funnel),
    ):
        (folder / name).write_text(json.dumps(body, sort_keys=True), encoding="utf-8")


def run_series(
    sweep: SweepBatch,
    seed: int,
    runner: Runner,
    market: Callable[[int], list[Bar]],
    *,
    train_start: date = TRAIN_START,
    validation_end: date = VALIDATION_END,
) -> SeriesResult:
    """Run the whole sweep on one random-walk series and say whether a champion came out.

    Args:
        sweep: The parsed sweep.
        seed: The series' seed.
        runner: Runs one candidate's replays.
        market: The series' bars for a seed, from ``train_start`` to ``validation_end``.
        train_start: The first day of Train.
        validation_end: The last day of Validation.
    """
    batch = sweep.batch
    points = {p.candidate_id: p for p in sweep.points}
    bars = market(seed)
    v1 = load_pre_registration()
    start = datetime(train_start.year, train_start.month, train_start.day, tzinfo=UTC)
    train_end = batch.funnel.thresholds.train_end
    windows: dict[Stage, tuple[datetime, DataSplit]] = {
        "train": (_day_end(train_end), DataSplit.TRAIN),
        "validation": (_day_end(validation_end), DataSplit.VALIDATION),
    }
    records: list[TrialRecord] = []
    with tempfile.TemporaryDirectory(prefix=f"null-check-{seed}-") as tmp:

        def stage_run(stage: Stage, candidates: Sequence[Candidate]) -> Path:
            end, split = windows[stage]
            evidence = Path(tmp) / stage
            evidence.mkdir()
            for candidate in candidates:
                run = runner(points[candidate.strategy_id], candidate, bars, start, end)
                records.extend(
                    _records(batch.batch_id, candidate.strategy_id, run, split, start, end, seed)
                )
                _write(evidence / candidate.strategy_id, run)
            return evidence

        files = LocalFiles()
        train_dir = stage_run("train", batch.candidates)
        train = judge_stage(batch, "train", train_dir, records, v1=v1, files=files)
        winners = family_winners(train)
        if not winners:
            return SeriesResult(seed, len(train.goes_on), 0, champion=False)
        judged = validation_batch(batch, winners)
        validation_dir = stage_run("validation", judged.candidates)
        validation = judge_stage(judged, "validation", validation_dir, records, v1=v1, files=files)
    return SeriesResult(
        seed, len(train.goes_on), len(winners), champion=validation.champion is not None
    )


def _day_end(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1, milliseconds=-1)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NullCheck:
    """The check's outcome over every series."""

    series: int
    champions: int
    fdr_q: float

    @property
    def rate(self) -> float:
        """The share of series that named a champion."""
        return self.champions / self.series

    @property
    def passed(self) -> bool:
        """ADR-031 §4: the family-winners m stands while the rate is at most q."""
        return self.rate <= self.fdr_q


def minimum_series(fdr_q: float) -> int:
    """The fewest series whose rate can show q: one champion in them is a rate of at most q."""
    return math.ceil(1 / fdr_q - 1e-9)


def record(check: NullCheck, sweep: SweepBatch, text: str, first_seed: int) -> dict[str, object]:
    """The record the sweep's draft carries: counts and the verdict, no metric value."""
    return {
        "schema": RECORD_SCHEMA,
        "batch_id": sweep.batch.batch_id,
        "sweep_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "candidates": len(sweep.points),
        "families": len(sweep.families),
        "series": check.series,
        "seeds": [first_seed, first_seed + check.series - 1],
        "champions": check.champions,
        "fdr_q": check.fdr_q,
        "verdict": "PASS" if check.passed else "FAIL",
        "m_rule": "family_winners" if check.passed else "every_candidate",
    }


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Job:
    path: str
    text: str
    config_dir: Path
    symbols: tuple[str, ...]


def _series(job: _Job, seed: int) -> SeriesResult:
    # Thousands of replays per series: their order logs and progress lines would bury the verdict.
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    sweep = parse_sweep(job.path, job.text)
    pairs = tuple(_parse_pair(s) for s in job.symbols)
    with contextlib.redirect_stdout(io.StringIO()):
        return run_series(
            sweep,
            seed,
            EngineRunner(job.config_dir, pairs),
            lambda s: random_walk(s, pairs, TRAIN_START, VALIDATION_END),
        )


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sweep", type=Path, required=True)
    parser.add_argument("--series", type=int, default=40)
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--config-dir", type=Path, default=REPO_ROOT / "config")
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the null check and print one line per series, then the verdict."""
    try:
        args = _parse(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    try:
        text = args.sweep.read_text(encoding="utf-8")
        path = args.sweep.resolve().relative_to(args.repo.resolve()).as_posix()
        sweep = parse_sweep(path, text)
    except (OSError, ValueError) as exc:
        print(f"ERROR: sweep: {exc}", file=sys.stderr)
        return 3
    q = sweep.batch.selection.fdr_q
    if args.series < minimum_series(q) or args.jobs < 1:
        print(
            f"ERROR: --series must be at least {minimum_series(q)} (1/q) and --jobs at least 1",
            file=sys.stderr,
        )
        return 2
    job = _Job(path, text, args.config_dir, load_pre_registration().universe())
    seeds = range(args.first_seed, args.first_seed + args.series)
    print(
        f"# null check {sweep.batch.batch_id}: {len(sweep.points)} candidates, "
        f"{args.series} series, q {q:g}"
    )
    try:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(_series, [job] * len(seeds), seeds))
    except Exception as exc:  # any run failure is an error, never a verdict
        print(f"ERROR: run: {exc!r}", file=sys.stderr)
        return 3
    for result in results:
        print(f"series {result.seed}: {'champion' if result.champion else 'no champion'}")
    check = NullCheck(len(results), sum(r.champion for r in results), q)
    verdict = "PASSED" if check.passed else "FAILED"
    print(f"NULL CHECK {verdict}: {check.champions} of {check.series} series named a champion")
    if args.out is not None:
        body = record(check, sweep, text, args.first_seed)
        args.out.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return 0 if check.passed else 1


if __name__ == "__main__":
    sys.exit(main())
