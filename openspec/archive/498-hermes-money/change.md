# Change: 498-hermes-money

**SDD change**: `498-hermes-money`
**Date**: 2026-10-04
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/498-hermes-money/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes money` prints the console's portfolio section on the command line
(build plan step 5): total value in EUR with the change for `--range`, the
Today / This year waterfall, the open line and the tax footnote, through the
shared `render_portfolio`. It is one new command module and a JSON
serialiser; no new layer, port or adapter.

## Scope

### In

- `entrypoints/money.py` (`main`, `portfolio_to_json`) and
  `entrypoints/commands/money.py` (`wants_demo=True`).
- `money` joins `CONSOLE_COMMANDS`, so `hermes --demo SCENARIO money` works.

### Out

- Live money: the ledger-backed read model (#387, #353). Until then the
  command prints the empty state, like `hermes positions`.

## Decisions

### D-1: empty state, not an error

Without a demo there is no money to show yet; exit 0 with one sentence, as
`hermes positions` does (#437 D-1).

### D-2: money under `pnl` in JSON

As in `positions --json`: a blind surface omits `pnl` entirely. Pending tax is
`null`, never zero.

## Requirements

### R-1: section per scenario matches the reviewed snapshot at 80 and 120 columns

### R-2: `--range today|week|month|year`; anything else exits 2

### R-3: JSON money only where `pnl_visible`

### R-4: empty state without a demo, exit 0

## Design

`main` parses arguments, resolves the read model, renders
`render_portfolio` plus `render_tax_footnote`, or serialises with
`portfolio_to_json`.

## Test plan

`tests/unit/entrypoints/test_money.py`: syrupy snapshots per scenario and
width, range and usage errors, JSON keys, blind surface, pending tax, empty
state, CLI `--demo` path.

## Risks

None beyond the shared `CONSOLE_COMMANDS` line, which other console PRs also
touch.

## Dependencies on other changes

- #384 (read model, renderers), #460 (command registry).
