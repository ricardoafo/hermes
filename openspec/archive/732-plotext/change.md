# Change: 732-plotext

**SDD change**: `732-plotext`
**Date**: 2026-10-05
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/732-plotext/change`.

## Summary

Add `textual-plotext` (pinned `>=1.0.1,<2`) so console screens can draw
price and portfolio charts, plus one reusable `PriceChart` widget that
plots a series of Decimal closes. The Code Owner approved the library on
2026-10-05 in the console design's "needs your OK" list. The widget is not
wired into any screen; issues #712 and #715 do that.

## Scope

### In

- `textual-plotext` in `pyproject.toml` and `uv.lock` (brings `plotext` 5.3.2).
- `src/hermes/entrypoints/tui_charts.py` with `PriceChart`, and its unit test.

### Out

- Wiring into screens (issues #712, #715).
- Snapshot of a real screen chart (lands with those screens).

## Decisions

### D-1: Pin as `>=1.0.1,<2`

- **Decision**: Same range style as every other direct dependency.
- **Alternatives**: exact `==1.0.1`, rejected because the repo uses ranges and the lockfile already fixes the version and hashes.
- **Rationale**: ADR-010 (lockfile is the reproducibility pin).
- **Cost of reversal**: LOW.

### D-2: Decimal stays Decimal; float only at the drawing edge

- **Decision**: `PriceChart` stores `Decimal` closes and converts to float only to call plotext.
- **Alternatives**: accept floats, rejected because money stays Decimal end to end.
- **Rationale**: display-only values, no arithmetic on the floats.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Dependency passes the supply-chain checks

- **A-1.1**: `python scripts/dev/reproducibility-check.py` exits 0.
- **A-1.2**: `textual-plotext` and `plotext` are MIT, on the licence allowlist; no `known_licenses.yaml` entry is needed.

### R-2: PriceChart plots Decimal closes

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_tui_charts.py` passes.

## Design

`PriceChart(PlotextPlot)` with `closes` (tuple of Decimal) and
`set_closes(Sequence[Decimal])`, which clears, plots and refreshes.

## Test plan

1. `uv run pytest -q` and `bash scripts/dev/pr-preflight.sh` green.

Test tiers touched: unit

## Risks

- **Risk 1**: `plotext` is a small single-maintainer library. Mitigation: pinned and hash-locked; charts are display-only and replaceable.
- **Risk 2**: Re-locking dropped four unrelated `greenlet` riscv64/s390x wheel entries that upstream no longer lists. No effect on supported platforms.

## Dependencies on other changes

- Issues #712 and #715 consume `PriceChart`.
