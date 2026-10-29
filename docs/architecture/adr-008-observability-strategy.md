# ADR-008: Observability strategy — structlog to journald, healthchecks.io dead-man's switch, Telegram alerts, no metrics stack

**Date**: 2026-08-20
**Status**: Accepted
**Accepted**: 2026-08-21
**Deciders**: Maintainer
**Supersedes**: —

## Context

Hermes runs unattended on a single Hetzner VPS most of the day. The operator
checks in a handful of times per week, has no dashboards mounted, and
carries a phone as the primary alert channel. When something goes wrong, the
critical questions are: what happened, when did it happen, what triggered
it, and what does the operator need to do about it?

Traditional observability stacks — Prometheus, Grafana, Loki, Alertmanager —
answer these questions well at scale. At the scale of "one process family on
one VPS with a solo operator", they impose a maintenance surface that
exceeds their benefit: three additional services to run, three additional
retention policies to configure, three additional pathways for a
mis-configuration to hide a real signal.

The observability need is real; the tooling to satisfy it does not have to
be enterprise. The counter-question guiding this ADR: **at N=1 operator +
N=1 machine + ~30-100 trades/year, what is the minimum instrumentation that
answers the four critical questions reliably?**

## Decision

Hermes uses a **five-piece observability stack**, each piece chosen for its
fit at solo-VPS scale.

### 1. Structured logs via `structlog` → stdout → journald

- Every log line is a JSON dict. Keys are stable; values are typed.
- Every log entry carries a **correlation ID** (a ULID stamped at the top of
  each engine cycle) and a **cycle type** (`rebalance` | `swing_check` |
  `swing_stop` | `backup` | `report` | `healthcheck` | `reconcile`).
- `structlog` is configured with the `ConsoleRenderer` for local dev
  (colored, human-readable) and `JSONRenderer` in production. Selection is
  by env var `HERMES_LOG_RENDERER`, not by imports.
- systemd's `StandardOutput=journal` routes stdout into journald.
- **No log files.** journald is the store of record.
- **Retention** — journald is configured with `SystemMaxUse=2G` and
  `MaxRetentionSec=180day`. Six months is enough for post-mortems and short
  of forever. Longer retention lives in the SQLite `events` table for
  business events only.

### 2. Business events → SQLite `events` table

Structured logs are for "what code is doing"; the `events` table is for
"what the business is doing". Every trade, every rebalance decision, every
anomaly gets a durable row with:

- `event_type` (from a controlled enum: `TradeExecuted`, `RebalanceTriggered`,
  `AnomalyDetected`, `EngineePaused`, `EngineResumed`, ...)
