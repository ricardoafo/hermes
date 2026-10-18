# Change: console-signals-screen

**SDD change**: `console-signals-screen`
**Date**: 2026-10-06
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/console-signals-screen/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Tab 2 of the Cockpit console becomes the Signals screen: a heat grid of the
week's 4h bar checks per coin, coloured by how many of the three frozen entry
rules held, the last check's rules as yes or no, the week's counts and the
frozen rules in words. The data is the per-rule yes/no that `hermes serve`
already writes on every entry check's `serve.bar.processed` journal line, so
nothing new is recorded. The main pick is a new read-model view,
`ConsoleReadModel.rule_checks()`, whose rows hold booleans only, so the
screen cannot show a price or a distance to a rule (ADR-012 D-06). The main
risk is the journal: where it cannot be read, the screen says "not available
yet" instead of drawing an empty week.

## Scope

### In

- `RuleCheck` view (at, coin, interval, breakout, momentum, regime) and
  `RULE_COUNT`, in `hermes.application.console.views`.
- `journal_activity.rule_checks(lines, since=...)`: parse the entry fields of
  `serve.bar.processed` lines, one check per coin, interval and candle.
- `ConsoleReadModel.rule_checks()`; the ledger read model reads the activity
  window of the journal; the fixture read model builds a fixed pattern whose
  only signal is the bar a held 4h coin opened on.
- `hermes.entrypoints.tui_signals.signals_screen` and the tab 2 hook in
  `tui.py`.
- Pilot, layout, property and snapshot tests (both themes, four breakpoints).

### Out

- "Blocked by" per check (max positions, exposure cap, correlation lock, stale
  book): serve does not log which gate stopped an entry. Follow-up issue.
- Week counts for opened, blocked and data late; the engine brief for a bar;
  Enter opening a bar (shared drill-down, issue #730); D W M Y range.
- 1d checks: the grid is one square per 4-hour bar, as the design spec draws.

## Decisions

### D-1: Read the rules from the journal, not the ledger

- **Decision**: Parse the `entry_breakout`, `entry_momentum` and
  `entry_regime` fields serve already logs on each entry check.
- **Alternatives**: a new ledger table (schema change and migration for data
  serve already writes); recomputing the indicators in the console (needs
  prices, which ADR-012 keeps away from the supervisor-facing screens).
- **Rationale**: the fields exist since PR #659 and the activity feed already
  counts them (#653); the journal source is already wired into the ledger
  read model.
- **Cost of reversal**: LOW. Only `LedgerReadModel.rule_checks` changes.

### D-2: Booleans only in the view

- **Decision**: `RuleCheck` has no numeric field.
- **Alternatives**: carry indicator values and hide them in the renderer.
- **Rationale**: ADR-012 D-06 and the design spec's rule that signals show as
  yes or no, never how close a price came. A test pins the field types.
- **Cost of reversal**: MEDIUM. Would need an ADR.

### D-3: Unavailable, not empty

- **Decision**: no readable journal raises `ViewNotAvailableError`.
- **Rationale**: an empty grid would read as "nothing was checked".
- **Cost of reversal**: LOW.

## Requirements

### R-1: Rules per check

- **A-1.1**: `uv run pytest tests/unit/application/console/test_rule_checks.py`
- **A-1.2**: `uv run pytest tests/unit/adapters/console/test_rule_checks_read_models.py`

### R-2: The screen

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_tui_signals.py`
  (Pilot on tab 2, yes/no rows, holding rows, counts, layouts, no scenario
  price on screen, 8 SVG snapshots).

## Design

```text
serve.bar.processed (journal) --rule_checks()--> RuleCheck rows
  LedgerReadModel.rule_checks()   (activity window, journal cache)
  FixtureReadModel.rule_checks()  (fixed synthetic pattern)
        |
        v
tui.HomeApp._redraw (tab 2) -> tui_signals.signals_screen(checks, ctx, tokens,
                                                          width=..., held=...)
```

Screen: "Bar checks · last 7 days" grid (■ coloured by rules met, ◆ for a
signal, a gap between local days), legend, "Last check · HH:MM bar" table,
then "This week" (checks, signals, share of checks each rule held) and "The
rules · frozen". From 120 columns the last two sit beside the checks.

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest tests/unit`, `uv run mypy src`, `uv run ruff check`,
   `uv run lint-imports` are clean.
