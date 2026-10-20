# Change: 1310-sweep-train-chunks

**SDD change**: `1310-sweep-train-chunks`
**Date**: 2026-10-20
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1310-sweep-train-chunks/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-031 lets one signed batch hold a sweep of up to 150 grid points. This
change makes `backtest-batch.yml` run one. Train runs every point in jobs of
up to 20, one after another on one trial log chain. The Train judge counts N
once every Train touch is in. Each family then sends one Train passer to
Validation, the one with the highest Train DSR, unless the family has no
Validation touch left. The champion rule then judges only the candidates
sent, so its m is their count. A plain batch runs as before, in one chunk.

## Scope

### In

- `scripts/backtest/run_portfolio_backtest.py`: `--batch` reads a sweep. The
  roster entry is the family's template. The point's values replace the
  template's defaults. Trial lines and evidence carry the point's id.
- `src/hermes/entrypoints/funnel_batch.py`: on a sweep's Train, it lists the
  family winners (`family_winners`). On a sweep's Validation, it judges only
  the ids in `--sent` (`validation_batch`).
- `scripts/backtest/validation_budget.py`: a sweep's grid points count in
  their family's budget. `--winners` / `--sent-out` drop each winner whose
  family is spent and write `sweep-selection.json`, which holds ids only.
- `.github/workflows/backtest-batch.yml`: the check job emits `chunks` and
  `sweep`. Each Train job runs a chunk in order. Evidence is uploaded as one
  artifact per chunk. The selection step runs in the Train judge, and
  Validation takes `--sent`.

### Out

- Per-candidate verdict records and the Lab hand-off for a sweep's champion
  (#1312).
- The first sweep draft (#1313).

## Decisions

### D-1: Chunks of 20 candidates, one job at a time

- **Decision**: The check job splits the candidate list into chunks of 20.
  The Train matrix is the chunk list, with `max-parallel: 1`. Each job runs
  its candidates in a loop on the chain the previous job uploaded.
- **Alternatives**: One job per candidate (150 jobs, each repeating checkout,
  uv sync and the data check); one job for the whole sweep (past the
  120-minute limit).
- **Rationale**: ADR-031 §2.
- **Cost of reversal**: LOW: one number in the check step.

### D-2: The budget filter after the DSR pick

- **Decision**: `family_winners` picks first. `validation_budget.py --winners`
  then drops each winner whose family has used its five touches, and the
  record keeps both lists.
- **Alternatives**: Skip a spent family before Train (its points still count
  in N, and the ADR runs Train for every point).
- **Rationale**: ADR-031 §3 enforces the budget on what reaches Validation;
  the per-candidate budget step in the Validation job stays as a second guard.
- **Cost of reversal**: LOW.

### D-3: Validation judges the ids the selection record sent

- **Decision**: `hermes funnel --batch SWEEP --stage validation` requires
  `--sent`, refuses it on a plain batch, and refuses an id outside the sweep.
- **Alternatives**: Judge the candidates that have evidence folders (a
  candidate whose job died would drop out of m silently).
- **Rationale**: ADR-031 §4 m rule (family winners).
- **Cost of reversal**: LOW.

## Requirements

### R-1: The driver runs a grid point

- **A-1.1**: `uv run pytest tests/unit/scripts_backtest/test_run_portfolio_backtest.py -q` exits 0.

### R-2: One candidate per family, m = the ones sent

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_funnel_batch.py tests/unit/scripts_backtest/test_validation_budget.py -q` exits 0.

### R-3: Chunked Train in the workflow

- **A-3.1**: `uv run pytest tests/unit/scripts_backtest/test_backtest_batch_workflow.py -q` exits 0.
- **A-3.2**: `actionlint` and `zizmor` report nothing on the workflow.

## Design

The check job's `chunks` output for 45 candidates is
`[{chunk: 0, first: 0, ids: [20 ids]}, {chunk: 1, first: 20, ...}, {chunk: 2, first: 40, ids: [5 ids]}]`.
A job downloads the chain unless `first` is 0, checks it starts with the
committed log, and runs:

```bash
for candidate in ids; do
  run_portfolio_backtest.py --batch "$BATCH_FILE" --candidate "$candidate" ...
done
```

The report file, which holds values, is deleted before the chunk's evidence
artifact is uploaded. The Train judge reads every chunk's evidence through
`merge-multiple`. For a sweep it then runs:

```bash
validation_budget.py --trial-log chain.jsonl --batch "$BATCH_FILE" \
    --winners validation-candidates.json --sent-out sweep-selection.json
```

The Validation matrix is `sent`, in batch order. The champion judge downloads
the Train artifact and passes `--sent sweep-selection.json`.

Run locally on this tree: the four test files pass (129 tests), `actionlint`
1.7.12 and `zizmor` 1.31.1 report nothing, and the `dup`, `size` and
`deadcode` ratchets are ok.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` is green.
3. The first sweep (#1313) runs it on server-01.

## Risks

- A sweep's Train takes as long as its candidates. 150 candidates make 8
  jobs, each under 120 minutes at the rates measured for swing-b2.
- The Train judge's bootstrap is about 3 s per candidate, so its timeout is
  raised to 60 minutes.
