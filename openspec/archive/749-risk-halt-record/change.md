# Change: 749-risk-halt-record

**SDD change**: `749-risk-halt-record`
**Date**: 2026-10-17
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/749-risk-halt-record/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Serve answers an entry with `Declined(rule="HALT")` when a risk-overlay rule halts it and with
`Halted` when a pre-registration §10 kill fires. Both left one log line only. This change records
each as a value-free `RiskHaltRecorded` row in the ledger `events` table, alerts it on Telegram and
Slack (loud), and shows the 24-hour count and the newest halt in `hermes status`. It is record-only:
when and whether halts and kills fire is unchanged. The main risk is a figure leaking into a blind
surface, so the event refuses free text in its rule, reason and citation fields.

## Scope

### In

- `domain/events/risk_halt_recorded.py`: the event and `HaltKind` (halt or kill).
- `application/execution/halt_record.py`: outcome → record, with fixed reasons.
- `KillDecision.rule`: a stable id per §10 criterion, set by both kill evaluators.
- `SqliteLedger.has_risk_halt`, `SqliteStatusReader.halts_since`, shared row parser.
- Routing class `RISK_HALT` (Telegram + Slack, always loud) and its note.
- Serve: record once, then alert; `hermes status` text and `--json`.

### Out

- Wiring the shadow kills and the drift check into serve (#796), which records through this event.
- Any change to a halt or kill rule, or to #514's inputs.
- A console screen for halts.

## Decisions

### D-1: The event is built from the kernel outcome in serve, not in the router

- **Decision**: `halt_record(outcome, …)` maps `Declined(rule="HALT")` and `Halted` to the event after
  `ShadowExecutor.execute` returns; serve records and alerts it.
- **Alternatives**: publish from the router (it has no pair, time or strategy context and also runs in
  the backtest); subscribe on the event bus (the bus cannot deduplicate a re-evaluated bar).
- **Rationale**: the backtest replay shares the kernel and must stay free of ledger writes and alerts.
- **Cost of reversal**: LOW.

### D-2: Value-free by construction

- **Decision**: halts take the router's fixed rule table (`value_free_reason`); kills take a fixed reason
  per `KillDecision.rule`. The event refuses a rule that is not an id, a reason that is not one
  snake_case word and a citation that is not `§N`.
- **Rationale**: ADR-012 D-06; the router's and the evaluator's own texts quote drawdowns and prices.
- **Cost of reversal**: LOW.

### D-3: One row per (strategy, pair, kind, rule, signal time)

- **Decision**: `occurred_at` is the signal time and serve skips a record the ledger already has, so a
  bar evaluated again after a crash neither records nor alerts twice.
- **Cost of reversal**: LOW.

### D-4: Record, then alert; a failing channel is logged

- **Decision**: the row is written first; each channel's exception is logged and sent to Sentry and the
  bar loop goes on (the #854 policy for signals).
- **Cost of reversal**: LOW.

## Requirements

### R-1: Every overlay HALT and §10 kill of an entry is one ledger row

- **A-1.1**: `tests/integration/entrypoints/test_serve_fills.py::test_a_halted_entry_is_recorded_once_and_alerted_once`
- **A-1.2**: `…::test_a_halt_re_evaluated_after_a_crash_is_not_recorded_or_alerted_twice`

### R-2: Record-only

- **A-2.1**: `…::test_a_halt_changes_no_decision_or_signal_push`

### R-3: Value-free

- **A-3.1**: `tests/unit/domain/events/test_risk_halt_recorded.py`, `tests/unit/application/execution/test_halt_record.py`

### R-4: `hermes status` shows the 24-hour count and the last halt

- **A-4.1**: `tests/unit/entrypoints/test_status.py::test_text_shows_the_halt_count_and_the_last_halt`

## Design

```text
RiskHaltRecorded(strategy_id, pair, kind: halt|kill, rule, reason, pre_registration_ref: "§N" | None)
halt_record(outcome, *, strategy_id, pair, at, correlation_id) -> RiskHaltRecorded | None
SqliteLedger.has_risk_halt(event) -> bool
StatusReadPort.halts_since(since) -> tuple[RiskHaltRecorded, ...]   # newest first
StatusReport.halts_24h: int; StatusReport.last_halt: RiskHaltRecorded | None   # 30-day lookback
```

Citations follow pre-registration v1's numbering: RO-01 cites §5.5, the other overlay rules and every
kill cite §10, and an unclassified halt cites none.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` is green.

## Risks

- **Alert noise.** A halt fires only on an entry signal, a few per week at most on 4h bars.
- **v2 numbering.** The citations assume v1's sections; a v2 pre-registration with other numbering
  needs the table updated with it.
