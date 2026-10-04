# Change: 379-telegram-bot

**SDD change**: `379-telegram-bot`
**Date**: 2026-10-03
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/379-telegram-bot/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Implements ADR-015: `hermes bot`, a separate long-polling Telegram process
(`hermes-bot.service`) that answers the operator's chat with read-only
replies (`/status`, `/next`, `/signals`, `/money`, `/pos`, `/logs`) and pushes
change-only heartbeat alerts. It reuses the CLI's status layer, console read
model, ledger activity reader and journal reader, so it holds no SQL or
business logic of its own, and it never accepts a command that changes
anything. Main risk: a second long-running unit with a bot token; the token
lives in a root-only env file the trading unit never loads, and the bot has
no exchange keys and no write handle on the ledger.

## Scope

### In

- `application/bot/`: command parsing and replies (`replies.py`), the
  allowlist and per-command rate limit (`gate.py`), the heartbeat alert state
  machine with backoff and quiet hours (`heartbeat_alerts.py`).
- `adapters/telegram_bot/client.py`: `getUpdates` long polling and
  `sendMessage` with `disable_notification`, outbound throttle.
- `entrypoints/bot.py` and `entrypoints/commands/bot.py` (`hermes bot`).
- `deploy/systemd/hermes-bot.service`; `vps_bootstrap.sh` writes
  `/etc/hermes/env.bot` (`root:root 0600`) from the Telegram secrets and
  enables the unit only when both are set; `install_release.sh` restarts the
  bot after a good deploy if it is running.
- `hermes status` shows the bot unit's state (`bot: running|down|not installed`).

### Out

- Live money (`/money`, `/pos`): until the ledger-backed read model and fills
  land (#353), these answer from the demo scenario when `HERMES_CONSOLE_DEMO`
  is set and otherwise say there is no live money yet.
- Telegram-side menus, inline keyboards, any write command (ADR-015 D-03).

## Decisions

### D-1: one gate in the application layer

Allowlist (only `HERMES_TELEGRAM__CHAT_ID`) and the 5 s per-command rate limit
are pure logic over an injected clock, so they are tested without Telegram. A
message from another chat is logged at WARNING with its chat id and no text.

### D-2: replies are plain short sentences

Telegram replies are plain text (no Markdown, so no escaping bugs), Madrid
time with UTC where actionable, built from the same views the CLI prints.
`/status` leads with the `hermes status` verdict, reason and next step. Money
replies pass through `pnl_visible(Surface.TELEGRAM_PULLED)`.

### D-3: heartbeat alerts are a pure state machine

`HeartbeatAlerter.observe(level, now)` returns the messages to send: nothing
while OK; one silent message on late; one loud message on down, then
reminders after 15 min, 1 h, 4 h and every 12 h; one silent "back after N
min" on recovery. Quiet hours (23:00 to 08:00 Madrid) hold reminders until
08:00; the first down message still rings. Pushed messages carry no money.

### D-4: outbound throttle in the client

The client keeps at most 1 send per second and 20 per minute by waiting before
a send (injected sleeper). Replies and alerts are rare, so waiting is enough;
nothing is dropped.

### D-5: secrets

The token and chat id are read once as `SecretStr` and never logged or echoed.
On the VPS they live only in `/etc/hermes/env.bot`; the bot unit does not load
`env.production`.

## Requirements

### R-1: only the configured chat gets answers; others are logged, not answered

### R-2: one reply per command per 5 s; extra requests get no reply

### R-3: each command renders from the shared layers; unknown text gets a one-line help

### R-4: money only in pulled replies; pushed alerts never carry money

### R-5: heartbeat transitions and backoff per D-3, quiet hours respected

### R-6: client long-polls with an offset and sends with `disable_notification` for silent messages

### R-7: unit enabled only with both secrets; restarted after a good deploy

### R-8: `hermes status` shows the bot state

## Design

`hermes bot` loads config, builds the client, gate, replies and alerter, then
loops: `getUpdates` with a 30 s long-poll timeout, answer allowed commands,
and once a minute build the status report and feed its verdict to the alerter.
Ctrl-C or SIGTERM ends the loop cleanly.

## Test plan

- `tests/unit/application/bot/`: gate (allowlist, rate limit), replies per
  command (status, next, signals, money demo and live, pos, logs, help),
  money visibility, alerter transitions, backoff and quiet hours.
- `tests/unit/adapters/telegram_bot/`: respx contract test of `getUpdates`
  and `sendMessage`, throttle.
- `tests/unit/entrypoints/test_bot.py`: one loop iteration end to end with a
  fake client.
- Deploy: unit file test, bootstrap env.bot and enable step, release restart.

## Risks

- A wrong allowlist would answer strangers; the gate has its own tests and the
  chat id comes only from the root-owned env file.
- Long polling holds one HTTPS connection; a network error backs off and retries.

## Dependencies on other changes

- ADR-015 (#378), #370 (status), #384 (read model), #453 (activity reader),
  #376 / PR #468 (journal reader; this PR opens after #468 merges).
