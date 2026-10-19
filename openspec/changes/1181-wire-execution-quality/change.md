# Change: 1181-wire-execution-quality

**SDD change**: `1181-wire-execution-quality`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#1181)

Mirror of engram topic key `sdd/1181-wire-execution-quality/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes serve` now feeds every executed shadow fill into the execution-quality
monitor of change `362-control-charts` (EWMA + CUSUM on fee bps, slippage bps,
latency ms; ADR-005 Category B item 3). The first fill of a breach episode is a
`WARNING` `AnomalyDetected` recorded in the ledger and alerted on the alert
channels. Alert only, as #362 D-2 decided: nothing pauses, and no decision,
size, kill rule or drift gate reads the charts. The monitor is primed at
start-up from the ledger's past fills, so a restart keeps its baseline and
does not alert an episode again. The main risk is noise: a flat metric (fixed
fee rate) alerts on any change.

## Scope

### In

- `application/monitoring/execution_quality.py`: `observation_of(quality, fee)`,
  `ExecutionQualityMonitor.prime(...)`, and `None` for a metric a fill did not
  measure.
- `entrypoints/serve.py`: the monitor on `Composition.execution_quality`,
  primed before warm-up; each executed fill observed after it is recorded; each
  breach published on the bus (the ledger records it) and alerted.

### Out

- Any pause, skipped entry, sizing change, kill-rule or drift-gate input (#362
  D-2). Changing that is a pre-registration change.
- Chart parameters from config: the textbook defaults of #362 D-1 stay in code.
- Removing `hermes.application.monitoring.execution_quality`,
  `hermes.domain.monitoring` and `hermes.domain.monitoring.control_chart` from
  the production-reachability parked list: done after that list lands.

## Decisions

### D-1: An alert is not a use of the #30 fields in metrics, rules or gates

- **Decision**: read `delta_ms` (and `slippage`) from `FillQualityRecorded` to
  chart latency and slippage.
- **Alternatives**: measure latency again in serve (a second, different number).
- **Rationale**: change `30-execution-logging` keeps the fields out of
  "metrics, risk rules, kill rules or the drift gate". A WARNING anomaly that a
  person reads changes none of those: nothing it emits is read back to trade.
- **Cost of reversal**: LOW.

### D-2: Observation from the quality row plus the fill's fee

- **Decision**: fee bps = fee / (fill price × quantity) × 10 000 when the fee is
  in the quote currency; slippage bps = `slippage` × 10 000; latency ms =
  `delta_ms`. The same function serves the live fill and the start-up replay.
- **Rationale**: one formula, so a replayed fill charts exactly as it did live.
- **Cost of reversal**: LOW.

### D-3: A metric a fill did not measure is skipped, not zero

- **Decision**: `None` latency (clock before the bar close, #30 D-3), `None`
  slippage (one-sided book) or a fee in another currency leaves that chart
  untouched; the other charts still take the fill.
- **Alternatives**: skip the whole fill (loses good fee and slippage points);
  read it as 0 (a false breach on a flat latency chart).
- **Cost of reversal**: LOW.

### D-4: Prime by replaying the ledger, in ledger order

- **Decision**: before warm-up, replay every quality row of the strategy that
  has its trade (`fill_quality_events` joined to `trade_events` by correlation
  id), oldest first, discarding events.
- **Rationale**: the replay leaves the charts exactly where a process that never
  stopped would be: same baseline, same open episode. No new ledger state.
- **Cost of reversal**: LOW.

### D-5: Monitor errors never stop a fill; breach writes fail like any write

- **Decision**: an exception from priming or observing is logged as
  `serve.execution_quality.failed` and serve goes on. Publishing a breach is
  outside that guard: a failed ledger write raises as every ledger write does. A
  failed channel is logged as `serve.execution_quality.alert_failed`.
- **Cost of reversal**: LOW.

## Requirements

### R-1: One recorded and alerted WARNING per breach episode, decisions unchanged

- **A-1.1**: `uv run pytest tests/integration/entrypoints/test_serve_execution_quality.py`
- **A-1.2**: `uv run pytest tests/unit/application/monitoring tests/unit/domain/monitoring`

## Design

- `_BarLoop.prime_execution_quality()` runs in `_bar_loop` before
  `warm_up_and_catch_up`.
- `_BarLoop._execute` (called only from `_fill`) ends with
  `_watch_execution_quality(result)`: on `Executed` with a quality row it
  observes, then per event publishes on `c.event_bus`, logs
  `serve.execution_quality.breach` (source and severity only, no figures) and
  calls `_alert(..., failed="serve.execution_quality.alert_failed")`.

## Test plan

1. A slippage step after a four-fill baseline records one WARNING with the
   hand-computed reason and alerts it once; a continuing episode adds nothing; a
   stable run records nothing.
2. A restart keeps the baseline and does not alert an episode already alerted.
3. A fill without latency still feeds slippage and does not alert latency.
4. A monitor or replay error is logged and the fills are recorded.
5. Paired run: signals, trades and halts are equal with a breaching chart and a
   chart that never breaches.
6. Property: priming then observing equals observing everything live.

## Risks

- **Flat metrics page on any change** (#362 D-3): the paper fee rate and the
  scripted latency are constant, so the first real change alerts once per
  episode. Accepted: one alert per episode bounds it.
- **Start-up reads every fill row twice** (shadow executor and the replay):
  small for the 90-day window.
