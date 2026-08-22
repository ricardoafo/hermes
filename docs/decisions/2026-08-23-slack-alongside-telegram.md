# Add Slack channel alongside Telegram for notifications

**Date**: 2026-08-23
**Author**: claude on behalf of Ricardo
**Related**: ADR-008 § Telegram alerts (partially superseded by this entry),
`openspec/changes/phase-1-observability-rollback-slack-scaffolds/change.md` D-3

## Decision

Route notifications across two channels by content class. Telegram
stays for urgent/terse (push-to-phone alerts). Slack is added for
rich/long-form (weekly reports, postmortem threads, CSV attachments).
An application-layer router (`hermes.application.notification_router`,
Phase 1) decides which adapter(s) fire per event class.

## Context

ADR-008 §4 chose Telegram as the sole notification channel with the
scope "user-facing signals only" — appropriate for phone alerts but
inadequate for the reporting surface (§5 weekly/monthly/yearly
reports). Telegram's rendering is thin: no rich formatting, no
threads, poor attachment story. Cramming a weekly rebalance report
into a Telegram message either truncates or arrives as an unreadable
wall of text.

Slack is better for content that benefits from formatting, cross-
references, threaded discussion, and file attachments (CSVs,
generated PDFs, SBOMs). Slack is worse for incidents where phone-push
latency matters — its notifications are more easily silenced or
missed on mobile.

Each tool has a natural lane. Using both eliminates the "wrong shape"
compromise both tools require alone.

## Alternatives considered

**Telegram only** (ADR-008 §4 status quo):

- Simple: one adapter, one credential, one dashboard
- Fails on reporting surface: reports must be plaintext-truncated or
  posted as file attachments with weak preview
- Rejected — the reporting cost outweighs the operational simplicity

**Slack only**:

- Rich formatting handles all content shapes
- No reliable phone-push for incidents — Slack mobile notification
  reliability is inferior to Telegram's
- Rejected — cannot degrade incident response for reporting comfort

**Auto-fallback (primary + backup)**:

- E.g., Slack primary, Telegram on Slack timeout
- Adds complexity (fallback state machine, dedup logic on recovery)
  for a rare failure mode
- Rejected — defer until we've measured actual channel outage rates.
  Router handles the split without this complication.

## Rationale

The router assigns events to channels by content shape:

| Event class             | Telegram         | Slack                     |
| ----------------------- | ---------------- | ------------------------- |
| Service crash           | ✅               | ❌                        |
| Health-check fail       | ✅               | ❌                        |
| Rollback fired          | ✅ (short)       | ✅ (postmortem thread)    |
| CVE detected            | ✅ (short)       | ✅ (analysis + timeline)  |
| Deploy done/failed      | ✅ (one line)    | ❌                        |
| Weekly rebalance report | ❌               | ✅ (formatted table)      |
| Monthly tax report      | ❌               | ✅ (CSV attachment)       |
| Trade fired (single)    | ❌               | ❌ (log only, no notify)  |

Both adapters implement `NotifierPort` (see
`src/hermes/ports/notifier.py`) with two methods: `notify_urgent()`
and `notify_report()`. Adapters no-op the method that doesn't fit
their channel — Telegram no-ops `notify_report`, Slack implements
both.

## Consequences

**What this locks in**:

- Both channels active; router decides per event class
- One `NotifierPort` Protocol with two methods (no split hierarchy)
- Two adapter implementations under `src/hermes/adapters/notifier/`
  (`telegram_adapter.py`, `slack_adapter.py`) — land in Phase 1
- Credentials for both channels stored per ADR-003 §04 (env vars via
  pydantic-settings, `SLACK_WEBHOOK_URL` + existing
  `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`)

**What this forecloses**:

- Fire-and-forget notifications become fire-and-decide (router
  overhead per event). Acceptable — router is a pure function on
  `EventClass` enum, ~10 lines of code.
- Cannot easily add a third channel without touching the router. If
  Discord/Matrix/etc. show up later, refactor to a plugin registry.

**What we'll revisit**:

- Channel routing table (in this entry) — evolve as new event classes
  appear. Each change: update the table + PR body cites this journal.
- If Slack's rendering also becomes limiting for reports (e.g., need
  full HTML/PDF), consider adding email as a third channel.

## ADR-008 status

Not modified (AGENTS.md prohibition #10 — ADRs are immutable once
Accepted). This decision-journal entry supersedes the specific
"Telegram alerts — user-facing signals only" scope in ADR-008 §4.
The overarching observability discipline (structured logging,
correlation IDs, journald, healthchecks.io dead-man's-switch) is
unchanged; only the notification surface expanded.
