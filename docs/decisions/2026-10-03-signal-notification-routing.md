# Route shadow signals to Telegram and Slack

**Date**: 2026-10-03
**Author**: AI Bot on behalf of the Code Owner
**Related**: `openspec/archive/309-serve-bar-loop/change.md` D-4 and R-0,
[2026-08-23 Slack alongside Telegram](2026-08-23-slack-alongside-telegram.md),
ADR-012 ruling 1 (aggregate blind), #319

## Decision

A new event class, `SIGNAL_EMITTED`, fires on both channels: Telegram for the
push, Slack for the durable record. `NotifierPort` gains `notify(event)`, the
template-driven method both adapters already implement. Messages show pair,
side, price and bar time only; no P&L, return, equity, Sharpe, drawdown or
win-rate figure appears until OOS certification.

## Context

The 2026-08-23 entry predates the shadow bar loop, so it has no row for a
strategy signal. A signal needs immediate visibility (Telegram) and a record
a person can search later (Slack), the same shape as `POSITION_LARGE`.
That entry is a merged snapshot and is not edited; this one adds the row.

Alternatives: reuse `notify_urgent` (it builds a synthetic CRITICAL anomaly,
the wrong shape); Telegram only (no durable record).

## Consequences

| Event class     | Telegram        | Slack                 |
| --------------- | --------------- | --------------------- |
| Signal emitted  | yes (short)     | yes (durable record)  |

- The router table in `notification_router.py` and its parity test change
  with this entry.
- Fan-out runs in a worker thread (both adapters use a blocking HTTP
  client). A channel outage is logged and sent to Sentry; it never stops the
  loop. Notifications are at-most-once; the ledger row is the record.
- A signal older than 2 bars is recorded but not notified.
