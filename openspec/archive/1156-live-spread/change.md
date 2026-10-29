# Change: 1156-live-spread

**SDD change**: `1156-live-spread`
**Date**: 2026-10-18
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1156-live-spread/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The Market panel shows the live spread in basis points per coin (the first
slice of the "more live data" issue #722, item 5). The ticker the console
already polls every 10 s returns the best bid and the best ask next to the last
price, so the spread costs no new request and no new port. The main risk is
showing a stale figure as live, so a failed tick shows a dash, never the last
spread.

## Scope

### In

- `application/console/live.py`: `spread_bps`, a spread on `TickOutcome` and
  `LiveQuote`, kept per coin by `LiveBoard`.
- `entrypoints/tui_live.py`: a `spread` column in the Market panel from 64
  cells.
- The live Home snapshots that show the Market panel at 120 columns.

### Out

- Book depth and the "book fresh / stale" flag: they need a book snapshot
  request and the engine's stale threshold; they stay with issue #722.

## Decisions

### D-1: spread from the ticker's best bid and ask

- **Decision**: `spread_bps = (ask - bid) / mid x 10000` from the tick already fetched.
- **Alternatives**: a book snapshot per coin every 10 s.
- **Rationale**: the top of the book is all a spread needs; a depth request triples the calls for no extra figure here.
- **Cost of reversal**: LOW.

### D-2: no spread over a failed tick

- **Decision**: a failed coin shows a dash in the spread column, and a good tick without a spread clears the old one.
- **Alternatives**: keep the last spread dimmed, as the price is.
- **Rationale**: a price is a level that stays true for a while; a spread is a condition of the book now, so an old one would read as live.
- **Cost of reversal**: LOW.

## Requirements

### R-1: spread

From 64 cells each Market row shows the coin's spread in basis points to one decimal.

### R-2: stale

A coin whose last fetch failed, or whose tick gave no spread, shows a dash.

### R-3: display only

The spread lives in the console process; it is never written, never moves a stop or an exit, and never reaches the supervisor, briefs or notifications (the existing import-linter contract on the live module).

## Design

`TickerPoller.fetch` computes the spread from the `Ticker` it already reads and puts it on the `TickOutcome`; `LiveBoard.apply` keeps the newest good one per coin; `market_rows` draws it between the chart and the result.

## Test plan

- `tests/unit/application/console/test_live.py`: the arithmetic, the poller carrying it, the board keeping and clearing it, a property test (positive, unit-free).
- `tests/unit/entrypoints/test_tui_live.py`: the column header and value, hidden under 64 cells, a dash after a failure.
- The live Home snapshots, reviewed in both themes.
