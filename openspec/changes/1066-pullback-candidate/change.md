# Change: 1066-pullback-candidate

**SDD change**: `1066-pullback-candidate`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1066-pullback-candidate/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Candidate C4 of the first research batch (ADR-026, batch swing-b1):
`swing-b1-pullback` buys a 4h RSI pullback while the daily trend is up. It is a
new entry and exit, so it ships as a new adapter with an RSI engine,
`signal: rsi_pullback` and a disabled `config/strategies.yaml` entry, so the
batch can bind it by `code_paths` before signing. No backtest is run here.

## Scope

### In

- `src/hermes/domain/strategy/swing/engines/rsi.py`: `RsiEngine` (Wilder).
- `src/hermes/domain/strategy/swing/pullback.py`: `PullbackAdapter` and
  `PullbackEntryCheck`.
- The `rsi_pullback` kind registered with the strategy factory (issue #1074).
- A disabled `swing-b1-pullback` entry in `config/strategies.yaml`
  (`exit_pricing: bar_close`, v2's sizing block).
- Unit tests and Hypothesis properties on synthetic bars.

### Out

- Any backtest run, trial line or batch document change.
- Serve's stop journal (`last_stop_record`) and entry-check fields: serve reads
  them from `SwingStrategyAdapter` only, and the candidate is disabled. A
  shadow of this candidate needs them first.

## Decisions

### D-1: RSI over a bounded window

- **Decision**: Wilder RSI over the last `2 × period + 1` bars, the convention
  of `AtrEngine`. A window with no losses is 100, no gains 0, neither 50.
- **Alternatives**: smooth over the whole 4h buffer (rejected: the value then
  depends on how many bars were seeded, so serve after a restart and the
  backtest disagree).
- **Rationale**: 29 bars fit inside the 45-bar 4h warm-up, so every decision
  sees a full window.
- **Cost of reversal**: LOW.

### D-2: "Its value 20 daily closes earlier" reads the same EMA series

- **Decision**: as in change `1065-trend-slope-candidate` D-2:
  `ema_value(closed[:-20])` over the same daily buffer, one shared seed.
- **Cost of reversal**: LOW.

### D-3: Stop from the signal close, fixed, restored from the entry bar

- **Decision**: the stop is the signal bar's close minus `stop_atr_mult` ×
  ATR(22) on the signal bar. On a restart it is recomputed from the entry bar
  when the 4h buffer still holds it (300 bars outlast the 30-day longest time
  stop), else taken from `OpenPosition.last_stop`.
- **Alternatives**: the fill price (rejected: the adapter never sees it, and
  sizing reads the signal's entry and stop); recompute from today's bars
  (rejected: the card fixes the stop for the trade).
- **Cost of reversal**: LOW.

### D-4: Exit precedence on one bar

- **Decision**: RSI exit, then stop, then time stop, then regime break, the
  card's order. Every exit fills at the bar close (`bar_close`), so the order
  only decides the reason recorded. The entry bar is never tested for an exit.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Entry

Long on a 4h close where the previous RSI(14) is below `rsi_reentry` and the
current one is at or above it, while the last daily close is above a daily
EMA(200) that is above its value 20 closes earlier.

### R-2: Exits

First of: RSI(14) above 70; a 4h low at or below the fixed stop; the first 4h
bar closing at or after `time_stop_days` days from the entry; a daily close
below the daily EMA(200).

### R-3: Parameters match the batch

Free parameters `rsi_reentry` (35), `stop_atr_mult` (2.0),
`time_stop_days` (20); the fixed ones are the card's.

## Design

`PullbackAdapter` mirrors `SwingStrategyAdapter`'s buffers (caps 300/300),
interval dispatch, closed-daily rule, `seed` and `restore_position`.
`PullbackEntryCheck(trend, reentry)` records the entry evaluation as booleans.

## Test plan

Engine: hand-computed value, edge values, short input; properties for range
and window. Adapter: entry and stop value, trend block, no cross, each exit,
entry bar not tested, restart with and without the entry bar, seed, invalid
parameters, defaults; properties for alternation, no lookahead, and no entry in
a falling daily trend.

## Risks

- A different reading of the open points (D-1, D-3, D-4): raised with the
  Backtesting thread before signing; any change lands before the batch binds
  `code_commit`.

## Dependencies on other changes

- Issue #1074 (strategy factory) for the `signal:` key and registration.
