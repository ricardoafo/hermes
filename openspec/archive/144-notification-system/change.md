# Change: 144-notification-system

**SDD change**: `144-notification-system`
**Date**: 2026-10-04
**Status**: Shipped
`src/hermes/application/messages/shapes.py`; swapping a shape is a one-line edit there plus a
snapshot update. The recommended shapes ship until the Code Owner picks otherwise.
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/144-notification-system/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Redesigns hermes's notifications into one style shared by Telegram, Slack and
the shell alert scripts: EUR and Madrid time, fixed number formats, kind and
coin on the first line, loud only when the operator is needed, blind until
certification. It also fills the gaps the inventory found: templates missing
for some events, routes missing for others, and no silent sends. This change is
staged. Phase 1, the research, the inventory and the shape proposal, lands
first as docs. Phase 2 implements the shapes the Code Owner picks in
`docs/decisions/2026-10-04-notification-templates-v2.md`.

## Scope

### In (phase 1, this PR)

- `docs/research/2026-10-04-notification-inventory.md`.
- `docs/research/2026-10-04-notification-ux-research.md`.
- `docs/decisions/2026-10-04-notification-templates-v2.md` (Proposed).

### In (phase 2, next PR, after the pick)

- `application/messages/format.py`: thin wrappers over the console format
  helpers, so every template shares one rule for EUR, percentages, durations
  and Madrid time.
- Telegram and Slack templates rewritten for every event in the inventory.
  Templates are added for `StrategyResumed` on Telegram, and for `LotClosed`
  and `BarProcessed` where they are routed. `SignalEmitted` gains the stop.
- Routing for `RebalanceTriggered`; a silent or loud flag on every route,
  passed to `disable_notification`.
- `hermes preview` covering every event on both channels, including
  `signal-emitted`.
- A migration test that old event payloads still render.
- Shell alert scripts aligned with the health shape if the Code Owner says so
  (Open question 3 in the proposal).

### Out

- Multi-recipient channels, throttling or coalescing, other channels, and a
  preferences UI (#144's out-of-scope list).

## Decisions

### D-1: stage the work behind the operator's pick

The shapes are a taste decision with real tradeoffs, so they are proposed with
mock-ups first. Building all three shapes in code would triple the template
work for options that get dropped.

### D-2: one formatter layer

The console already has the EUR, percentage and Madrid-time helpers that the
operator approved. Templates reuse them instead of formatting numbers again.

### D-3: severity glyphs are plain text

`▲ ▼ ■ ◐ ●` read the same in Telegram, in Slack and in a bash script, so the
three paths can share one look without emoji or channel-specific colour.

## Requirements

### R-1: every event in the inventory has a template on each channel it routes to

### R-2: money, prices, times and durations use the shared formatters

### R-3: loud only for down, P0 and rejected orders; everything else is silent

### R-4: pushed messages carry no P&L before certification (#319)

### R-5: `hermes preview <event>` renders both channels for every event

### R-6: old payloads still render (migration test)

### R-7: 100% line coverage on the template modules

## Design

Phase 2 keeps the current registry dispatch in `telegram.py` and `slack.py`.
Each `_render` function builds its lines from the shared formatters in the
picked shape, and the router's route table gains a `silent` flag that the
adapters pass through.

## Test plan

- Phase 1: markdownlint on the three docs.
- Phase 2: syrupy snapshots per event and channel; property tests on the
  formatters (separators and fixed precision for any Decimal); a test that
  scans every rendered message for P&L words; the migration test; route-table
  tests for the silent and loud flags.

## Risks

- Rewriting every template at once touches the most operator-facing code; the
  snapshot per event and channel makes every wording change reviewable.
- Unchanged wording in the shell scripts would keep two dialects alive; the
  proposal asks the Code Owner to decide when they move.

## Dependencies on other changes

- #384 (console format helpers), #319 (blind notifications), ADR-015 (silent and
  loud policy), #379 / PR #495 (bot heartbeat alerts).
