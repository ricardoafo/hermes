# Change: 1311-sweep-null-check

**SDD change**: `1311-sweep-null-check`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1311-sweep-null-check/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-031 §4 keeps Benjamini-Hochberg's m at the candidates sent to Validation
(one per family) only if a null check shows that rule rarely names a champion
when there is no edge. This change adds that check: a script that runs a whole
sweep, through the backtest driver's own replay and the real judge, on many
seeded random-walk price series, and reports PASS when the false-champion rate
is at most the sweep's q. It also adds `family_winners` and `validation_batch`,
the per-family Validation pick that #1310 and #1312 reuse. The main risk is
cost: a 150-candidate sweep takes roughly 14 core-hours for 40 series, measured
below.

## Scope

### In

- `src/hermes/application/calibration/sweep_selection.py`: `family_winners`,
  `validation_batch`.
- `scripts/backtest/null_check.py`: `random_walk`, `EngineRunner`,
  `run_series`, `NullCheck`, `minimum_series`, `record`, `main`.
- Unit and property tests for both; a planted-edge case that must find a
  champion; one test through the real engine on a small sweep.

### Out

- Running the check for the first sweep, and putting its record in the draft
  (#1313). It runs on server-01 when Rick starts it.
- The workflow's per-family Validation (#1310) and champion step (#1312); they
  call `family_winners` and `validation_batch`.

## Decisions

### D-1: The real driver and judge, not a model of them

- **Decision**: Each series runs every candidate's funnel replays through
  `run_portfolio_backtest._replay_one` and `_kernel`, then `judge_stage` on
  Train and on the Validation winners, exactly as the workflow does.
- **Alternatives**: Simulate return series per candidate (fast, but it would
  test a model of the gates, not the gates); short-circuit candidates that fail
  a base-run row (changes the trial Sharpe variance the DSR uses).
- **Rationale**: ADR-031 §4 asks for "a full sweep of the signed shape (same
  families, grids and gates)".
- **Cost of reversal**: LOW.

### D-2: Each series counts only its own trials

- **Decision**: N is the series' own trial lines (Train, then Train plus
  Validation). No trial log is read.
- **Alternatives**: Add the experiment's real log (issue #1311 forbids reading
  it, and its values must not reach an agent).
- **Rationale**: A smaller N deflates the DSR less, so a series names a
  champion more easily than a real sweep would. The check errs toward FAIL.
- **Cost of reversal**: LOW.

### D-3: Prices

- **Decision**: Zero-drift Gaussian 4h log returns at 1.2% per bar (about 56% a
  year), one market factor with pair correlation 0.7, wicks up to 0.3%, daily
  bars aggregated from the 4h bars, every pair from 100, from 2020-09-01 to
  2025-08-31 (Train start to Validation end, no warm-up before Train, as the
  driver loads).
- **Alternatives**: Independent pairs (understates how the shared portfolio
  moves); bootstrapped real returns (reads real data, which the issue forbids).
- **Rationale**: No edge by construction, and close enough to crypto majors
  that the strategies trade as they would.
- **Cost of reversal**: LOW: constants in the script.

### D-4: PASS means rate at most q, over at least 1/q series

- **Decision**: PASS iff champions / series <= q; fewer than 1/q series is a
  usage error. Default 40 series.
- **Alternatives**: A confidence bound on the rate (needs hundreds of series at
  this cost).
- **Rationale**: ADR-031 §4 says "if that false-champion rate is above the
  batch's q, sweeps use m = every candidate tested".
- **Cost of reversal**: LOW.

## Requirements

### R-1: The family pick

- **A-1.1**: `uv run pytest tests/unit/application/calibration/test_sweep_selection.py -q` exits 0.

### R-2: The null check

- **A-2.1**: `uv run pytest tests/unit/scripts_backtest/test_null_check.py -q` exits 0.
- **A-2.2**: A planted edge names a champion; noise names none (tests above).

## Design

```bash
uv run python scripts/backtest/null_check.py --sweep docs/experiments/<exp>/batches/<id>.md \
    --series 40 --jobs <cores> --out <record.json>
```

Output: `series <seed>: champion|no champion` per series, then
`NULL CHECK PASSED|FAILED: <k> of <n> series named a champion`. Exit 0 PASS,
1 FAIL, 2 usage, 3 error. The record (`hermes.null-check/1`) holds the sweep's
hash, the candidate and family counts, the seeds, the champion count, q, the
verdict and the m rule that follows.

Measured on 4 cores: a 4-candidate sweep over 20 series took 2 min 51 s, about
8.5 core-seconds per candidate per series (replays plus the judge's bootstrap).
A 150-candidate sweep over 40 series is about 51,000 core-seconds.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` is green.
3. CI green on the PR.

Test tiers touched: unit, property

## Risks

- **Run time on server-01**: the record above lets Rick size `--jobs`; the
  backtest runner is not used, so the workflow is not blocked.
- **Driver internals**: the script imports the driver's `_replay_one` and
  `_kernel`, so a change to them changes the check with them, which is the
  point; a renamed helper fails the engine test.

## Dependencies on other changes

- ADR-031 (PR #1284), the sweep format (PR #1320).
- Followed by #1310, #1312, #1313.
