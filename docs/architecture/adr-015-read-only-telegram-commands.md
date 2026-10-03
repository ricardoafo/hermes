# ADR-015: Read-only Telegram commands — a separate long-polling bot that answers the operator, never trades, and keeps pushed messages blind

**Date**: 2026-10-03
**Status**: Proposed — Accepted when the Code Owner merges it
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: in part, [ADR-003 §06](adr-003-solution-architecture/06-notifications.md) "Telegram adapter —
outbound-only, no polling" and its rejected-alternatives row "Webhook / polling for inbound commands", for the
read-only commands below only. Everything else in ADR-003 §06 and [ADR-008](adr-008-observability-strategy.md)
§4 stands.

---

## Context

ADR-003 §06 makes the Telegram adapter outbound-only and defers inbound commands to MCP in Phase 3. On 2026-10-03
the Code Owner decided they want to check hermes from their phone in MVP1 without SSH: whether it is running, what it
did, and how the money stands. The approved console UX sets the shape: everything in EUR, Madrid local time, a dim
one-line status, net first, and no signals card on the home view.

Three constraints shape the answer:

- **Attack surface.** ADR-003 rejected inbound HTTP. A webhook would open a port on the VPS.
- **Trading safety.** Nothing reachable from a chat may change what trades (ADR-001, ADR-012 loop autonomy, the
  five binding rules in [2026-10-03 operator sees shadow P&L](../decisions/2026-10-03-operator-sees-shadow-pnl.md)).
- **Alert fatigue.** ADR-008's not-for-Telegram rule: routine INFO never goes to Telegram.

