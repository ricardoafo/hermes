# Change: 1117-missed-bar-budget

**SDD change**: `1117-missed-bar-budget`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1117-missed-bar-budget/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-021 §2 Done rule 2 allows a shadow at most 1% of its bars missed. Serve
already writes a `BarMissed` row for every bar it reaches late (issue #1078),
and each shadow keeps its own ledger (issue #1076). This change counts those
rows per ledger by distinct bar, gives the budget as a whole number of bars
fixed up front, adds "N of M bars used, K left" to the restart report, and
sends the lab chat one alert when a shadow crosses it. The main risk is the
denominator chosen (D-1), which Rick may want read another way.

## Scope

### In

- `application/monitoring/missed_bar_budget.py`: the allowance, the budget,
  the report line and the crossing alert text.
- `SqliteLedger.missed_bar_count()` (distinct bars) and
  `SqliteLedger.has_anomaly_from(source)`.
- Serve: the budget line in the restart report, and the crossing alert after a
  catch-up or after a late bar in the live loop.

### Out

- The budget on screen: the list of shadows on the Shadow test tab is issue
  #731 and needs its own design.
- The champion twin compared with prod: issue #1142, after the cutover.
- A gate in `hermes certify` on this rule: the certification checks list
  follows the screen work.

## Decisions

### D-1: The budget is 1% of every bar the shadow's streams close in 90 days

- **Decision**: allowance = floor(1% x bars in 90 days, summed over every pair
  and interval serve evaluates). Today 3 pairs on 4h and 1d: 1890 bars, 18
  allowed.
- **Alternatives**: 1% of the bars elapsed so far (a two-bar outage on day 1
  would cross it and later recover, so the budget could not be read as "what
  is left"); a budget per stream (one outage misses every stream alike, so
  it adds rows without changing the answer).
- **Rationale**: Done is judged at day 90 over the whole run; a fixed number
  lets the report say what is left from day one, and crossing it is final.
- **Cost of reversal**: LOW (one function).

### D-2: Counted from the ledger's start, not from day 0

- **Decision**: every distinct `BarMissed` row in the shadow's ledger counts.
- **Rationale**: ADR-021 §3 starts a counted run on a fresh ledger at day 0,
  so a counted ledger's rows are the run's rows. A warm-up ledger gets the
  same report, which is informational there.
- **Cost of reversal**: LOW.

### D-3: One crossing alert per ledger, recorded in the ledger

- **Decision**: an `AnomalyDetected` row with source
  `serve.missed_bar_budget` (severity WARNING) is written and sent once; its
  presence stops a second one after a restart or a re-handled bar.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Each shadow spends only its own budget, once per bar

- **A-1.1**: `uv run pytest tests/integration/scenarios/test_missed_bar_budget.py tests/unit/adapters/ledger/test_sqlite_ledger_missed_bars.py` passes.

### R-2: The allowance is the largest whole number within 1%

- **A-2.1**: `uv run pytest tests/unit/application/monitoring/test_missed_bar_budget.py` passes.

## Design

```text
serve._report_restart: reason = restart_report(...) + "\n" + budget.summary()
                       then _alert_if_budget_crossed()
serve.handle (late bar, not catching up): _alert_if_budget_crossed()
_alert_if_budget_crossed: crossed and no row from serve.missed_bar_budget
                          -> append AnomalyDetected, send to alert channels
```

## Test plan

1. Every acceptance criterion above passes.
2. Each new test fails with its guard removed (distinct count, catch-up
   alert, live-loop alert; checked by hand).
3. `bash scripts/dev/pr-preflight.sh` → all checks green.

Test tiers touched: unit, property, integration

## Risks

- **D-1 read differently**: if Rick reads the 1% against bars elapsed, only
  `missed_bar_allowance` changes.
- **Alert lost on a crash between the row and the send**: the row is written
  first, so the alert is not sent again; the restart report still shows the
  budget at 0 left.

## Dependencies on other changes

- PR #1103 (issue #1078) for `BarMissed`; issue #1076 for one ledger per
  shadow.
