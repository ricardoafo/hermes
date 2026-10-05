# Change: 656-entry-check-log

**SDD change**: `656-entry-check-log`
**Date**: 2026-10-04
**Status**: Proposed
**Tracking issue**: see sibling `issue.md`

## Summary

Every 4h check in `hermes serve` now logs which of the three
pre-registered entry conditions held (breakout, momentum, regime) as
yes/no fields on its `serve.bar.processed` journal line. The daily
review and the supervisor briefings (issue #365) can then explain
"why no signal" without an AI reading prices or indicator values from
the OOS window (ADR-012 D-02, D-06). Record-only: the strategy decides
exactly what it decided before. The main risk is changing a signal
decision by accident, so a property test compares the new evaluation
with the previous first-miss rule on random bar paths.

## Scope

### In

- `EntryCheck` (three booleans) and `SwingStrategyAdapter.last_entry_check`.
- `serve.bar.processed` gains `entry_breakout`, `entry_momentum` and
  `entry_regime` (`yes`/`no`) on 4h bars evaluated for an entry.
- One sentence in `docs/guides/operator-usage.md`.

### Out

- vps-diagnose printing 24h of `serve.bar.processed` lines (a workflow
  change; it follows the CI line).
- Console display of the fields (after issue #650).
- Exit conditions: an exit fires on its own rule and names it in the
  signal's `exit_reason`, so no per-bar exit record is added.

## Decisions

### D-1: Journal fields, not a ledger event

- **Decision**: Add fields to the existing `serve.bar.processed` log
  line; no new event type, no schema or migration.
- **Alternatives**: A new ledger event (needs a migration, and a
  rollback to an older release would meet an unknown event type);
  extending the `BarProcessed` payload (changes its hash schema).
- **Rationale**: The shadow clock is running. Ledger changes must be
  record-only and nullable; the journal needs neither, and an older
  release simply stops writing the fields.
- **Cost of reversal**: LOW. Removing the fields touches one call site.

### D-2: Evaluate all three conditions, decide on their AND

- **Decision**: `_entry_check` evaluates all three conditions with the
  same engine calls, and `_entry_conditions_met` returns their AND.
- **Alternatives**: Keep the first-miss evaluation and record the
  unevaluated conditions as unknown. That answers "which held" only
  for the first condition, which is what the Code Owner asked to see.
- **Rationale**: Each condition is a pure read of the bar buffers, so
  evaluating the remaining ones cannot change the decision. Thresholds,
  periods and the entry rule are unchanged (pre-registration v1 §5.1;
  §11 lists engine changes that do not change strategy math as no
  deviation). `swing_parameters()` and the D-8 fingerprint are untouched.
- **Cost of reversal**: LOW.

### D-3: Booleans only

- **Decision**: The record holds yes/no per condition, never the close,
  the channel high, the ADX value or the EMA.
- **Rationale**: ADR-012 D-06 keeps AI inputs outcome-blind and D-02
  keeps OOS data out of AI prompts; booleans explain the rule outcome
  without exposing market values.
- **Cost of reversal**: LOW.

## Requirements

### R-1: The record matches the rule

- **A-1.1**: `uv run pytest tests/unit/domain/strategy/swing/test_entry_check.py`
  passes: on every flat 4h bar the record equals each condition
  computed independently, and its AND equals the previous first-miss rule.

### R-2: The journal line carries the record

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_serve.py -k "entry_check or condition_fields"`
  passes: a 4h entry check logs the three fields as yes/no; a daily
  bar logs none.

## Design

```text
SwingStrategyAdapter.on_bar(bar)
  last_entry_check = None
  4h, flat  -> _evaluate_entry -> _entry_conditions_met
                 check = _entry_check(as_of)   # breakout, momentum, regime
                 last_entry_check = check
                 return check.all_met
  4h, long  -> _evaluate_exit (unchanged; no record)
  1d        -> buffer update (unchanged; no record)

_BarLoop.handle -> _log.info("serve.bar.processed", ..., **_entry_check_fields(strategy))
```

## Test plan

1. Both acceptance criteria pass.
2. `bash scripts/dev/pr-preflight.sh` is green.

## Risks

- A decision change by accident: covered by the property test against
  the first-miss rule. Severity if missed: F-row, signals differ from
  the backtest; issue #358's parity check would also catch it.
- Extra ADX and EMA computations on bars where the breakout already
  failed: a few hundred Decimal operations per 4h bar.
