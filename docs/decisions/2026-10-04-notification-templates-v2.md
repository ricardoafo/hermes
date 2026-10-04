# Notification templates v2 (proposal)

**Date**: 2026-10-04
**Status**: Proposed. The Code Owner picks one shape per family; the recommendation is marked.
**Tracking issue**: #144
**Inputs**: [notification inventory](../research/2026-10-04-notification-inventory.md),
[notification UX research](../research/2026-10-04-notification-ux-research.md)

## Context

Today every alert path speaks a different dialect. Telegram has a coloured
circle, an UPPER_SNAKE title and a `ref:` line. Slack has a text severity field.
The shell alerts are plain sentences. Timestamps are ISO UTC, durations are raw
seconds, numbers are raw decimals (`68000` next to `61234.5`), and EUR and USDT
are mixed. Everything on Telegram is loud. Several events have no template or no
route: `StrategyResumed` has no Telegram template, `LotClosed` and
`BarProcessed` have none at all, and `RebalanceTriggered` has no route. The
inventory lists each gap.

The console design approved on 2026-10-03 already fixed the house style for
screens: EUR everywhere, Madrid time, plain words, net first, one dim status
line. This proposal brings notifications into the same style.

## Rules every template follows

These apply whichever shapes are picked:

1. **One formatter layer.** Money, prices, percentages, durations and times
   come from the console format helpers, in EUR and Madrid local time. UTC is
   added only where a time is actionable on the VPS (log lookups).
2. **Numbers with fixed precision and separators.** Write `€61,234.50` and
   `+1.22%`, never `61234.5` or `0.35`.
3. **The first line answers "what happened, to what".** Kind and coin come
   first. The severity glyph is a plain-text sign (`▲ ▼ ■ ◐ ●`), so it reads
   the same on both channels and in the shell scripts.
4. **Loud only when it needs the operator now.** Down, P0 alerts and a
   rejected order ring. Everything else sends with `disable_notification`,
   following ADR-015 D-06 and D-07.
5. **Blind until certification.** Pushed messages never carry P&L (#319).
   Plan-versus-actual messages show the plan, and the actual half reads
   "withheld until certification".
6. **One ref per message** (the correlation-id tail), at the end, so a line
   can be grepped with `hermes logs --cycle`.
7. **Recovery mirrors the alert.** A down alert gets a matching "back after
   N min" message, and a pause gets a resume message, on the same channel.

## Family 1: trade lifecycle (signal, entry, stop raise, exit, rejection)

### A. Compact (recommended for Telegram)

```text
▲ Entry long BTC · €52,410.20
stop €50,516.00 (−3.6%) · size 0.0040 BTC · Sat 14:00
swing-donchian-4h · …K7Q2ZP
```

### B. Verbose (recommended for Slack, with the plan in a thread)

```text
▲ Entry long BTC · €52,410.20 · Sat 14:00 (12:00 UTC)
Plan: stop €50,516.00 (−3.6%, 1.8 ATR) · size 0.0040 BTC · risk €7.58
Actual: withheld until certification
swing-donchian-4h · …K7Q2ZP
```

### C. Minimal

```text
▲ Long BTC €52,410.20 stop €50,516.00 (14:00)
```

**Tradeoff**: compact fits the lock screen and carries the stop that defines
the plan. Verbose is the audit record. Minimal is fastest to read, but it drops
the ref and the strategy.

## Family 2: health and ops (down, late, back, paused, resumed, anomaly, balance)

### A. Compact (recommended for both channels)

```text
■ hermes is down since 03:12 (8 min)
crashed and stopped after 5 restarts
See why: hermes logs --since 03:00
```

```text
● hermes is back after 12 min
```

### B. Verbose (Slack only)

```text
■ hermes is down since 03:12 (8 min) · hermes-01
crashed and stopped after 5 restarts · exit 1
Last error: serve.run.failed LedgerError: disk full
See why: hermes logs --since 03:00 · …H1M9XA
```

### C. Minimal (also the layout the shell scripts can reproduce without the venv)

```text
■ DOWN hermes.service 03:12
● OK hermes.service 03:24 (12 min)
```

**Tradeoff**: compact gives the elapsed time and one action hint, which is what
a change-only alert needs. Verbose is for a desk. Minimal is what bash can
produce, so the failure and crash scripts can match the templated path.

## Family 3: reports (daily line, weekly rebalance, rebalance heads-up)

### A. Compact (recommended)

```text
hermes alive · 18x4h + 3x1d bars in 24 h · 0 signals · next 4h bar 16:00
Rebalance heads-up: weights drift 4.1% (limit 5%) · no action yet
```

Slack posts the summary and puts the CSV in a thread. Telegram gets a single
pointer line, and only for the weekly rebalance heads-up.

### B. Verbose

Slack gets the compact summary plus a section per stream and a table of current
and target weights, in EUR. Add this after certification: until then it is the
report most likely to leak an outcome word.

### C. Minimal

Only the daily alive line (#373), with the day's signal and rebalance counts
added.

## Recommendation

- **Trade lifecycle**: compact on Telegram, verbose on Slack with the plan in a
  thread.
- **Health and ops**: compact on both channels, with minimal reused by the shell
  scripts.
- **Reports**: compact on Slack, with a Telegram pointer only for the weekly
  rebalance heads-up.

## Open for the Code Owner

1. The shape per family. The recommendation is above.
2. Whether Slack gets colour through attachments. Block Kit alone can't colour
   a message, and this needs a check in the real workspace.
3. Whether the shell alerts (`hermes-notify-failure.sh`,
   `hermes-notify-crash.sh`) move to the same wording now or after the
   templated path ships.

## What happens next

Once the shapes are picked, the implementation PR follows
`openspec/changes/144-notification-system/change.md`. It contains:

- the shared formatters;
- the templates rewritten for every event, with the missing ones added;
- routes for the unrouted events;
- silent and loud flags;
- `hermes preview` covering every event on both channels;
- a migration test showing old payloads still render.

Once that lands, the Code Owner previews each template in a real channel. That
preview is #144's acceptance step.
