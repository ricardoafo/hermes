# Change: 371-log-processed-bars

**SDD change**: `371-log-processed-bars`
**Date**: 2026-10-03
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/371-log-processed-bars/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes serve` writes one INFO journal line per handled bar
(`serve.bar.processed`) and one line when the loop goes live
(`serve.bar.next_due`) with each stream's next expected delivery. Today a bar
without a signal only reaches the ledger, so the journal cannot tell a quiet
market from a stalled loop. Logging only: no ledger, notification or strategy
change. The main risk is journal volume: three lines every 4h plus three at
the daily close for the current BTC/ETH/SOL universe.

## Scope

### In

- `serve.bar.processed` in `_BarLoop.handle`, after the `BarProcessed`
  publish and cursor move.
- `serve.bar.next_due` once, between catch-up and the live stream.
- `_BarLoop.next_due(stream)`, reused by both lines.

### Out

- `hermes status` (#370) and the healthchecks progress ping (#372), which
  read the same facts from the ledger.
- Any change to what is written to the ledger or sent to Telegram/Slack.

## Decisions

### D-1: due time = last open + 2 intervals + settle

The next bar opens one interval after the last handled bar and closes one
interval later; the feed delivers it `settle_delay_seconds` after that (the
closed-bar rule of the live klines adapter). Computing from the stored open
time keeps it exact for ledger-restored cursors too, whose `previous` entry is
already the last handled bar's open time.

### D-2: log after the cursor moves

The line is emitted only once the ledger row exists, so a `serve.bar.processed`
line always means the bar is durably recorded. A skipped duplicate logs nothing
beyond the existing DEBUG `serve.bar.duplicate_skipped`.

## Requirements

### R-1: one line per handled bar

Every bar that passes the contiguity check logs `serve.bar.processed` with
`pair`, `interval`, `open_time`, `close_time`, `signal` (`none`, `entry` or
`exit`) and `next_due`, all as strings.

### R-2: next due per stream when going live

After warm-up and catch-up, one `serve.bar.next_due` line maps
`"<pair> <interval>"` to the next due time of every stream with a handled or
seeded bar.

### R-3: no behavior change

Ledger rows and notifications are unchanged.

## Design

`_BarLoop.handle` logs after `self.previous[stream] = bar.open_time`;
`signal` comes from the strategy's return value (a duplicate signal still
names its kind). `_bar_loop` calls `loop.log_next_due()` after
`warm_up_and_catch_up` returns without shutdown.

## Test plan

`tests/unit/entrypoints/test_serve.py::TestBarLogging`:

- two bars log two lines with exact fields (R-1);
- an entry bar logs `signal="entry"`, the next `none` (R-1);
- a repeated bar logs one line (D-2);
- `log_next_due` after catch-up lists both streams with exact times (R-2).

Existing `TestBarLoop` and serve integration tests cover R-3.

## Risks

- Journal volume: 3 pairs × (6 + 1) bars a day, negligible.
- A wrong `next_due` would mislead the operator, not the loop; the exact-value
  tests pin it.

## Dependencies on other changes

None. #370 and #372 consume the same facts from the ledger.
