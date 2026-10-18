# Change: 1181-wire-tax-guard

**SDD change**: `1181-wire-tax-guard`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#1181, tax-guard part)

Mirror of engram topic key `sdd/1181-wire-tax-guard/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-002 Decision 6 promises an engine-level recharacterization guard. The
guard (`application/tax_guard/window.py`, `evaluate_ledger`) and its ledger
read (`SqliteLedger.trades_between`, #135) exist and are tested, but serve
never called them. Serve now evaluates the guard on the first 1D bar of each
daily close, publishes a breach on its bus (recorded in the ledger, #136) and
alerts it on the anomaly channels. Record and alert only: the pause the ADR
also promises stays the Code Owner's decision (#136). The main risk is a
duplicate or missed daily row around pairs and restarts, covered by D-1.

## Scope

### In

- `entrypoints/serve.py`: `_BarLoop._check_tax_guard`, called next to
  `_record_correlations` in `handle`.
- Docstrings that said no code reads `TaxGuardConfig` (`models.py`,
  `root.py`, `config/tax_guard.yaml`) and the stale "future engine pauses"
  notes in `window.py`.
- Integration tests in `tests/integration/entrypoints/test_serve_tax_guard.py`
  and `test_serve_fills.py`; catalogue entries in `failure_modes.py` and
  `rules.py`.

### Out

- Pausing or gating entries on a breach: the Code Owner's decision (#136).
  Listed as a gap in `tests/integration/scenarios/rules.py`.
- Removing `hermes.application.tax_guard`, `...tax_guard.window` and
  `hermes.ports.trade_history` from the parked list of
  `tests/unit/test_production_reachability.py`: that file lands with PR #1221;
  the entries are removed after it merges.
- FX conversion of non-EUR fees and the cross-tenant threshold of ADR-002 §6:
  unchanged from the guard as shipped.

## Decisions

### D-1: One evaluation per daily close, decided by its first 1D bar

- **Decision**: the first 1D bar handled for a close time evaluates the window
  `[close - 90 days, close]`, stamped at the close. Later 1D bars of other
  pairs with the same close do not evaluate it. After a restart, a tax-guard
  row already at or after the close (read with `latest_anomaly`) skips it.
- **Alternatives**: every 1D bar (one row per pair per day); waiting for all
  pairs' 1D bars as the correlation recorder does (a pair whose daily bar is
  passed over would silence the guard for that day); the wall clock as the
  window end (the result would depend on when the bar was handled).
- **Rationale**: delivery order is fixed (`order_for_delivery`), so the
  evaluating bar is the same on every run; the bar's close makes the window
  independent of handling time and of catch-up after a restart.
- **Cost of reversal**: LOW.

### D-2: A lasting breach is recorded and alerted once per daily close

- **Decision**: no suppression of repeats: each daily close that still breaches
  gives one row and one alert, with that day's counts in the reason.
- **Alternatives**: record only on change, like the clock-skew anomaly (#859);
  the guard emits no "back under the ceiling" event, so the state could not be
  rebuilt after a restart without a new event type.
- **Rationale**: the #136 intake records every anomaly published on the bus,
  and the periodic alert jobs it covers (`archive-check`, `reconcile
  --notify`) record every alert they send, run after run, without
  suppressing repeats. Each row is that day's window.
- **Cost of reversal**: LOW: a change-only filter can be added in
  `_check_tax_guard`.

### D-3: A failed evaluation is logged and skipped

- **Decision**: any exception while reading or evaluating is one
  `serve.tax_guard.failed` warning naming the error; the bar is still
  processed. Publishing a breach is outside that guard, so a ledger that cannot
  be written fails serve as every other ledger write does.
- **Rationale**: the same rule as the correlations (#835): a record-only side
  step never stops the loop.
- **Cost of reversal**: LOW.

## Requirements

### R-1: A breach on a daily close is recorded and alerted once

- **A-1.1**: `uv run pytest tests/integration/entrypoints/test_serve_tax_guard.py`
  passes.

### R-2: A breach changes no decision, fill or position

- **A-2.1**: `uv run pytest tests/integration/entrypoints/test_serve_fills.py -k tax_guard`
  passes.

### R-3: Tax data stays measurement only

- **A-3.1**: `uv run lint-imports` keeps `tax-is-measurement-only`.

## Design

```text
_BarLoop.handle(bar)
  ... record_live_bar, _record_correlations(bar)
  _check_tax_guard(bar)          only 1D, only the first bar of a close
    latest_anomaly(source)       restart: row at/after close -> skip
    evaluate_ledger(ledger, config.tax_guard, now=bar.close_time)
    breach -> event_bus.publish  -> ledger.append_event (#136)
           -> _alert             -> every ANOMALY_DETECTED channel
  ... portfolio mark, BarProcessed
```

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest -n auto -W error tests` passes.

Test tiers touched: integration

## Risks

- **EUR-quoted pairs**: the guard counts every EUR fee as fee income and the
  shipped ceiling is 0, so a pair quoted in EUR would breach on its first
  trade. The shipped universe is quoted in USDT, so no EUR fee is recorded.
- **Journal**: the reason can carry a EUR fee figure, so the journal line
  carries the close time only; the reason goes to the ledger and the alert.

## Dependencies on other changes

- PR #1221 (production reachability test): remove the three parked tax-guard
  entries after it merges.
