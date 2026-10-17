# Change: 1030-risk-inputs-view

**SDD change**: `1030-risk-inputs-view`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1030-risk-inputs-view/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Under the #514 ruling (the shadow set), shadow records the RO-02 spread
inputs and the equity history behind the §10 recovery kill but never acts on
them. A read-only `hermes risk-inputs` command shows them, says whether each
rule would have fired, and lists the latest recorded RO-01 correlations. It
reads the ledger with `mode=ro`, so it is safe next to serve. The only risk is
a misleading verdict, so the verdicts are pure functions with their own tests.

## Scope

### In

- `hermes.application.risk_inputs.readings`: `spread_reading`, `recovery_reading`.
- `hermes risk-inputs [--strategy ID] [--db PATH] [--config-dir DIR]`.

### Out

- Any decision on these readings: shadow keeps not acting on them (#514).
- The single SA-9 P0 kill: it has no recorded input of its own beyond the
  rule fires the #796 shadow kill already counts.
- A console screen: the console is read-only and its screens are another lane.

## Decisions

### D-1: The recovery verdict follows pre-registration §10's wording

- **Decision**: would fire after more than 90 days without a new high that
  include a drawdown of at least 8%.
- **Alternatives**: mirror `KillTriggerEvaluator`, which fires on 90 days
  since the high alone.
- **Rationale**: the signed text binds; the view is where the difference
  becomes visible before the live phase uses the evaluator.
- **Cost of reversal**: LOW.

### D-2: The equity high is the last mark whose equity reached its peak

- **Decision**: scan the marks oldest first; with no such mark the first mark stands in.
- **Alternatives**: persist a separate equity-high row (a migration for a view).
- **Rationale**: the marks already carry equity and peak per bar.
- **Cost of reversal**: LOW.

## Requirements

### R-1: The readings are right at their thresholds

- **A-1.1**: `tests/unit/application/risk_inputs/test_readings.py` passes.

### R-2: The command shows every input, labelled, read-only

- **A-2.1**: `tests/unit/entrypoints/test_risk_inputs.py` passes.

## Design

See the module docstrings of `readings.py` and `entrypoints/risk_inputs.py`.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

## Risks

- The scan reads every mark of the strategy; at 24 marks a day that is a few
  thousand rows a year, well within a CLI read.
