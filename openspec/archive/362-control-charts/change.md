# Change: 362-control-charts

**SDD change**: `362-control-charts`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#362)

## Summary

Adds statistical process control on execution quality (ADR-005 Category B,
ADR-012 supervision loop). An EWMA chart plus a two-sided CUSUM watches each
of fee (bps), slippage (bps) and order latency (ms). The first observation of
each out-of-control episode becomes an `AnomalyDetected` WARNING, routed by
the existing `ANOMALY_DETECTED` class. Inputs carry no P&L. Nothing pauses
automatically, and no strategy, sizing or kill threshold changes.

## Scope

### In

- `domain/monitoring/control_chart.py`: `ControlChart`, `ChartParameters`.
- `application/monitoring/execution_quality.py`: `ExecutionQualityMonitor`.

### Out

- Feeding it from execution logs inside `hermes serve`: after #30 and #353.

## Decisions

### D-1: Textbook SPC parameters, not pre-registered numbers

- **Decision**: EWMA λ = 0.2, L = 3; CUSUM k = 0.5σ, h = 5σ; a Phase I
  baseline of 30 observations. In-control average run lengths are about 500
  and 465 (Montgomery, ch. 9).
- **Rationale**: the pre-registration sets no control-chart numbers, and these
  monitor execution costs, not the strategy, so no pre-registered value is
  reinterpreted. They live in `ChartParameters` and can change without a new
  experiment.
- **Cost of reversal**: LOW.

### D-2: Alert only, one alert per episode

- **Decision**: severity WARNING, no auto-pause. An episode starts when either
  statistic leaves its limits and ends when both are back inside. CUSUM sums
  are not reset on a signal, so a persistent shift stays one episode.
- **Rationale**: an automatic pause on a new signal during v1 would add a pause
  rule to the pre-registered protocol (§10, §11).
- **Cost of reversal**: LOW.

### D-3: Zero-variance baselines

- **Decision**: a flat baseline (for example a fixed fee rate) gets a tiny
  sigma floor, so any later change still signals.
- **Cost of reversal**: LOW.

## Requirements

### R-1: One alert per breach episode, no P&L

- **A-1.1**: `uv run pytest tests/unit/domain/monitoring tests/unit/application/monitoring`

## Design

See the module docstrings.

## Test plan

1. Baseline points never alert; in-control data never alerts.
2. A step shift alerts exactly once; a later second excursion alerts again.
3. A one-sigma drift is caught by CUSUM within 20 points.
4. Each metric alerts independently; the reason names the metric and unit and
   contains no P&L words.
5. `bash scripts/dev/pr-preflight.sh` passes.

## Risks

- **Simulated fees and latency are near-constant in paper mode**, so charts may
  be quiet until live trading. That is expected; the charts become meaningful
  as real execution data accrues.
- **A noisy metric pages often**: one alert per episode bounds it; parameters
  can be tuned in code without touching the protocol.