- `correlation_id` linking back to the cycle that produced it
- `payload` as JSON (the event's specific data)
- `emitted_at` timestamp

Retention: **forever** (or backup horizon). Events are the audit trail
for what the engine did and why; they never age out.

### 3. Healthchecks.io — dead-man's-switch

- A systemd timer pings a healthchecks.io endpoint every 5 minutes.
- The endpoint is configured with a 10-minute alert window: two consecutive
  misses trigger a Telegram notification.
- The ping payload includes the last cycle's structured summary
  (rebalance status, ledger row count, uptime).
- **Why healthchecks.io and not self-hosted**: the whole point is that we
  learn about the VPS being down. A self-hosted heartbeat detector on the
  same VPS answers the wrong question.

### 4. Telegram alerts — user-facing signals only

- Every trade fires an individual Telegram message (per ADR-003 section 06).
- Every anomaly detected by SPC or by explicit rule (see ADR-005) fires
  an individual Telegram message with severity + suggested action.
- Weekly digest fires Sunday 20:00 CET with the week's trades, current
  positions, realized/unrealized P&L, and health.
- Immediate push: anomalies, engine pauses, reconciliation mismatches,
  healthcheck outages (from healthchecks.io directly, not from hermes).
- **All messages in English** (per user preference).
- **Not-for-Telegram** rule: routine INFO logs (cycle started, cycle
  finished OK) NEVER go to Telegram. Telegram is a signal channel; if it
  becomes noisy, the operator stops reading it.
  [ADR-015](adr-015-read-only-telegram-commands.md) (Proposed) keeps this rule: command
  replies are pulled by the operator, not pushed.

### 5. Report generation — weekly + monthly + yearly

- Weekly report generated Sunday 20:00 CET (systemd timer). Rendered as a
  Rich table in Telegram + full HTML at `/reports/YYYY-WW.html` accessible
  via Tailscale.
- Monthly report generated first Sunday of the month. Includes tax-basis
  running total, YTD gains, tax-guard status.
- Yearly report generated 2027-01-05 (early January) for FY 2026 (and
  every subsequent year). Produces Modelo 100 pre-fill draft + Modelo 721
  pre-fill draft (see ADR-002).

### What is explicitly NOT in this stack

- **No Prometheus / Grafana / Loki / Alertmanager.** At N=1 machine and
  ~30-100 trades/year, the metrics stack is administrative overhead that
  buys nothing structured logs + journald + healthchecks.io don't already
  buy.
- **No OpenTelemetry SDK.** OTel is a fine choice at larger scale; for
  hermes the traces would be self-referential (one process family, one
  correlation ID per cycle) and add another dependency's version-tracking
  overhead.
- **No hosted SIEM.** Logs live on the VPS in journald; egress is
  deliberate (backup copies to Backblaze B2 encrypted, monthly).
- **No error-tracking SaaS** (Sentry / Rollbar / Honeybadger). Uncaught
  exceptions in the engine fire a Telegram alert with the traceback body;
  the alert IS the ticket.

### Correlation ID convention

- Format: ULID (26 chars, sortable by time, monotonic-per-instance).
- **Stamped once** at the top of each cycle by the entrypoint (systemd
  service or `hermes` CLI command).
- Threaded through every log line and every event row via a
  `structlog.contextvars.bind_contextvars(correlation_id=...)` call at
  the top of the cycle. Every downstream log line inherits it.
- Every Telegram message includes the last 6 chars of the correlation ID
  as `[…{tail}]` so an operator reading a phone alert can grep journald
  for the full cycle.

### What gets logged, at what level

| Level      | Used for                                                                | Retention                                             |
| ---------- | ----------------------------------------------------------------------- | ----------------------------------------------------- |
| `TRACE`    | Never in production (compile-out); dev-only for adapter wire debugging  | Local terminal only                                   |
| `DEBUG`    | Adapter request/response summaries; disabled in prod by default         | 30 days if enabled                                    |
| `INFO`     | Cycle start/end, decision outputs (trade fired, no-op, pause)           | journald 180 days                                     |
| `WARNING`  | Retry occurred, rate-limit near threshold, degraded but recoverable     | journald 180 days                                     |
| `ERROR`    | Failed to place order, ledger write failed, config parse failed         | journald 180 days + Telegram alert                    |
| `CRITICAL` | Unrecoverable — process should exit; e.g. corrupted DB, missing secrets | journald 180 days + Telegram alert + healthcheck fail |

### What NEVER gets logged

- **Credentials of any kind.** API keys, secrets, Telegram bot token, DB
  paths that could leak secret file names.
- **Personally identifying data.** ADR-002's LOPDGDD posture (per severity
  rubric row S8) forbids PII in logs. hermes doesn't handle third-party
  PII, so this is straightforward, but the rule is stated for
  future-proofing.
- **Full order-book snapshots** or raw exchange responses larger than a
  few KB. These belong in DEBUG-level as summaries or in the ledger as
  structured rows.

## Why

### Why structlog specifically

Structlog's contract — every log line is a dict, formatting is pluggable —
matches how the operator will read logs. Post-mortems always begin as `journalctl -u hermes-rebalancer | jq 'select(.correlation_id == "...")'`; that shape is possible because every line is JSON.

The `contextvars` binding is the killer feature: bind the correlation ID
once at cycle top, every log call from every layer picks it up. No manual
threading of context through function signatures.

Alternatives (`loguru`, stdlib `logging` with a JSON formatter) are
adequate; structlog is chosen because its stability and contextvars
integration are best-in-class in Python.

### Why journald and not files

- journald handles rotation and retention deterministically. `SystemMaxUse`
  is a hard cap; files with logrotate are a coordination problem.
- `journalctl -u <unit>` slices logs per service without touching files;
  cross-service queries work the same way.
- Structured fields survive: `journalctl --output=json` gives back the JSON
  dict we emitted.
- journald is the OS convention on Ubuntu 22.04; fewer surprises for
  operators familiar with systemd.

### Why healthchecks.io, not self-monitoring

The failure modes we need to detect include "the VPS is down", "the systemd
timer stopped firing", "the process wedged and stopped writing logs". A
monitor on the same VPS cannot detect any of these; the whole point of an
external dead-man's switch is that it fails loud when the VPS goes silent.

Alternatives (StatusCake, UptimeRobot, Pingdom) are equivalent. Healthchecks.io
was chosen for: free tier covers our cadence, Telegram integration is
first-class, "cron mode" (expected-cadence) is simpler than uptime-mode
(response-required) for our use case.

### Why Telegram for signals, not email / SMS / Slack

The operator carries a phone. Telegram is:

- Push-notified reliably on iOS + Android
- Trivial to script against (Bot API is a REST endpoint)
- Rich-text capable (code blocks for structured summaries, tables for
  weekly digest)
- Free at any plausible message rate
- Historized (message history is searchable months later)

Email is slower, unreliably delivered, and gets buried in inbox noise.
SMS costs money and is limited in length + formatting. Slack is a
professional-context tool the operator doesn't want conflated with a
personal trading system.

### Why no metrics stack at N=1

