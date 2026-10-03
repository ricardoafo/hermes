# Change: 357-trial-log

**SDD change**: `357-trial-log`
**Date**: 2026-10-03
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#357)

Mirror of engram topic key `sdd/357-trial-log/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Pre-registration v1 §8.1 counts every backtest run during Train and Validation as a trial, and the Deflated Sharpe
Ratio penalises the best result by that count. Today nothing records trials, so the count would come from memory and
undercount. This change adds an append-only, hash-chained trial log that the backtest driver writes on every run that
evaluates the strategy, before the report exists, with no opt-out. A summary function reads the count, the
Validation touches and the Sharpe variance from the log. Main risk: deleting the newest lines (or the whole file) is
not detectable by the chain alone, so the log is committed to git and the chain catches every other edit.

## Scope

### In

- `domain/calibration/trial_record.py`: `TrialRecord`, `DatasetRef`, `TrialSummary`, `summarise_trials`.
- `domain/errors.py`: `TrialLogError`.
- `ports/trial_log.py`: `TrialLogPort` (append and read only).
- `adapters/trial_log/jsonl_file.py`: hash-chained JSON Lines store.
- `application/strategy_protocol/trial_log.py`: `trial_split`, `trial_summary`.
- `scripts/backtest/run_calibration.py`: builds the strategy from a recorded parameter set and appends a trial
  (strategy id, parameters, split, window, pinned datasets, headline metrics, git SHA and dirty flag) before writing
  the report. `--trial-log` sets the path; there is no flag to skip it.
- `scripts/backtest/trial_stats.py`: prints `{"count", "validation_count", "sharpe_variance"}`, the shape of the
  `trials` section of certification evidence.

### Out

- Per-trial Sharpe: the phase-1 driver has no per-period return series, so `sharpe` is `null` until #313 adds one.
  Until then `sharpe_variance` is `null`.
- Wiring the summary straight into `hermes certify`: the DSR code is in open PR #400, which takes the `trials` section
  from evidence JSON; `trial_stats.py` produces that section. A direct read lands with or after #400.

## Decisions

### D-1: A trial is a run that evaluated the strategy

- **Decision**: The driver appends a trial after the strategy pass and before the report is written; `--dry-run`
  (load bars, no strategy) is not a trial. A failure to append stops the run with exit 1 and no report.
- **Alternatives**: Log at start (counts runs that crashed before producing any result, which nobody could have
  tuned on). Log after the report (a crash between the two leaves an unlogged result on disk).
- **Rationale**: Pre-registration §8.1; issue acceptance "running a backtest without a trial record being written
  is impossible".
- **Cost of reversal**: LOW.

### D-2: Hash-chained JSON Lines, committed to the repo

- **Decision**: One JSON object per line with `prev_sha256` and `sha256` (SHA-256 of the canonical record JSON plus
  `prev_sha256`); reads and appends verify the whole chain. Default path
  `docs/experiments/swing-mvp1/trial-log.jsonl`, next to the lab notebook and the data manifest.
- **Alternatives**: SQLite table (no delete path is a convention, not a property; harder to review in a PR). A
  Markdown section in the lab notebook (free text, easy to undercount).
- **Rationale**: Edits, middle removals, reorders and truncation to empty fail loudly; git history covers tail
  deletion.
- **Cost of reversal**: LOW — the log is small and can be migrated.

### D-3: A window touching Validation at all is a Validation touch

- **Decision**: `trial_split` returns `VALIDATION` when the evaluation window intersects Validation, otherwise
  `TRAIN`; OOS is refused. Warm-up bars do not count.
- **Rationale**: Pre-registration §4 says to touch Validation sparingly and log every touch.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Every evaluating run is logged

- **A-1.1**: A `run_calibration.py` run without `--dry-run` appends exactly one line, or exits 1 without a report.

### R-2: Append-only, tamper-evident

- **A-2.1**: Editing, removing a non-final line, reordering or emptying the log makes `records()` and `append()`
  raise `TrialLogError`.

### R-3: The DSR count comes from the log

- **A-3.1**: `trial_summary(log)` and `scripts/backtest/trial_stats.py` report the number of logged trials and the
  Validation touches.

### R-4: Tests per ADR-007

- **A-4.1**: `uv run pytest tests/unit/domain/calibration tests/unit/adapters/trial_log
  tests/unit/application/strategy_protocol/test_trial_log.py tests/unit/scripts_backtest` passes.

## Design

```text
src/hermes/domain/calibration/trial_record.py          new
src/hermes/domain/errors.py                            + TrialLogError
src/hermes/ports/trial_log.py                          new
src/hermes/adapters/trial_log/{__init__,jsonl_file}.py new
src/hermes/application/strategy_protocol/trial_log.py  new
scripts/backtest/run_calibration.py                    append a trial per run
scripts/backtest/trial_stats.py                        new
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. Two local driver runs (one Train, one reaching Validation) append two chained lines and `trial_stats.py` prints
   `count: 2, validation_count: 1`.

## Risks

- **Tail deletion.** Removing the newest lines leaves a valid shorter chain. Mitigation: the log is committed, so a
  shrinking file shows in review; a test documents the limit.
- **Uncommitted runs.** A run on a machine whose log never reaches git is invisible to the count. Mitigation: the
  default path is in the repo, and `git_dirty` marks runs on uncommitted code.
- **Overlap with #313.** #313 rewrites the driver; the trial append is one block after the harness, easy to carry.

## Dependencies on other changes

- #356 (data manifest, merged) for the dataset ids and the split windows.
- #400 (certification statistics) consumes the trial count.
