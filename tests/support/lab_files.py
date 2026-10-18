"""A synthetic experiment for the Lab screen's tests (issue #1056).

Every id, family, parameter and figure here is made up. The trial lines carry
distinctive figures (:data:`FIGURES`) so a test can search a render for them.

Strategies:

- ``synth-ensemble``: signed batch b1, Train and Validation PASS, the champion;
- ``synth-daily-ma``: Train PASS, Validation FAIL;
- ``synth-slope``: Train FAIL;
- ``synth-pullback``: signed batch b1, Train PASS, a run used a parameter value
  that is neither the signed base nor a signed neighbour;
- ``synth-prereg-only``: a pre-registration with no run;
- ``synth-legacy``: runs in the trial log and nothing else (before the pipeline).
"""

import hashlib
import json
from pathlib import Path

from hermes.application.console.lab import LabFiles

__all__ = ["EXPERIMENT", "FIGURES", "synthetic_files", "write_experiment"]

EXPERIMENT = "synth-exp"
FIGURES = ("1.7391", "0.4268", "2.5137", "0.9086")
_BATCH = f"docs/experiments/{EXPERIMENT}/batches/b1.md"
_PREREG = f"docs/experiments/{EXPERIMENT}/pre-registration-v9.md"
_COMMIT = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

_BATCH_TEXT = """---
batch_id: b1
experiment: synth-exp
status: SIGNED
backtest_funnel:
  dsr_min: 0.95
  pbo_max: 0.05
  t_stat_above: 3.0
  trade_count_min: 100
  cost_stress_multiplier: 2.0
  positive_year_share_above: 0.5
  validation_to_train_return_min: 0.5
  neighbour_sharpe_ratio_min: 0.5
  return_correlation_below: 0.7
candidates:
  - strategy_id: synth-ensemble
    idea_family: breakout-ensemble
    card: docs/experiments/synth-exp/cards/synth-ensemble.md
    summary: |
      Long when breakouts over several lookbacks agree.
      Expected: fewer trades, better t-statistic.
    parameters:
      agree_min: {base: 2, neighbours: [1, 3]}
      lookback: {base: 120, neighbours: [90, 180]}
  - strategy_id: synth-daily-ma
    idea_family: daily-ma-trend
    summary: |
      Long after a daily close crosses above its average.
    parameters:
      sma_period: {base: 100, neighbours: [50, 150]}
  - strategy_id: synth-slope
    idea_family: slope-trend
    summary: |
      Long while the trend slope is positive.
    parameters:
      slope_window: {base: 20, neighbours: [10, 30]}
  - strategy_id: synth-pullback
    idea_family: pullback-in-uptrend
    summary: |
      Buy short pullbacks inside a daily uptrend.
    parameters:
      rsi_level: {base: 35, neighbours: [30, 40]}
---

# Research batch b1
"""

_PREREG_TEXT = """---
strategy_id: synth-prereg-only
idea_family: reversion
status: SIGNED
summary: |
  Fade stretched moves back to the mean.
---

# Pre-registration v9
"""

_CARD_TEXT = """---
card_id: synth-ensemble
---

# Card

## Mechanism

Agreement across horizons filters false single-length breakouts.

## Rules

- Trend: the daily close is above its average.
- Entry (long only): at least two lookbacks break out together.
- Exit, first to fire: a trailing stop; a time stop.

## Expected sign

- Fewer trades than the baseline.
- Bear stretch toward flat.

## Falsification test

Rejected if the cost-stress check fails on Train.
"""