At N=1 machine, the observability questions Prometheus answers well ("what
is p99 latency across shard replicas?" / "what is the memory pressure
across the fleet?") don't apply. What we need is:

- Is the engine alive right now? (healthchecks.io)
- What did it do in the last hour? (journalctl)
- Did anything go wrong that needs attention? (Telegram alerts)
- What is the P&L week / month / year? (report generation)

None of these need a time-series database. If we ever reach N=multiple
machines, adding Prometheus + a scraper is straightforward — but paying
the operational cost today for a hypothetical tomorrow is the
premature-optimization move ADR-003 principle 2 explicitly rejects.

### Why "not-for-Telegram" is enforced by code, not by convention

Alert fatigue is the failure mode. An operator who stops reading Telegram
because it's noisy is worse off than one who doesn't get alerts. The
tiering (INFO to log, ERROR to Telegram + log, CRITICAL to Telegram +
log + healthcheck) is enforced at the notification layer: `notify()` refuses
to send an INFO-level message. Overriding requires code review.

## Consequences

### Positive

- **Post-mortems have a stable start.** Correlation ID + journald +
  events table cover 95% of "what happened when" queries without
  additional tooling.
- **Alerts don't decay to noise.** The tiering + not-for-Telegram
  enforcement means every Telegram message is either a trade (expected)
  or a signal worth attention.
- **Dead-man's switch is external.** VPS-down or process-wedged is a
  Telegram push, not a "why didn't I see anything for six hours"
  discovery.
- **Retention is deterministic.** journald caps at 2GB / 180 days; SQLite
  events grow but stay auditable; nothing is dropped silently.

### Negative

- **No aggregation across time-series.** "What was the average slippage
  in Q1?" requires a report query on the events table, not a dashboard
  panel. Acceptable at our data volume; would be painful at scale.
- **Correlation ID discipline is manual.** A code path that forgets to
  inherit the contextvars binding produces logs without correlation
  ID; the fix is a linter rule enforcing `bind_contextvars` presence
  at cycle-entry functions. Rule to be implemented in Phase 1.
- **journald is Linux-specific.** Local dev on macOS / Windows uses
  `ConsoleRenderer` to stdout; log-shape parity with prod requires
  a `HERMES_LOG_RENDERER=json` env in local scripts. Documented in
  the dev bootstrap guide.
- **Healthchecks.io is a third-party dependency.** If they go down or
  change pricing, the dead-man's switch moves elsewhere. Runbook
  documents the failover to a StatusCake or self-hosted `cronitor`
  clone; not urgent.

### Neutral

- **The `llm_audit` table (per ADR-003 section 03) is a special-case
  events table** for LLM I/O in Phase 3+. Populated only when the LLM
  supervisor path is active; separate from the general `events` table.
- **`hermes logs --cycle <correlation_id>`** is a CLI convenience that
  wraps `journalctl` with the right filter. Trivial; part of Phase 0.
- **Report generation uses the same code path locally and on the VPS.**
  A monthly report is generated identically in a dev shell and on
  the VPS timer.

## What would change this decision

- **Second machine / second engine instance.** Time-series aggregation
  across machines needs Prometheus + a scraper. Additive.
- **Regulatory audit requiring immutable log storage** (e.g. write-once
  media, external SIEM). The events table + monthly encrypted backup
  cover most of this; a strict interpretation would add an append-only
  log ship to an external destination.
- **Metrics needed at sub-cycle cadence** (e.g. sub-second latency
  tracking for HFT-style scenarios — not applicable here). Prometheus
  or comparable would be the answer.
- **Alerting requires escalation.** The current single-channel Telegram
  approach assumes one operator. Multiple operators or on-call
  rotation would require Alertmanager or PagerDuty.

## Cross-references

- [ADR-002](./adr-002-tax-posture.md) — tax-guard anomalies fire via
  the Telegram-alert path
- [ADR-003 section 03](./adr-003-solution-architecture/03-persistence-schema.md)
  — `events`, `heartbeats`, `llm_audit` schemas
- [ADR-003 section 06](./adr-003-solution-architecture/06-notifications.md)
  — Telegram delivery mechanism, template layout
- [ADR-003 section 09](./adr-003-solution-architecture/09-deployment.md)
  — systemd + journald configuration
- [ADR-005](./adr-005-ai-ml-support-boundary.md) — SPC anomaly
  detection produces the Telegram-alert stream Category B feeds
- ADR-009 — Documentation strategy (runbook lives at `docs/runbook/`)
  *(pending)*

## Pending

- **Runbook** — `docs/runbook/` skeleton created in Phase 0 with one
  seed entry (`vps-restart-procedure.md`); expanded as incidents happen
- **journald retention setting on VPS** — configured in the deployment
  playbook alongside systemd unit installation
- **Healthchecks.io endpoint creation** — user action; free tier account
  plus one check per timer. To be done before shadow mode.
- **`hermes logs` CLI subcommand** — Phase 0
- **`bind_contextvars`-at-entrypoint linter rule** — Phase 1, once we
  have entrypoints to inspect
