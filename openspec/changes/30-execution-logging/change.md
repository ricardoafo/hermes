# Change: 30-execution-logging

**SDD change**: `30-execution-logging`
**Date**: 2026-10-04
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/30-execution-logging/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Every simulated shadow fill already records its fill price, book mid, slippage and
model drift in `FillQualityRecorded`. This change adds two record-only fields to that
event: the arrival price (close of the signal bar) and the decision-to-fill latency in
milliseconds. They let implementation shortfall and latency be analysed after the 90-day
shadow window. Nothing that prices, sizes, gates or measures a fill reads them, so the
pre-registration is untouched. The event goes to schema v2 with an upcaster for v1 rows.

## Scope

### In

- `arrival_price: Decimal | None` and `delta_ms: int | None` on `FillQualityRecorded`.
- Schema version 1 -> 2 and `upcast_fill_quality_payload`, used by the ledger read.
- `ShadowExecutor` fills both fields when it writes the quality row.

### Out

- Any use of the fields in metrics, risk rules, kill rules or the drift gate.
- A new ledger table or column: the event JSON in `events` already carries the payload.
- Logging: the executor performs no logging by design; the caller logs the result.

## Decisions

### D-1: Fields live on FillQualityRecorded (schema v2)

- **Decision**: Add both fields to `FillQualityRecorded`, default `None`, schema v2.
- **Alternatives**: A new event type (a second row per fill to join); a ledger column
  (the event is stored as JSON, so no column exists to extend).
- **Rationale**: One row per fill already carries execution quality; code-conventions §10
  asks for an additive field plus an upcaster.
- **Cost of reversal**: LOW — readers ignore unknown keys; v1 rows stay readable.

### D-2: arrival_price is the signal bar close

- **Decision**: `arrival_price = bar.close` of the bar passed to `execute`.
- **Alternatives**: The signal's `entry_price` (absent on exits).
- **Rationale**: The close is the last price the strategy saw when it decided.
- **Cost of reversal**: LOW.

### D-3: delta_ms is fill time minus signal bar close_time

- **Decision**: `delta_ms = floor((fill.timestamp - signal.signal_time) / 1 ms)`;
  validation refuses a negative value instead of clamping it.
- **Alternatives**: Wall-clock `now` minus close (differs from the recorded fill time).
- **Rationale**: Uses the timestamps already stored on the event, so it is replayable.
- **Cost of reversal**: LOW.

## Requirements

### R-1: New fields validated

- **A-1.1**: `arrival_price <= 0` raises `ValueError`; `delta_ms < 0` raises `ValueError`.
- **A-1.2**: Both default to `None`.

### R-2: v1 rows stay readable

- **A-2.1**: A schema-1 row reads back with both fields `None`; an unknown version is a `LedgerError`.

### R-3: The executor records them

- **A-3.1**: `quality.arrival_price == bar.close`; `quality.delta_ms` equals the whole
  milliseconds from `signal.signal_time` to the fill's timestamp.

## Design

- `fill_quality_recorded.py`: two defaulted fields, `__event_schema_version__ = 2`,
  validation in `__post_init__`, `upcast_fill_quality_payload(version, payload)`.
- `sqlite_ledger.py`: `fill_quality_events` upcasts then rehydrates; no migration.
- `shadow.py` `_quality`: passes `arrival_price=bar.close` and `delta_ms`.

## Test plan

1. Event validation, defaults, upcaster (domain unit tests).
2. Ledger round trip, v1 row, unknown version (SQLite `:memory:`).
3. Executor records close and 6000 ms on a live fill, 0 ms on a synthetic one.
4. `bash scripts/dev/pr-preflight.sh` green.

Test tiers touched: unit, integration

## Risks

- **Record-only**: fill price, fill kernel, §6.2 book, drift and slippage computation,
  sizing, fees and kill rules are unchanged. Pre-registration v1 §6 (fee and execution
  model), §7 (certification lifecycle, SA-5 drift gate), §9 (metrics) and §10 (kill
  criteria) were checked; §11 lists changes to §6, §9 and §10 as deviations, and none occurs.
- **Old rows**: v1 rows have `None` in both fields; consumers must treat them as unknown.
- **Negative delta**: not clamped. A fill stamped before its signal would fail validation;
  the executor stamps fills at or after the signal time, so this cannot happen today.

## Dependencies on other changes

- Builds on `353-fill-kernel-in-serve` (FillQualityRecorded).
