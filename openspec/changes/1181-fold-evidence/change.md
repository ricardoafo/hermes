# Change: 1181-fold-evidence

**SDD change**: `1181-fold-evidence`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (part of #1181)

Mirror of engram topic key `sdd/1181-fold-evidence/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Pre-registration v1 §8.3 (kept by v2) promises walk-forward and purged
combinatorial cross-validation on Train and Validation. `walk_forward_splits`
and `purged_cpcv_splits` (#354, D-5) exist but nothing calls them. On 18 Oct
2026 the Code Owner decided to wire them "as evidence that doesn't gate
anything". This change adds a `folds` section to the funnel evidence file every
backtest run already writes, with each fold's dates, Sharpe and return and a
summary per scheme. No verdict, threshold, selection, parameter, sizing, fee or
kill rule changes; the reader skips the section and an import contract keeps
the judging code away from it. The main risk is a reader taking the folds for a
gate; the docstrings, the contract and a verdict-identity test answer that.

## Scope

### In

- `src/hermes/domain/statistics/fold_metrics.py`: a fold side's date ranges,
  day count, per-day Sharpe and compounded return; the per-scheme summary.
- `src/hermes/application/backtest/fold_evidence.py`: the `folds` JSON section
  with the signed scheme settings.
- `scripts/backtest/funnel_evidence.py`: `funnel_evidence()` adds
  `"folds": fold_evidence(start_date, base daily equity)`.
- `src/hermes/application/calibration/evidence_files.py`: `folds` is an
  accepted key of the funnel evidence file, never parsed.
- `pyproject.toml`: import contract `fold-evidence-is-reported-only`.
- Tests: unit, property, and verdict identity through `hermes funnel` and
  `hermes funnel --batch`.

### Out

- Any gate on the folds. How folds could feed PASS/FAIL is a gate-content
  decision for the Code Owner (judgement §17) and needs a signed
  pre-registration or batch change first.
- `evidence.json`: the batch workflow builds it with `jq` from the report
  (`.github/workflows/backtest-batch.yml`). Workflows are not touched here.
- The research-funnel interface contract text (owned by the Backtesting
  thread): see Dependencies.
- The console Lab screen showing the folds.

## Decisions

### D-1: The folds live in the funnel evidence file

- **Decision**: `funnel_evidence()` adds a `folds` section to
  `funnel-evidence.json`.
- **Alternatives**: `evidence.json` (built by `jq` inside the workflow, so it
  needs a workflow change); a new `--folds-out` file (the workflow would have
  to pass and upload it); `run_calibration_full.py` (single-pair driver the
  funnel does not use).
- **Rationale**: every Train and Validation run, batch or single, already
  writes this file with `--funnel-evidence-out` and uploads it with the
  candidate's evidence, so the folds reach the artifact with no workflow edit.
  The file already holds the base run's daily equity, so the folds need no new
  data and no new backtest.
- **Cost of reversal**: LOW: drop the key from the writer; the reader keeps
  accepting it.

### D-2: "Retrain" re-evaluates the fixed rule

- **Decision**: each fold reads the one run's daily returns on its train days
  and its test days; nothing is fitted.
- **Alternatives**: re-running the backtest per fold (more trials, more data
  reads, and nothing to fit with fixed parameters).
- **Rationale**: the candidates have fixed parameters (ADR-026 §1; batch
  cards). With nothing to fit, a retrain every 6 months is the same rule, so
  each fold is a window of the same run. No new trial is appended: the folds
  are views of a run already logged, not new configurations (§8.1 counts
  configurations tried).
- **Cost of reversal**: MEDIUM: per-fold re-runs would need the driver to
  replay each window and to log each as a trial.

### D-3: The folds cover the run's own window

- **Decision**: the folds split whatever days the run covered.
- **Rationale**: a Train run's evidence ends at the Train end, so its folds
  are Train only. A Validation run (Train start to Validation end) already
  reads Validation for that stage, and §8.3 names Train + Validation, so its
  folds cover that window. No extra Validation touch happens: the folds read
  the run already counted. OOS is refused before any run (the workflow's
  window check, the manifest wall, `validate_evidence`), and the folds read
  only the run's curve.
- **Cost of reversal**: LOW.

### D-4: Scheme settings

- **Decision**: walk-forward with a 12-month first train window, 6-month step,
  3-month tests; purged CPCV with 5 groups, 2 held out per fold (10 folds),
  5-day purge, 3-day embargo.
- **Rationale**: §8.3 and openspec 354 D-5, the defaults `splits.py` already
  has. The settings are written into each scheme's section.
- **Cost of reversal**: LOW.

### D-5: Fold days are return days

- **Decision**: the splits index the base run's daily returns; return `i` is
  earned on `start_date + 1 + i`, as `daily_equity[i]` is the equity at the
  end of `start_date + i`.
- **Rationale**: Sharpe and return are functions of returns; dating each
  return by the day it is earned keeps purge and embargo in calendar days.
- **Cost of reversal**: LOW.

### D-6: The folds never gate, by construction

- **Decision**: the reader accepts `folds` and skips it whatever it holds; the
  `fold-evidence-is-reported-only` import contract forbids
  `hermes.application.calibration`, the order router, the strategy protocol,
  the strategy, fee and risk domains, and the `certify`, `funnel` and
  `funnel_batch` entrypoints from importing the fold modules.
- **Rationale**: a malformed `folds` value must not fail a judged run (exit 3
  would change the outcome), and no future edit should reach the folds from a
  gate without a visible contract change.
- **Cost of reversal**: LOW.

## Requirements

### R-1: The folds are §8.3's

- **A-1.1**: `uv run pytest tests/unit/application/backtest/test_fold_evidence.py`
  passes (signed settings; walk-forward boundary at 456 and 455 returns; CPCV
  dates with purge and embargo).
- **A-1.2**: `uv run pytest tests/unit/domain/statistics/test_fold_metrics.py`
  passes (purge and embargo at the boundary day and one past it; exact metrics;
  properties).

### R-2: The evidence file holds the folds of the base run

- **A-2.1**: `uv run pytest tests/unit/scripts_backtest/test_funnel_evidence.py`
  passes.

### R-3: No verdict changes

- **A-3.1**: `uv run pytest -k FoldEvidenceIsReportedOnly tests/unit/entrypoints`
  passes: `hermes funnel --batch` (train and validation) writes byte-identical
  outputs and stdout with and without folds, with a PASS, a FAIL and a
  champion among them; `hermes funnel` prints the same for a pass and a fail.
- **A-3.2**: `uv run lint-imports` keeps `fold-evidence-is-reported-only`.
- **A-3.3**: every existing test passes unchanged.

## Design

```text
run_portfolio_backtest.py --funnel-evidence-out F
  └─ funnel_evidence(strategy_id, curves, ...)            scripts/backtest/funnel_evidence.py
       └─ "folds": fold_evidence(start_date, base equity)  application/backtest/fold_evidence.py
            ├─ walk_forward_splits / purged_cpcv_splits     domain/statistics/splits.py
            └─ evaluate_folds / summarise_folds             domain/statistics/fold_metrics.py
hermes funnel [--batch] / certify
  └─ load_funnel_evidence: "folds" accepted, skipped       application/calibration/evidence_files.py
```

```json
"folds": {
  "walk_forward": {
    "min_train_months": 12, "step_months": 6, "test_months": 3,
    "folds": [{"train": {"ranges": [["2020-09-02", "2021-09-01"]], "days": 365,
                          "sharpe": 0.04, "total_return": 0.12},
               "test":  {"ranges": [["2021-09-02", "2021-12-01"]], "days": 91,
                          "sharpe": 0.02, "total_return": 0.01}}],
    "summary": {"folds": 1, "positive_test_folds": 1, "median_test_sharpe": 0.02}
  },
  "purged_cpcv": {"groups": 5, "test_groups": 2, "purge_days": 5, "embargo_days": 3,
                  "folds": ["... 10 folds ..."], "summary": {"...": "..."}}
}
```

(Values illustrative.) Sharpe is per day, unannualised, `None` when undefined;
`total_return` is `None` for a side with no day. A curve too short for a
scheme gives it an empty `folds` list.

## Test plan

1. Every acceptance criterion above passes.
2. Planted bugs: dropping the purge, a 2-day or 4-day embargo, `>=` for a
   positive fold, dating folds from `start_date`, purge 4 in the settings,
   folds from the stress curve, a reader that rejects or reads `folds`, and a
   judge module importing the folds each fail at least one test or the import
   contract.
3. `uv run pytest -n auto -W error tests` green.

Test tiers touched: unit, property

## Risks

- **A reader takes the folds for a gate.** Mitigation: module docstrings, the
  import contract and the verdict-identity tests.
- **A fold window is not a fresh start.** Positions and indicator state carry
  across fold boundaries because each fold is a window of one run. For a fixed
  rule this is the honest reading of "retrain"; a fresh-start per fold would be
  a different experiment (D-2).
- **The evidence file grows.** Ten CPCV folds and a few walk-forward folds add
  a few kilobytes beside the daily equity and trial series already there.
- **Evidence digest.** `judge_candidate` hashes the funnel evidence bytes into
  the gate evidence digest, so the digest of a run with folds differs from one
  without. The digest identifies the file; no verdict reads it.

## Dependencies on other changes

- Research funnel interface contract (owned by the Backtesting thread): §2 says
  every driver output is unchanged. The funnel evidence file now carries an
  optional `folds` key, reported only and never judged. The contract should
  note it in §2 and §4 in its next version.
- No ADR changes. No workflow changes.
