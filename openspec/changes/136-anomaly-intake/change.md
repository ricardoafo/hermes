# Change: 136-anomaly-intake

**SDD change**: `136-anomaly-intake`
**Date**: 2026-10-06
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#136, record-only part)

Mirror of engram topic key `sdd/136-anomaly-intake/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Every `AnomalyDetected` hermes raises is now appended to the ledger `events`
table, and `hermes anomalies` lists them read-only. Serve subscribes the event
to `ledger.append_event` the same way it does `SignalEmitted` and
`BarProcessed`, and the three alert jobs (`archive-check`, `reconcile --notify`,
`parity check --alert`) append each alert before sending it. Nothing pauses,
gates or kills on an anomaly: binding rule 2 of
`docs/decisions/2026-10-03-operator-sees-shadow-pnl.md` allows a halt only for
a §10 criterion or an operational fault, so the pause stays a separate decision
for the Code Owner. The main risk is the alert jobs, which were read-only, now
write one row next to a running serve.

## Scope

### In

- `serve.py`: `event_bus.subscribe(AnomalyDetected, ledger.append_event)`.
- `entrypoints/_anomaly_record.py`: best-effort append of one anomaly from a
  CLI job through `SqliteLedger.append_event`.
- `archive-check`, `reconcile --notify` and `parity check --alert` record each
  alert before sending it; the alert text and its routing are unchanged.
- `adapters/ledger/anomaly_reader.py` (read-only, `mode=ro`) and the
  `hermes anomalies [--since] [--db] [--json]` command.
- `infrastructure/config/models.py`: the tax-guard docstring no longer claims
  that an anomaly pauses signal generation.

### Out

- Pausing entries on an anomaly: a separate decision for the Code Owner,
  limited to an allow-list of operational faults (issue #136 re-scope, item 2).
- Replaying anomalies at startup: without a pause flag there is no state to
  restore, so a replay would rebuild nothing.
- Wiring `tax_guard/window.py` and `monitoring/execution_quality.py` into
  serve: both return events but nothing calls them in `src` yet. When serve
  publishes them, the new subscription records them with no further change.
- The SA-9 shadow kill (§10 line 235): moves to issue #514.

## Decisions

### D-1: Persist through the existing event-sourcing path

- **Decision**: Serve records anomalies by subscribing them to
  `ledger.append_event`. The CLI jobs call the same `append_event` through a
  short-lived write connection.
- **Alternatives**: a new `anomalies` table (a second source of truth beside
  `events`); a notifier-side hook (couples recording to delivery, so an
  unconfigured channel would also lose the record).
- **Rationale**: ADR-003 §03 keeps every domain event in `events` with its
  schema version, and `hermes ledger verify` already covers that table.
- **Cost of reversal**: LOW. Drop the subscription and the helper call; the
  rows already written stay valid events.

### D-2: Recording never changes an alert's delivery or a job's exit code

- **Decision**: The CLI append happens before the send. A missing or
  unwritable ledger prints one WARNING and the job continues unchanged.
  A missing ledger is never created.
- **Alternatives**: fail the job when recording fails (a ledger hiccup would
  then silence a real alert).
- **Rationale**: the alert is the operator-facing safety signal. The record is
  there for later review.
- **Cost of reversal**: LOW.

### D-3: A new read-only command, not an extension of `hermes activity`

- **Decision**: `hermes anomalies` reads only `AnomalyDetected` rows.
- **Alternatives**: add anomaly rows to the `activity` feed (touches the
  console render path and its snapshots, which are out of this change's scope).
- **Rationale**: a smaller surface, and the console can adopt the reader later.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Serve records every anomaly published on its bus, and nothing else changes

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_serve.py -k anomaly` passes.
- **A-1.2**: `uv run pytest tests/integration/entrypoints/test_serve_fills.py -k anomaly`
  passes: signals, trades, fill quality, positions and notifications are equal
  with and without anomalies on the bus.

### R-2: Each CLI alert is recorded exactly once

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_archive_check.py tests/unit/entrypoints/test_reconcile.py tests/unit/entrypoints/test_parity.py tests/unit/entrypoints/test__anomaly_record.py` passes.

### R-3: Recorded anomalies are listable read-only

- **A-3.1**: `uv run pytest tests/unit/entrypoints/test_anomalies.py tests/unit/adapters/ledger/test_anomaly_reader.py` passes.

### R-4: No pause, gate or kill is driven by an anomaly

- **A-4.1**: `rg -n "AnomalyDetected" src/hermes/application/order_router src/hermes/domain/strategy` prints nothing. The only new subscriber is `ledger.append_event`.

## Design

```text
serve bus ── AnomalyDetected ──> SqliteLedger.append_event ──> events
archive-check / reconcile --notify / parity check --alert
   └─ record_anomaly(db, alert, err) ──> SqliteLedger.append_event ──> events
   └─ notify (unchanged)
hermes anomalies ──> SqliteAnomalyReader (mode=ro) ──> events WHERE event_type='AnomalyDetected'
```

`record_anomaly(db: Path, event: AnomalyDetected, err: TextIO) -> bool` and
`SqliteAnomalyReader.since(since: datetime) -> tuple[AnomalyDetected, ...]`
(newest first).

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh`: all checks green.

Test tiers touched: unit, property, integration

## Risks

- **A CLI write next to a running serve.** SQLite serialises the two writers.
  The job's one-row append waits up to the default 5 s busy timeout. If it
  still fails, it warns and the alert is sent anyway (D-2).
- **`SqliteLedger(...)` runs migrations from a CLI job.** This is the same
  idempotent runner every ledger open uses. The ledger file must already
  exist, so a job never creates a fresh one.

## Dependencies on other changes

- None. The pause decision (issue #136 re-scope item 2) builds on these rows.
