"""Synthetic batch candidates with the three evidence files the generic gate reads.

:func:`write_candidate` writes a signed batch whose funnel block fits a short
synthetic history (Train is 2021, Validation is 2022) and the candidate's
``performance.json``, ``evidence.json`` and ``funnel-evidence.json``. The
returns are made up and steady, so every check passes unless a test changes
one input. No market data is read.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.application.calibration.research_batch import ResearchBatch, parse_batch
from hermes.application.calibration.signed_gate import CandidateEvidence
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.market_data.dataset_manifest import DataSplit
from tests.support.research_batch import BATCH_PATH, batch_front_matter, batch_text

STRATEGY_ID = "swing-alpha"
START = date(2020, 12, 31)
TRAIN_END = date(2021, 12, 31)
DAYS = 730
"""31 Dec 2020 to 31 Dec 2022: Train is 2021, Validation is 2022."""
TRAIN_DAYS = 365
"""A history that ends on ``TRAIN_END``: a Train-only run."""

SECONDARY_V1 = "Unchanged from v1: max drawdown, recovery, profit factor, ulcer, fees, win rate."
BODY = (
    "\n# Batch b1\n\n"
    "## C1 — swing-alpha\n\n### 9. Metrics\n\n#### Secondary\n\n" + SECONDARY_V1 + "\n\n"
    "### 10. Kill rules\n\nRules.\n\n"
    "## C2 — swing-beta\n\n### 9. Metrics\n\n#### Secondary\n\n" + SECONDARY_V1 + "\n"
)

FUNNEL: dict[str, Any] = {
    "dsr_min": 0.95,
    "train_end": TRAIN_END.isoformat(),
    "oos_start": "2025-09-01",
    "cscv_blocks": 4,
    "pbo_max": 0.05,
    "t_stat_above": 3.0,
    "trade_count_min": 100,
    "cost_stress_multiplier": 2.0,
    "positive_year_share_above": 0.5,
    "validation_to_train_return_min": 0.5,
    "neighbour_sharpe_ratio_min": 0.5,
    "return_correlation_below": 0.7,
    "regimes": [
        {"kind": "bull", "start": "2021-02-01", "end": "2021-05-01"},
        {"kind": "bear", "start": "2021-07-01", "end": "2021-10-01"},
    ],
}


_EVERY_OTHER_DAY: Final = [
    datetime(START.year, START.month, START.day, 12, tzinfo=UTC) + timedelta(days=2 * i)
    for i in range(140)
]
"""140 round trips, at most 46 in any 90 days: well under the cap."""


def returns(drift: float, days: int = DAYS) -> list[float]:
    """Alternating returns ``drift ± 0.001``: positive mean, dispersion in every block."""
    return [drift + (0.001 if i % 2 else -0.001) for i in range(days)]


def equity(daily: list[float]) -> list[float]:
    """Return the equity curve that starts at 5,000 and earns ``daily``."""
    points = [5000.0]
    for r in daily:
        points.append(points[-1] * (1.0 + r))
    return points


def batch(body: str = BODY, *, round_trip_cap: int | None = None) -> ResearchBatch:
    """Return a SIGNED batch whose ``backtest_funnel`` fits the synthetic history.

    Every candidate signs the ADR-029 §4 cap of 250; ``round_trip_cap``
    signs another on the first candidate.
    """
    front_matter = batch_front_matter()
    front_matter["backtest_funnel"] = dict(FUNNEL)
    if round_trip_cap is not None:
        front_matter["candidates"][0]["round_trips_90d_max"] = round_trip_cap
    return parse_batch(BATCH_PATH, batch_text(front_matter, body))


@dataclass(frozen=True, slots=True)
class Written:
    """The files :func:`write_candidate` wrote."""

    files: CandidateEvidence
    batch_file: Path


def write_candidate(
    root: Path,
    *,
    days: int = DAYS,
    drift: float = 0.002,
    fills: int = 40,
    window: str = "train_val",
    body: str = BODY,
    strategy_id: str = STRATEGY_ID,
    round_trip_opened_at: list[datetime] | None = None,
    round_trip_times: bool = True,
) -> Written:
    """Write a batch under ``root`` and the candidate's three files beside it.

    Args:
        root: A clean directory; the batch goes to its repo-relative path.
        days: Days of returns; 365 ends on ``TRAIN_END`` (a Train-only run).
        drift: The mean daily return.
        fills: The calibration fill count in ``evidence.json``.
        window: The performance file's window.
        body: The batch body, to test the §9 secondary rule.
        strategy_id: The candidate; its files go to ``root/evidence/<strategy_id>/``.
        round_trip_opened_at: When each round trip opened; written with a
            matching ``trade_count``. ``None`` writes the 140 trips the
            fixture counts, one every two days from ``START``.
        round_trip_times: False writes no round-trip times at all.

    """
    front_matter = batch_front_matter()
    front_matter["backtest_funnel"] = dict(FUNNEL)
    batch_file = root / BATCH_PATH
    batch_file.parent.mkdir(parents=True, exist_ok=True)
    batch_file.write_text(batch_text(front_matter, body), encoding="utf-8")

    base = returns(drift, days)
    curve = equity(base)
    out = root / "evidence" / strategy_id
    out.mkdir(parents=True, exist_ok=True)
    performance = {
        "window": window,
        "daily_equity": curve,
        "trades": [{"gross_pnl": 50.0, "fees": 2.0}] * 90
        + [{"gross_pnl": -20.0, "fees": 2.0}] * 50,
        "trials": {"count": 1, "sharpe_variance": 0.0},
        "bootstrap_seed": 11,
    }
    funnel = {
        "strategy_id": strategy_id,
        "start_date": START.isoformat(),
        "daily_equity": curve,
        "trade_count": 140,
        "cost_stress": {"multiplier": 2.0, "daily_equity": equity(returns(drift / 2, days))},
        "trials": {
            "base": base,
            "lookback=25": returns(drift * 0.75, days),
            "cost_stress=2": returns(drift / 2, days),
        },
        "neighbour_trial_ids": ["lookback=25"],
        "running_strategies": {},
    }
    if round_trip_times:
        opened = round_trip_opened_at or _EVERY_OTHER_DAY
        funnel["trade_count"] = len(opened)
        funnel["round_trip_opened_at"] = [t.isoformat() for t in opened]
    files = CandidateEvidence(
        performance=out / "performance.json",
        evidence=out / "evidence.json",
        funnel_evidence=out / "funnel-evidence.json",
    )
    files.performance.write_text(json.dumps(performance), encoding="utf-8")
    files.evidence.write_text(
        json.dumps({"backtest": {"calibration_fill_count": fills}}), encoding="utf-8"
    )
    files.funnel_evidence.write_text(json.dumps(funnel), encoding="utf-8")
    return Written(files, batch_file)


def write_trial_log(path: Path, sharpes: list[float]) -> Path:
    """Write a hash-chained trial log with one Train trial per Sharpe in ``sharpes``."""
    log = JsonlTrialLog(path)
    for n, sharpe in enumerate(sharpes):
        log.append(
            TrialRecord(
                recorded_at=datetime(2026, 10, 18, 9, n, tzinfo=UTC),
                strategy_id=STRATEGY_ID,
                parameters={"lookback": str(20 + n), "batch.id": "b1"},
                split=DataSplit.TRAIN,
                window_start=datetime(2020, 12, 31, tzinfo=UTC),
                window_end=datetime(2021, 12, 31, tzinfo=UTC),
                datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
                metrics={"fills_count": "40"},
                sharpe=sharpe,
                git_sha="d" * 40,
                git_dirty=False,
            )
        )
    return path