def _line(
    previous: str | None,
    strategy: str,
    split: str,
    when: str,
    parameters: dict[str, object],
    figure: str,
) -> tuple[str, str]:
    body = {
        "strategy_id": strategy,
        "split": split,
        "window_start": "2020-09-01T00:00:00+00:00",
        "window_end": "2024-06-30T00:00:00+00:00",
        "git_sha": _COMMIT,
        "git_dirty": False,
        "recorded_at": when,
        "parameters": parameters,
        "metrics": {"dsr": float(figure)},
        "sharpe": float(figure),
        "prev_sha256": previous,
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return json.dumps({**body, "sha256": digest}, sort_keys=True), digest


def _trial_log() -> str:
    rows = (
        (
            "synth-ensemble",
            "train",
            "2026-10-17T18:00:00+00:00",
            {"agree_min": 2, "lookback": 120},
            FIGURES[0],
        ),
        (
            "synth-ensemble",
            "train",
            "2026-10-17T18:01:00+00:00",
            {"agree_min": 1, "lookback": 120},
            FIGURES[1],
        ),
        (
            "synth-ensemble",
            "validation",
            "2026-10-17T19:00:00+00:00",
            {"agree_min": 2, "lookback": 120, "batch.id": "b1"},
            FIGURES[2],
        ),
        ("synth-daily-ma", "train", "2026-10-17T18:02:00+00:00", {"sma_period": 100}, FIGURES[3]),
        (
            "synth-daily-ma",
            "validation",
            "2026-10-17T19:01:00+00:00",
            {"sma_period": 100, "batch.id": "b1"},
            FIGURES[1],
        ),
        ("synth-slope", "train", "2026-10-17T18:03:00+00:00", {"slope_window": 20}, FIGURES[0]),
        ("synth-pullback", "train", "2026-10-17T18:04:00+00:00", {"rsi_level": 35}, FIGURES[2]),
        ("synth-pullback", "train", "2026-10-17T18:05:00+00:00", {"rsi_level": 33}, FIGURES[3]),
        ("synth-legacy", "validation", "2026-10-08T14:38:00+00:00", {"period": 20}, FIGURES[1]),
    )
    lines: list[str] = []
    previous: str | None = None
    for strategy, split, when, parameters, figure in rows:
        text, previous = _line(previous, strategy, split, when, parameters, figure)
        lines.append(text)
    return "\n".join(lines) + "\n"


def _verdict(strategy: str, family: str, stage: str, checks: dict[str, str], **flags: bool) -> str:
    record = {
        "schema": "hermes.verdict/1",
        "batch_id": "b1",
        "strategy_id": strategy,
        "idea_family": family,
        "stage": stage,
        "run_id": 39056430551,
        "commit": _COMMIT,
        "window": {"start": "2020-09-01", "end": "2024-06-30"},
        "trial_lines": 2,
        "checks": [{"check": name, "status": status} for name, status in checks.items()],
        "passed": all(v != "FAIL" for v in checks.values()),
        **flags,
    }
    return json.dumps(record)


def _verdicts() -> dict[str, str]:
    base = f"docs/experiments/{EXPERIMENT}/verdicts/b1"
    all_pass = {
        "max_drawdown": "PASS",
        "recovery_days": "PASS",
        "profit_factor": "PASS",
        "ulcer_index": "PASS",
        "fees_to_gross_pnl": "PASS",
        "win_rate": "PASS",
        "dsr": "PASS",
        "pbo": "PASS",
        "t_stat": "PASS",
        "trade_count": "PASS",
        "cost_stress": "PASS",
        "positive_years": "PASS",
        "bull_return": "PASS",
        "bear_return": "PASS",
        "validation_vs_train": "NA",
        "neighbours": "PASS",
        "correlation": "PASS",
    }
    failing = {**all_pass, "dsr": "FAIL", "t_stat": "FAIL"}
    return {
        f"{base}/synth-ensemble.train.json": _verdict(
            "synth-ensemble", "breakout-ensemble", "train", all_pass, goes_on=True
        ),
        f"{base}/synth-ensemble.validation.json": _verdict(
            "synth-ensemble",
            "breakout-ensemble",
            "validation",
            {**all_pass, "validation_vs_train": "PASS", "bh_survivor": "PASS"},
            champion=True,
        ),
        f"{base}/synth-daily-ma.train.json": _verdict(
            "synth-daily-ma", "daily-ma-trend", "train", all_pass, goes_on=True
        ),
        f"{base}/synth-daily-ma.validation.json": _verdict(
            "synth-daily-ma", "daily-ma-trend", "validation", failing, champion=False
        ),
        f"{base}/synth-slope.train.json": _verdict(
            "synth-slope", "slope-trend", "train", failing, goes_on=False
        ),
        f"{base}/synth-slope.validation.json": _verdict(
            "synth-slope",
            "slope-trend",
            "validation",
            {"missing_evidence": "FAIL", "bh_survivor": "FAIL"},
            champion=False,
        ),
        f"{base}/synth-pullback.train.json": _verdict(
            "synth-pullback", "pullback-in-uptrend", "train", all_pass, goes_on=True
        ),
    }


def synthetic_files() -> LabFiles:
    """Return the synthetic experiment as the reader would hand it over."""
    return LabFiles(
        experiment=EXPERIMENT,
        batches={_BATCH: _BATCH_TEXT},
        pre_registrations={_PREREG: _PREREG_TEXT},
        cards={f"docs/experiments/{EXPERIMENT}/cards/synth-ensemble.md": _CARD_TEXT},
        trial_log=_trial_log(),
        verdicts=_verdicts(),
        notebook=f"docs/experiments/{EXPERIMENT}/lab-notebook.md",
    )


def write_experiment(repo: Path) -> Path:
    """Write the synthetic experiment under ``repo/docs/experiments`` and return that folder."""
    files = synthetic_files()
    for group in (files.batches, files.pre_registrations, files.cards, files.verdicts):
        for rel, text in group.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    folder = repo / "docs" / "experiments" / EXPERIMENT
    (folder / "trial-log.jsonl").write_text(files.trial_log, encoding="utf-8")
    (folder / "lab-notebook.md").write_text("# Lab notebook\n", encoding="utf-8")
    return repo / "docs" / "experiments"
