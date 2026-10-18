# Change: 1078-restart-recovery

**SDD change**: `1078-restart-recovery`
**Date**: 2026-10-18
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1078-restart-recovery/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

When serve comes back from downtime it evaluates every bar that closed while
it was down. Today those bars are filled as if live: an exit is dated at the
old bar's close on the §6.2 book, and an entry is halted as RO-04, which counts
towards the kill inputs. This change applies the rule Rick set for ADR-024 §7:
a bar reached more than the live window (5 min) after its close is recorded as
missed, its entry is not filled, its exit fills at the price when serve is
back, stamped now, and one restart report names the downtime, the missed bars
and the exits checked. The main risk is a clock that is wrong at boot, so the
service now waits for time sync.

## Scope

### In

- `BarMissed` domain event and the `reached_late` rule
  (`src/hermes/domain/events/bar_missed.py`).
- `ShadowExecutor.execute(..., price_bar=)` and `prior_fill()`
  (`src/hermes/application/execution/shadow.py`).
- Serve bar loop: record missed bars, pass over missed entries, fill missed
  exits on the newest bar, send the restart report
  (`src/hermes/entrypoints/serve.py`,
  `src/hermes/application/monitoring/restart_report.py`).
- Parity: a day with missed bars is reported as not compared
  (`ParitySourcePort.missed_bars_between`, `check_day`, `hermes parity check`).
- `hermes.service` and `hermes-staging.service` order after `time-sync.target`.

### Out

- The per-shadow missed-bar budget: issue #1076 reads the `BarMissed` rows.
- Enabling `systemd-time-wait-sync.service` on hermes-01: a machine step for
  Rick, sent through the coordinator.
- The cutover move tool (issue #1085).

## Decisions

### D-1: A missed bar is one handled more than the live window after it closed

- **Decision**: reuse `LIVE_BOOK_WINDOW` (5 min), the line past which no live
  book stands in for a bar.
- **Alternatives**: any bar handled during catch-up (a quick restart inside the
  window would lose a fillable entry); one interval late (a 3-hour-old entry
  would still fill).
- **Rationale**: the same line already decides live vs synthetic books (D-2 of
  the shadow executor), so a missed bar is exactly a bar no live book can price.
- **Cost of reversal**: LOW, one constant.

### D-2: The strategy still reads a missed bar; only execution changes

- **Decision**: the signal row is written as before; the entry is not filled
  and the strategy is synced flat; the exit fills now.
- **Alternatives**: skip missed bars entirely (indicators and the stop would
  drift from the backtest).
- **Rationale**: ADR-021 parity and the stop marks need every bar evaluated.
- **Cost of reversal**: MEDIUM.

### D-3: A passed-over entry is not a halt

- **Decision**: no `RiskHaltRecorded` row.
- **Alternatives**: keep RO-04 (counts an outage as a risk event in the kill
  inputs).
- **Rationale**: nothing failed; the price is simply gone.
- **Cost of reversal**: LOW.

### D-4: Parity skips a day with missed bars

- **Decision**: `check_day` returns `missed_bars > 0` and no mismatches; the
  CLI prints `not compared` and exits 0.
- **Alternatives**: compare and expect mismatches (the nightly alert would fire
  after every outage).
- **Rationale**: the difference is by rule, not a defect.
- **Cost of reversal**: LOW.

### D-5: The restart report is an `AnomalyDetected` with source `serve.restart`

- **Decision**: severity WARNING, recorded in the ledger and routed to the
  alert channels like the clock-skew alert. Text names times, counts and exit
  outcomes; never a price or P&L (ADR-012 D-02).
- **Cost of reversal**: LOW.

## Requirements

### R-1: No back-dated fills after downtime

- **A-1.1**: `uv run pytest tests/integration/scenarios/test_downtime_recovery.py` passes.
- **A-1.2**: `uv run pytest tests/unit/application/execution/test_shadow.py -k missed` passes.

### R-2: Missed bars are recorded and reported once

- **A-2.1**: `uv run pytest tests/unit/domain/events/test_bar_missed.py tests/unit/application/monitoring/test_restart_report.py` passes.

### R-3: Parity does not alert on an outage day

- **A-3.1**: `uv run pytest tests/unit/application/parity/test_check.py tests/unit/entrypoints/test_parity.py tests/unit/adapters/ledger/test_parity_reader.py` passes.

### R-4: Serve waits for the clock

- **A-4.1**: `uv run pytest tests/unit/deploy/test_systemd_units.py -k clock` passes.

## Design

```text
_BarLoop.handle(bar)
  late = reached_late(bar, now=clock.now(), window=LIVE_BOOK_WINDOW)
  signal = strategy.on_bar(bar)            # unchanged
  entry and late and no prior fill -> pass over, sync flat, no halt
  exit  and late                   -> executor.execute(..., price_bar=newest[pair])
  late -> ledger.append_event(BarMissed) just before BarProcessed
warm_up_and_catch_up()
  ... catch-up ...
  any missed -> AnomalyDetected(source="serve.restart") to ledger + alert channels
```

`ShadowExecutor.execute` with `price_bar` always tries the live book; the
fill is stamped with the snapshot time, or now on the §6.2 fallback, and the
quality row measures drift against `price_bar`.

## Test plan

1. Every acceptance criterion above passes.
2. The scenario tests failed on main before the change (RO-04 halt on the
   missed entry, exit dated at the old close, no missed rows).
3. `bash scripts/dev/pr-preflight.sh` → all checks green.

Test tiers touched: unit, property, integration

## Risks

- **A clock far off at boot** marks live bars as missed (entries passed over)
  or missed bars as live. Mitigation: `After=time-sync.target`, the existing
  clock-skew alert, and Rick enabling `systemd-time-wait-sync.service`.
- **A crash between the `BarMissed` row and `BarProcessed`** re-handles the bar
  and writes a second row. Mitigation: the row is appended just before
  `BarProcessed`; #1076's budget should count distinct bar keys.
- **A missed exit with the depth read down** fills on the §6.2 book of the
  newest bar, stamped now; the quality row records it as synthetic.

## Dependencies on other changes

- ADR-024 §7 (merged in PR #1082).
- Issue #1076 consumes the `BarMissed` rows.
- VPS step for Rick: enable `systemd-time-wait-sync.service` on hermes-01.
