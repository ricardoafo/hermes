# Change: 1064-daily-ma

**SDD change**: `1064-daily-ma`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#1064)

Mirror of engram topic key `sdd/1064-daily-ma/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Research batch swing-b1 (ADR-026) names four candidates. This change builds C2,
`swing-b1-daily-ma`, to the batch's §C2 words: long after a daily close crosses
above its SMA, decided on the 4h bar that closes at 00:00 UTC, out on a daily
close below the SMA or on a ratcheting protective stop. It adds the signal kind
`daily_ma`, its domain module and a disabled roster entry. No backtest runs
here; the batch's Train run does that once the batch is signed and merged. Main
risk: code that trades a rule other than the signed one. Each rule and
parameter is pinned by a hand-computed test.

## Scope

### In

- `src/hermes/domain/strategy/swing/daily_ma.py`: `DailyMaAdapter`.
- `daily_ma` registered in
  `src/hermes/application/strategy_protocol/strategy_factory.py`.
- `config/strategies.yaml`: `swing-b1-daily-ma`, disabled, with v2's capital,
  sizing and close-priced exits (batch Common).

The batch's `code_paths` for C2 are the module, the factory and
`config/strategies.yaml`.

### Out

- The batch document and the card (issue #1062, PR #1067).
- Any backtest run, metric or trial line.

## Decisions

- **D-1 A standalone adapter.** C2 shares no rule with v2 (no Donchian, no
  EMA regime, no chandelier, no time stop), so `DailyMaAdapter` implements
  `StrategyPort` directly rather than subclassing `SwingStrategyAdapter`.
- **D-2 Parameter names and bases from the batch.** `sma_period` (int, base
  100) and `stop_atr_mult` (Decimal, base 3.0) are keyword-only, so
  `default_parameters()` returns exactly the batch's base values.
- **D-3 The decision bar.** Entries and the moving-average exit are taken only
  on a 4h bar whose `close_time` is 00:00 UTC and only when the daily bar with
  the same `close_time` is buffered. A missing daily bar means no decision that
  day, never a decision from an older close.
- **D-4 The stop in force.** The 4h bar is tested against the stop as it stood
  when the bar opened: daily closes up to the bar's `open_time` raise it first,
  and the daily close completing at the bar's `close_time` raises it only after
  the bar is tested. So the midnight bar's own low is never measured against a
  level computed from its own close.
- **D-5 Both exits on one bar.** If the midnight bar's low touches the stop and
  its daily close is below the SMA, the reason recorded is `protective_stop`:
  the stop is touched inside the bar, before the close that breaks the SMA.
  With close-priced exits both fill at the same price.
- **D-6 The re-entry right.** An exit sets it; a decision bar whose close is at
  or below the SMA, or an entry, clears it. Any close above the SMA on a
  decision bar re-enters while it is set.
- **D-7 Restore.** A restored position's stop is the higher of its
  `last_stop` and its entry price minus the multiple times the daily ATR(22)
  at the entry, raised through every daily close since the entry before the
  next bar is tested. Each level is recomputed from the same daily bars, so a
  retried exit and a restart hold the stop the position had, and a position
  the adapter did not enter takes its own stop.
- **D-8 Warm-up.** The daily buffer holds 300 bars, more than the longest SMA
  neighbour (150) plus one, and ATR(22) needs 23.

## Requirements

- R-1 Entry only on the 00:00 UTC 4h bar, when the daily close just completed is
  above SMA(`sma_period`) and the previous close was at or below its SMA, or the
  re-entry right is set.
- R-2 A close equal to the SMA neither enters nor exits.
- R-3 Exit on a 4h low at or below the stop in force when the bar opened, or on
  a midnight bar whose daily close is below the SMA.
- R-4 The stop starts at the signal bar's close minus `stop_atr_mult` x daily
  ATR(22) and is raised at each daily close, never lowered.
- R-5 No daily bar closing after the 4h bar's close is read.
- R-6 The kind's defaults are the batch's base values, and every neighbour builds.
- R-7 The roster entry is disabled and names `signal: daily_ma`.

## Design

`on_bar` buffers daily bars and evaluates 4h bars: the exit path when in
position, the entry path when flat. `_daily_decision` returns, for the midnight
bar only, whether the close is above and below its SMA and whether the previous
close was at or below its own. `_raise_stop(through=t)` walks daily bars closed
in `(raised_through, t]`, computes each one's level from the ATR of the bars up
to it, and keeps the maximum.

## Test plan

- Unit, `tests/unit/domain/strategy/swing/test_daily_ma.py`: synthetic daily
  bars whose true range is always 2, so the ATR(22) is 2 and every SMA, stop and
  level is worked by hand. Covers the fresh cross and the close equal to the SMA
  (R-1, R-2), the stop at and just above the low and the MA break (R-3), the
  ratchet, the midnight-bar ordering and a wide day that must not lower the stop
  (R-4), a daily bar delivered early (R-5), re-entry and its reset, restore, the
  defaults, neighbours and refusals (R-6) and the roster entry (R-7).
- Property: over random daily paths with intraday dips, signals alternate
  entry, exit, entry, and each exit's stop is at or above its entry's.

## Risks

- A data gap on the daily feed skips that day's decision (D-3); the backtest
  data is complete, and serve does not run this kind while it is disabled.
