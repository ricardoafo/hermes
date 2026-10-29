# Change: 1309-sweep-batch

**SDD change**: `1309-sweep-batch`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1309-sweep-batch/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-031 §1 adds a sweep: one signed batch whose candidates come from up to 12
family grids instead of being written by hand. This change parses `kind: sweep`
front matter, expands the grids in one fixed order into candidate ids like
`breakout@lookback=20,atr_mult=3.0`, and makes `hermes batch check` run a sweep's
checks. The sweep code lives in a new module, `research_sweep`, that reuses the
batch module's readers, because `research_batch.py` is at its size-ratchet cap.
The main risk is a later stage reading a generated id as a roster name; the
matrix written by `--candidates-out` names each point's template and values so
the workflow change (#1310) never has to parse an id.

## Scope

### In

- `src/hermes/application/calibration/research_sweep.py`: `Family`, `GridAxis`,
  `SweepPoint`, `SweepBatch`, `expand`, `candidate_id`, `is_sweep`,
  `parse_sweep`, `check_sweep`.
- `hermes batch check` dispatches on `kind: sweep`; `--candidates-out` writes a
  sweep's template and grid values per candidate, and `max_candidates`.
- Unit and property tests; one end-to-end check on a real throwaway repository.

### Out

- Running a sweep: chunked Train jobs and per-family Validation (#1310).
- The random-walk null check of ADR-031 §4 (#1311).
- The champion step, per-candidate verdicts and the Lab screen (#1312).
- 1h families: refused until issues #1237 and #1238 land (ADR-031 §6).
- `hermes funnel --batch` and `hermes certify` still load plain batches only;
  #1310 and #1312 teach them sweeps.

## Decisions

### D-1: A new module, not more of `research_batch.py`

- **Decision**: Sweep parsing and checks live in `research_sweep.py`, importing
  the batch module's private readers.
- **Alternatives**: Grow `research_batch.py` (refused by the size ratchet at 934
  lines); copy the readers (refused by the duplication ratchet, and two copies
  of the signing check could drift).
- **Rationale**: The signing hash, the funnel floors and the selection rule must
  mean the same thing for a sweep and a batch (ADR-031 §1 "as ADR-026 §1 and §5
  define them").
- **Cost of reversal**: LOW.

### D-2: A template is a roster strategy name

- **Decision**: `template` names an entry in `config/strategies.yaml`; with the
  roster's defaults, every grid axis must be a parameter that entry's strategy
  kind takes, of the same number kind (integer or decimal).
- **Alternatives**: Name the signal kind directly (would lose the entry's
  sizing, fees and exit pricing, which ADR-031 §1 says the family fixes).
- **Rationale**: A point is "its family's template at that point", so the
  driver runs the roster entry with the point's values as overrides.
- **Cost of reversal**: MEDIUM: #1310 builds on it.

### D-3: Expansion order and neighbours

- **Decision**: Families in document order; within a family the product of the
  axes in document order, last axis fastest. Axes list values ascending, so a
  point's neighbours for the ADR-021 neighbour check are the adjacent values on
  each axis (one at an edge).
- **Alternatives**: Sorted ids (hides the signed order); signed neighbours per
  point (150 hand-written blocks).
- **Rationale**: One order means one hash-chained trial log order (ADR-031 §2).
- **Cost of reversal**: HIGH once a sweep is signed; none before.

### D-4: Separate check names for a sweep

- **Decision**: `SWEEP_CHECK_NAMES` replaces `candidate_count` and
  `one_per_family` with `family_count`, `interval_allowed` and `candidate_cap`,
  and `strategy_ids_known` with `templates_known`.
- **Alternatives**: Reuse the batch names with new meanings (a FAIL line would
  name a rule the sweep does not have).
- **Rationale**: The operator reads the check name to know what to fix.
- **Cost of reversal**: LOW.

## Requirements

### R-1: A signed sweep passes and expands the same way every time

- **A-1.1**: `uv run pytest tests/unit/application/calibration/test_research_sweep.py -q` exits 0.
- **A-1.2**: `uv run pytest tests/unit/entrypoints/test_batch.py -q -k sweep` exits 0.

### R-2: `hermes batch check` refuses a sweep that breaks ADR-031 §1 or §6

Expansion past `max_candidates`, more than 12 families, a template not in the
roster, a 1h family, more than 3 grid axes: each has a test above.

### R-3: Plain batches are unchanged

- **A-3.1**: `uv run pytest tests/unit/application/calibration/test_research_batch.py tests/unit/entrypoints/test_batch.py -q` exits 0.

## Design

A sweep's front matter is a batch's without `candidates`, plus:

```yaml
kind: sweep
max_candidates: 10            # signed, 2..150; the expansion may not exceed it
families:                     # 1..12, each id once
  - family: breakout          # lowercase id; the candidate id prefix
    template: swing-alpha     # a name in config/strategies.yaml
    card: docs/experiments/<experiment>/cards/<card>.md
    card_sha256: <64 hex>
    section: C1               # one heading in the body
    summary: |
      <at most 6 lines>
    code_paths: [src/hermes/...]
    interval: 4h              # 1h after #1237 and #1238
    grid:                     # 1..3 axes, each >= 2 ascending values of one kind
      lookback: [15, 20, 25]
      atr_mult: ["2.5", "3.0"]
```

Check order: `front_matter`, `status_signed`, `content_sha256`, `family_count`,
`interval_allowed`, `summary_present`, `section_present`, `card_hashes`,
`parameters_cap`, `candidate_cap`, `funnel_floors`, `selection`,
`templates_known`, `code_paths`. Card and code checks run once per family.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` is green.
3. CI green on the PR.

Test tiers touched: unit, property

## Risks

- **A generated id read as a roster name**: `--candidates-out` writes each
  point's `template` and `parameters`, and #1310 runs from those fields.
- **YAML floats in a grid**: refused; decimals are quoted, as in a batch, so a
  value never changes by float rounding between signing and running.
- **A non-plain decimal such as `1e1`**: refused, so an id never holds an
  exponent and two spellings of one value cannot both appear.

## Dependencies on other changes

- ADR-031 (merged in PR #1284).
- Followed by #1311, #1310, #1312, #1313 in that order.
