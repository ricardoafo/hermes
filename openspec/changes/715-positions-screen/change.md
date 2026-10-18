# Change: 715-positions-screen

**SDD change**: `715-positions-screen`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/715-positions-screen/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Tab 3 of the Cockpit console, Positions (design spec screen 3): the open
positions and the recently closed ones as one filterable list, and the selected
position's detail beside it (a detail screen under `◂ Positions › ETH` on
terminals under 100 columns). The detail is the one Home already draws (big
net, why it opened, the stop and what would close it, the money if sold now,
the price path and the timeline), so the two never disagree. The recently
closed rows come from the activity read the app already makes, so there is no
new port or read. The main pick is reuse; the main risk is money on a surface
that hides it, so every money cell comes from the render context's
`shows_money`.

## Scope

### In

- `src/hermes/entrypoints/tui_positions.py` (new): `PositionsView`,
  `position_rows`, `closed_items`, `closed_detail`.
- `src/hermes/entrypoints/tui.py`: tab 3 composes the view; each read hands it
  the positions and the closed rows; switching to the tab focuses the list.
- Snapshots in both themes at four sizes; the Positions caption in the console
  catalog.

### Out

- A live price line beside the closed rows and any write: the screen is read
  only.
- A closed position's own price chart and waterfall (they need a trade history
  read; the Tax screen's lots carry the sales).

## Decisions

### D-1: closed rows come from the activity

- **Decision**: `closed_items` takes the `closed` activity rows that name a coin (newest first, at most 20).
- **Alternatives**: a new trade-history read in each read model.
- **Rationale**: the activity already carries the coin, time, exit price and net of each close; a new read would touch three adapters for the same facts.
- **Cost of reversal**: LOW.

### D-2: the open detail is Home's detail

- **Decision**: the view asks the app for the detail body of a coin (`_detail_content`), so the live line and the unavailable states match Home.
- **Alternatives**: a second renderer.
- **Rationale**: one renderer, one place to fix.
- **Cost of reversal**: LOW.

### D-3: the cursor follows the position, not the row number

- **Decision**: a refresh puts the cursor back on the same key.
- **Alternatives**: keep the row index.
- **Rationale**: a position closing or a new close above the cursor would otherwise move the detail to another position between two reads.
- **Cost of reversal**: LOW.

## Requirements

### R-1: list

Open positions first, then the recently closed ones; columns coin, timeframe, state, when, change, net, stop room; filter by `coin:`, `state:`, `side:` and `since:`.

### R-2: detail

With room (100 columns and up) the selected position's detail shows beside the list and follows the cursor; under 100 columns Enter opens it as a screen and Esc returns with the cursor on the position shown last. A closed position's detail shows when it closed and, with money, its exit price and net.

### R-3: money

On a surface that hides money no row or detail shows a net, a price or a EUR figure.

### R-4: read only

No key writes anything.

## Design

`position_rows` turns the positions and the closed activity rows into rows for the shared `QueryList`; `PositionsView` holds the list panel and the detail panel and re-reads nothing itself: the app hands it each read through `receive`.

## Test plan

- `tests/unit/entrypoints/test_tui_positions.py`: rows and order, money hidden (including a property test over closed nets), closed detail, the screen lists the fixture's positions, the detail follows the cursor, the compact detail screen and Esc, the cursor kept over a refresh, a failed read, SVG snapshots.

## Risks

- The closed rows are only as old as the activity window; an older close is on the Tax screen's lots.

## Dependencies on other changes

- None; the Lab tab (issue #1056) and the Logs tab (issue #716) renumber keys 5 to 9 only.