Two related channels already exist or are in flight: the Slack line when `hermes.service` stops uncleanly (#380,
PR #440) and the daily alive line on Slack (#373).

## Decision

### D-01: A separate bot process, long polling, no webhook

`hermes bot` runs as its own systemd unit, `hermes-bot.service`, separate from `hermes serve`. It long-polls
Telegram `getUpdates` over outbound HTTPS (50 s timeout). There is no webhook and no listening port. A crash or
hang of the bot cannot affect the trading loop, and the bot opens the ledger read-only (SQLite `mode=ro`).

### D-02: A fixed set of read-only commands

| Command | Reply |
| --- | --- |
| `/status` | The heartbeat verdict and "your move" line, next check (Madrid and UTC), what happened since the last `/status`, the 90-day test state, total value and today's net |
| `/next` | Next expected bar per stream, Madrid and UTC |
| `/signals` | Signals of the last 7 days |
| `/money [week\|month\|year]` | Gross, fees, estimated tax, net for the period (today by default) |
| `/pos [COIN]` | Open positions, net per row; with a coin, that position's detail |
| `/logs` | Last 20 WARNING-or-worse journal lines, secrets masked |

Anything else gets a one-line help reply. Adding a command needs an amendment to this ADR. Replies are short
sentences, amounts in EUR, times in Madrid local with UTC where a time is actionable. Replies reuse the read model
and renderers of the CLI (#370, #374 to #376, #384), so the bot holds no SQL or business logic of its own.

### D-03: It never accepts a command that changes anything

There are no write commands: no pause, resume, close, parameter change or sizing change, and no free-text
instructions. The bot has no write handle on the ledger, and its unit does not receive exchange credentials (D-05).
Adding any write command needs a new ADR and the Code Owner's sign-off.

### D-04: Only the operator's chat gets answers

The bot answers only `HERMES_TELEGRAM__CHAT_ID`. A message from any other chat gets no reply and is logged at
WARNING with the chat id and no message text.

### D-05: The bot token lives only in a root-owned env file on the VPS

- The token and chat id are read at start through `pydantic-settings` as `SecretStr` (ADR-003 §04 secret rules)
  and are never logged, rendered or echoed in errors.
- On the VPS they live in `/etc/hermes/env.bot`, `root:root` `0600`, written by `vps-bootstrap` from the GitHub
  environment secrets. systemd expands it for `hermes-bot.service` only. The bot unit does not load
  `/etc/hermes/env.production`, so it never sees exchange keys.
- Nothing is committed: no token in the repo, in fixtures (test literals are fake and carry `# gitleaks:allow`), or
  in docs. The unit is enabled only when both variables are present.
- Rotation: revoke in BotFather, update the GitHub secret, re-run bootstrap, restart `hermes-bot.service`.

### D-06: What Telegram may send

| Kind | Who triggers it | Content | Sound |
| --- | --- | --- | --- |
| Command reply | The operator, by asking | Status, and money through the `pnl_visible` gate | Normal |
| P0 alert (ADR-003 §06 table) | hermes, pushed | No P&L (#319) | Loud |
| Heartbeat state change (D-07) | The bot, pushed | Service state only, no P&L | As D-07 |

Money appears only in replies the operator pulls, per the 2026-10-03 shadow P&L decision. Every pushed message
stays blind to P&L (#319). Routine INFO stays off Telegram (ADR-008 §4); the daily alive line (#373) stays on
Slack.

### D-07: Heartbeat alerts are silent while OK and change-only

The bot checks the same status the `/status` reply uses, once a minute:

- OK: nothing is sent.
- Late (a check overdue past its grace): one silent message (`disable_notification`).
- Down: one loud message, then reminders after 15 min, 1 h and 4 h, then every 12 h while it stays down.
- Recovered: one silent "back after N min".

A crash that systemd restarts within the grace never becomes "late", so it produces only the Slack line from
PR #440. healthchecks.io stays the independent dead-man's switch for the host and both units.

### D-08: Rate limits and quiet hours

- At most one reply per command per 5 s; extra requests in that window are dropped with no reply.
- Outbound sends are capped at 1 per second and 20 per minute, under Telegram's per-chat limits; over the cap,
  messages queue and the oldest heartbeat reminder is dropped first.
- Quiet hours 23:00 to 08:00 Madrid (configurable): silent-class messages stay silent, heartbeat reminders are
  held until 08:00, and the first "down" message and P0 alerts still ring. A service outage at night is worth
  waking for; its repeats are not.

### Message flow

```mermaid
flowchart LR
    op((Operator phone)) -- "/status, /money ..." --> tg[Telegram API]
    bot[hermes-bot.service] -- "getUpdates (long poll, HTTPS out)" --> tg
    bot -- "chat id allowed?" --> gate{allowlist + rate limit}
    gate -- no --> warn[WARNING log, no reply]
    gate -- yes --> rm[CLI read model and renderers]
    rm -- "mode=ro" --> ledger[(ledger SQLite)]
    rm -- "pnl_visible: pulled reply" --> reply[reply text]
    reply -- sendMessage --> tg
    hb[heartbeat check, 1/min] --> bot
    bot -- "state change only, no P&L" --> tg
    serve[hermes.service] -- "P0 alerts, no P&L" --> tg
    serve -. "unclean stop (#440)" .-> slack[Slack]
    timer[daily timer (#373)] -. "alive line" .-> slack
    tg --> op
```

## Why

- **Long polling over a webhook** keeps the VPS with no inbound port; the cost is one idle HTTPS request per 50 s.
- **A separate unit** isolates trading from a chat client bug, and lets the bot run with fewer secrets.
- **Pull versus push** is what makes P&L in Telegram compatible with #319: a reply exists only because the operator
  asked, and nothing an AI session reads gets money in it.
- **Change-only heartbeat** gives the phone one message per real state change instead of a stream.

Alternatives considered:

- **Commands through MCP only (ADR-003's plan)**: needs a laptop and Phase 3 tooling. Rejected for MVP1.
- **Commands inside `hermes serve`**: one process, but a chat bug could stall the bar loop. Rejected.
- **Webhook behind Tailscale Funnel**: avoids polling, but exposes an endpoint. Rejected.

## Consequences

### Positive

- The operator sees status and money from the phone without SSH.
- The trading loop and its secrets are untouched by the chat path.

### Negative

- A second long-running unit to deploy, monitor and keep updated.
- Telegram chat history holds money figures. Only the operator reads it; no AI session has access to it.

### Neutral

- ADR-003 §06's outbound adapter is unchanged; the bot adds an inbound reader beside it.

## What would change this decision

- Any need for a write command, which needs a new ADR.
- Telegram rate limits or polling semantics changing in a way that misses messages.
- MCP tools (#34) giving the same answers from the phone, which could retire the bot.

## Cross-references

- [ADR-003 §06](adr-003-solution-architecture/06-notifications.md) — outbound adapter (superseded in part)
- [ADR-008](adr-008-observability-strategy.md) §4 — not-for-Telegram rule
- [ADR-012](adr-012-continuous-ai-evaluation.md) — loop autonomy limits, AI blind to P&L
- [2026-10-03 operator sees shadow P&L](../decisions/2026-10-03-operator-sees-shadow-pnl.md) — pull-only P&L
- Issues #378 (this ADR), #379 (implementation), #373, #380, #384, #319

## Pending

- #379 implements the bot, the unit file and the bootstrap step.
- `vps-bootstrap` writes `/etc/hermes/env.bot` from GitHub environment secrets (part of #379).
