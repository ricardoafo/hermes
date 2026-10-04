# Notification inventory (2026-10-04)

**Status**: Research digest for issue #144. Describes the notification system
as it exists on `main` at commit `792ab25`. Nothing here proposes a design; the
companion
[UX research](2026-10-04-notification-ux-research.md) and the later
templates-v2 decision do that.
**Method**: Source read of `src/hermes/application/messages/`,
`notification_router.py`, `signal_notifier.py`, `deploy/*.sh`, ADR-015, and
rendered output from `uv run hermes preview`. Confirmed facts come from code
or command output; working hypotheses are marked as such.

## Summary

Hermes has four independent alert paths, and only one of them uses the
templates this issue is about:

1. Domain-event templates (Telegram MarkdownV2 and Slack Block Kit), routed by
   `notification_router.py`.
2. Shell scripts under `deploy/` that post plain text straight to the Telegram
   or Slack API when systemd reports a failure or a crash, and once a day.
3. The Healthchecks.io dead-man's switch, which alerts through its own
   channels.
4. The read-only Telegram bot of ADR-015 (heartbeat alerts), whose code is not
   on `main` yet.

At runtime only one domain event is dispatched from the serve loop:
`SignalEmitted`, through `SignalNotifier` (`serve.py:353`). The
`archive-check` command dispatches `AnomalyDetected` on the
`ANOMALY_DETECTED` route. Every other template is reachable only from
`hermes preview` and tests today.

## Event classes and templates

Rendered by `telegram_template` (7 events) and `slack_template` (8 events plus
one phase-aware event). A missing entry raises `ValueError` at first render.
Both modules dispatch on `type(event)` through a static table.

| Domain event | Telegram template | Slack template | Routing class | Channels |
| --- | --- | --- | --- | --- |
| `SignalEmitted` | yes | yes | `SIGNAL_EMITTED` | both |
| `StrategyPaused` | yes | yes | `STRATEGY_PAUSED` | both |
| `StrategyResumed` | none | yes | `STRATEGY_RESUMED` | Slack only |
| `AnomalyDetected` | yes | yes | `ANOMALY_DETECTED` | both |
| `BalanceLow` | yes | yes (unrouted) | `BALANCE_LOW` | Telegram only |
| `TradeRejected` | yes | yes (unrouted) | `TRADE_REJECTED_ESCALATED` | Telegram only |
| `TradeExecuted` | yes (as POSITION_LARGE) | yes (as Position large) | `POSITION_LARGE` | both |
| `RebalanceTriggered` | yes | yes | none | no class, no route |
| `DailyTradeDigest` | none | yes, phase-aware | `DAILY_TRADE_DIGEST` | Slack only |
| `LotClosed` | none | none | none | not notified |
| `BarProcessed` | none | none | none | not notified |

Routing classes with no domain event and no template: `SERVICE_CRASH`,
`HEALTH_CHECK_FAIL`, `ROLLBACK_FIRED`, `CVE_DETECTED`, `DEPLOY_STATUS`,
`WEEKLY_REBALANCE_REPORT`, `MONTHLY_TAX_REPORT`, and `TRADE_FIRED_SINGLE`
(log-only by design). They exist in the routing table but nothing in
`src/hermes/` constructs them as an event. The deprecated
`notify_urgent` and `notify_report` wrappers wrap free text in a synthetic
critical `AnomalyDetected`, so any caller of those would render as an
anomaly.

Facts worth knowing before redesigning:

- `RebalanceTriggered` has two templates but no `EventClass`, so
  `hermes preview rebalance-triggered` prints "Telegram does not fire" even
  though a Telegram template exists. The router has no way to send it.
- `TradeExecuted` always renders as `POSITION_LARGE`. The renderer does not
  check size; the (not yet written) escalation layer decides. A plain fill has
  no template under its own name.
- `hermes preview list` shows 8 events; `signal-emitted`, the only event live
  in production, is not in the catalog. The signal output below was rendered
  with a short script.
- Neither adapter ever sets `disable_notification` (Telegram) or threads a
  reply (Slack); every Telegram push is loud and every Slack post is a new
  top-level message. The only threading is the planned CSV upload as a reply
  for report events.

## Fields shown per template

Both channels show the same facts per event. Slack adds labelled fields;
Telegram folds them into one sentence.

| Event | Telegram body | Slack section | Never shown |
| --- | --- | --- | --- |
| `SignalEmitted` | pair, side, kind, entry price (or entry ref and exit reason), bar time (ISO UTC) | Pair, Side, Kind, Price or Entry ref plus Exit rule, Bar time | `stop_price`, `atr_context`, `f_star_estimate`, `strategy_id` |
| `StrategyPaused` | strategy id, reason; hint "review strategy" | Strategy, Severity, Reason | pause timestamp outside the Slack header |
| `StrategyResumed` | none | Strategy, "Paused for N seconds" | none |
| `AnomalyDetected` | source in brackets, reason | Source, Severity, Reason | none |
| `BalanceLow` | asset, current, threshold; hint "top up" | Asset, Current, Threshold | which account or wallet, shortfall |
| `TradeRejected` | exchange, side, pair, reason | Exchange, Pair, Side, Reason | quantity, price, order id |
| `TradeExecuted` | side, qty, pair, price | Pair, Side, Qty, Price | fee, strategy id, notional value |
| `RebalanceTriggered` | pair, current weight, target weight, band | Pair, Current weight, Target weight, Band | the proposed trades |
| `DailyTradeDigest` | none | Day, Total trades, Filled, Rejected, Total notional, plus the withheld notice | winners, losers, CSV (all blind) |

Every Telegram message ends with `ref: <last 6 chars of the correlation id>`.
Every Slack message ends with the same tail in a context block. Severity is a
colored circle on Telegram (red for critical, yellow for warning, blue for
info, and a check mark reserved for recovery that no template uses yet) and a
text field on Slack, with no color on Slack at all.

## Blind-until-certification rule

`reporting_phase.py` (#319, ADR-012 ruling 1) fails closed: every template
that could carry an outcome renders blind unless the caller passes
`ReportingPhase.CERTIFIED`. The default is `PRE_CERTIFICATION`; nothing in the
composition root defaults to certified.

- While blind, a message may show service health, signal events with pair,
  side, price and bar time, kill-trigger state as yes or no, and calibration
  metrics M-09 and M-10 as pass or fail.
- Withheld names: pnl, p&l, return(s), equity, sharpe, drawdown, win rate,
  winners, losers (`OUTCOME_FIELD_NAMES`).
- Only `DailyTradeDigest` is phase-aware today. It keeps activity counts and
  notional and appends the sentence "Outcome figures are withheld until OOS
  certification." Unblinded, it adds winner and loser counts and a CSV
  attachment.
- `StrategyPaused` and `AnomalyDetected` take free-text `reason` strings. The
  preview sample reason is "30% drawdown breached", which is itself an
  outcome-adjacent figure. Nothing filters `reason`; the rule holds only as
  long as the code that builds the reason avoids outcome words (working
  hypothesis: worth a guard test in the v2 work).
- Pull-versus-push (ADR-015 D-06): P&L may appear in a bot reply the operator
  asks for, never in a pushed message.

## Rendered examples

Telegram messages use MarkdownV2, so reserved characters carry a backslash
(`swing\.v1`). Each Telegram message starts with a colored circle emoji; this
repo bans emoji in text, so the circle is written as a word in brackets below.
Output is otherwise verbatim from `uv run hermes preview <event>` against the
canned samples. Sample correlation tail: `WY6PH1`.

### Signal entry (both channels; production path)

```text
[blue circle] *SIGNAL\_EMITTED*
BTC/USDT long entry @ 61234\.5 · bar 2026\-10\-03T16:00:00\+00:00
ref: WY6PH1
```

Slack header: `━ Signal entry — 2026-10-03T16:00:00+00:00 ━`. Slack section:

```text
*Pair*: `BTC/USDT`
*Side*: `long`
*Kind*: `entry`
*Price*: `61234.5`
*Bar time*: `2026-10-03T16:00:00+00:00`
```

The bar time appears twice on Slack (header and section). The exit variant
reads `BTC/USDT long exit \(trailing\_stop\) · entry ref 61234\.5 · bar ...`.

### Strategy paused (both channels)

```text
[red circle] *STRATEGY\_PAUSED*
strategy 'swing\.v1' paused — 30% drawdown breached
ref: WY6PH1 · action: review strategy
```

```text
Slack text fallback:
Strategy paused — swing.v1 — 30% drawdown breached
Slack section:
*Strategy*: `swing.v1`
*Severity*: `CRITICAL`
*Reason*: 30% drawdown breached
```

### Balance low (Telegram only)

```text
[red circle] *BALANCE\_LOW*
USDT balance 50 below threshold 100
ref: WY6PH1 · action: top up
```

### Trade rejected (Telegram only)

```text
[red circle] *TRADE\_REJECTED*
binance rejected BUY BTC/USDT — insufficient balance
ref: WY6PH1
```

### Anomaly detected (both channels)

```text
[red circle] *ANOMALY\_DETECTED*
\[swing\.risk\] 30% drawdown breached
ref: WY6PH1
```

### Position large (both channels)

```text
[blue circle] *POSITION\_LARGE*
BUY 0\.5 BTC BTC/USDT @ 68000
ref: WY6PH1
```

"BTC" appears twice because `qty` renders with its unit and the pair follows.
Slack text fallback: `Position large — BUY 0.5 BTC BTC/USDT @ 68000`.

### Daily trade digest (Slack only, blind)

```text
Slack header: ━ Daily trade digest — 2026-08-23 — 2026-08-23T14:22:03+00:00 ━
Slack text fallback: Daily trade digest — 2026-08-23 — 10/12 filled, notional 12500.50 EUR
Slack section:
*Day*: `2026-08-23`
*Total trades*: `12`
*Filled*: `10`
*Rejected*: `2`
*Total notional*: `12500.50 EUR`
Outcome figures are withheld until OOS certification.
```

### Rebalance triggered (Slack only in practice)

```text
Slack text fallback: Rebalance triggered — BTC/USDT at 0.35 vs target 0.25
*Pair*: `BTC/USDT`
*Current weight*: `0.35`
*Target weight*: `0.25`
*Band*: `0.05`
```

## Alert paths outside the templates

| Path | Trigger | Channel | Content | Sound or visibility |
| --- | --- | --- | --- | --- |
| `deploy/hermes-notify-failure.sh` | `OnFailure=` when the unit lands in `failed` | Telegram, plain text, no parse mode | "hermes.service entered failed state on <host>" plus the last 5 journal lines, secrets redacted, 300 chars per line | loud (no silent flag) |
| `deploy/hermes-notify-crash.sh` | `ExecStopPost=` on any non-success stop | Slack `chat.postMessage`, plain `text` | host, `result`, exit code, exit status, "systemd restarts it in 30s; Telegram alerts if it stays down", last `serve.run.failed` line | normal Slack post |
| `deploy/hermes-daily-line.sh` | `hermes-daily.timer`, 07:00 Madrid | Slack, plain text | `hermes status --summary` line: alive or verdict, bars in 24 h, signal count, next 4h bar in UTC and Madrid, errors, warnings, restarts | normal Slack post |
| Healthchecks.io | missed `ping_success` | Healthchecks' own channels (outside this repo) | monitor name and state, owned by the vendor | vendor-defined |
| Read-only bot, heartbeat (ADR-015 D-07) | late, down, recovered | Telegram, bot | state change only; reminders at 15 min, 1 h, 4 h, then 12 h | late and recovered silent, down loud |

Details that matter for design:

- The failure and crash scripts are bash plus curl on purpose: a broken venv
  is a likely cause of the failure, so they cannot import the templates. Any
  v2 visual language has to be mirrored by hand in shell, or the scripts stay
  as the deliberately plain fallback tier.
- The daily line example format (from `daily_line.py`, parts joined by
  ` · `): `hermes alive · 18x4h + 3x1d bars in 24 h · 1 signal · next 4h bar
  20:00 UTC (22:00 Madrid) · 0 errors · 0 warnings · 0 restarts`. The exact
  error and warning parts read `0 errors`, or `2 errors (serve.notify.failed
  x2)` when events exist (`_level`).
- ADR-015 is on `main` with status Proposed, and the bot code is not on `main`: there is no
  long-polling module under `src/` and no `hermes-bot.service` under
  `deploy/` (`rg getUpdates src deploy` returns nothing). The Slack line for a
  crash that systemd restarts within the grace period is the only alert such a
  crash produces. Treat D-07 and D-08 as design intent, not behavior.
- `src/hermes/application/console/heartbeat.py` is on `main`. It builds the
  heartbeat verdict the bot will push, so the heartbeat text is the one
  message family whose template will be written outside
  `application/messages/`.

## Gaps and inconsistencies

### Formatting differs per channel and per path

- Telegram templates are MarkdownV2 with a colored circle, an UPPER_SNAKE
  title and a `ref:` line. The Slack header has no icon and a title-case name.
  The shell scripts post unformatted sentences. The same incident can read
  three different ways.
- Telegram events are named `STRATEGY_PAUSED`; Slack says "Strategy paused";
  the routing enum says `STRATEGY_PAUSED`; the script says "entered failed
  state". There is no shared vocabulary for severity words either: Slack shows
  the enum value (`CRITICAL`), Telegram shows a circle.
- Slack carries no color or icon signal. Severity is a text field, which
  reads the same for `INFO` and `CRITICAL` at a glance.

### Timestamps

- Both channels print ISO-8601 UTC (`2026-08-23T14:22:03+00:00`) with a
  seconds-level offset, and the signal bar time is that same string. The
  operator reads Madrid time (`infrastructure/time.py`: "UTC-internal /
  Europe/Madrid-at-edges").
- The daily line is the only message that shows both zones
  (`20:00 UTC (22:00 Madrid)`), and ADR-015 puts "Madrid and UTC" in the
  `/status` reply. Templates show UTC only, with no Madrid conversion.
- Slack repeats the timestamp in the header and, for signals, again in the
  body. Telegram has no timestamp except on signals, where it is long.
- Durations are raw seconds (`paused for 900s`, "Paused for 900 seconds"),
  never "15 min".

### Numbers and currency

- Numbers are raw `Decimal` strings: `61234.5`, `68000`, `0.35`, `12500.50`,
  with no thousands separator, no fixed precision and no trailing-zero rule
  (`68000` next to `61234.5`).
- Weights render as `0.35` against target `0.25` and band `0.05`, where
  `35%` or `+10 pp` would scan faster. The drawdown reason string uses `30%`,
  so one message family mixes ratio and percent.
- Currency is mixed. Prices and balances are in the quote asset (USDT);
  the digest total is in EUR; the ledger now stores EUR values (#435). Money
  amounts never show a dual-currency form, and the `BalanceLow` text says
  `USDT balance 50` with no EUR equivalent.
- `Quantity` renders with its unit (`0.5 BTC`) while price renders bare, so
  the position line reads `0.5 BTC BTC/USDT @ 68000`.

### Missing or one-sided events

- `StrategyResumed` has no Telegram template, so a pause alert pushed to the
  phone is never followed by a recovery message there; the operator learns of
  the resume only in Slack. The unused check-mark glyph was reserved for this.
- `TradeExecuted` has no fill template of its own; only the large-position
  variant exists, and `LotClosed` and `BarProcessed` have none.
- `RebalanceTriggered` has templates but no route and no "heads-up then full
  report" pair; the weekly rebalance report class has no template or event.
- The operator-facing classes `SERVICE_CRASH`, `HEALTH_CHECK_FAIL`,
  `ROLLBACK_FIRED`, `CVE_DETECTED`, `DEPLOY_STATUS` have no template; their
  real alerts are shell text or Healthchecks.
- Swing-trade lifecycle is not covered: no "position opened", "stop moved",
  or "position closed" message with plan versus actual, which issue #144
  asks for. `SignalEmitted` omits `stop_price`, the one number that defines
  the plan.

### Duplicate and overlapping alerts

- A crash can produce up to three messages: the crash script on Slack, the
  failure script on Telegram if the unit stays failed, and (once the bot ships)
  a heartbeat "down" on Telegram. The crash script says "Telegram alerts if it
  stays down", but two different Telegram senders will then both fire.
- Healthchecks adds a fourth, independent, alert for the same outage.
- A pause fires both channels at once with identical facts and no thread, so
  Slack is a second copy instead of a durable record with more detail.

### Interaction and tooling

- No message carries a hint beyond `action: review strategy` and
  `action: top up`; the other templates give no next step.
- Telegram never sets a silent flag, so the loud-versus-quiet policy of
  ADR-015 D-06 and D-07 has no implementation in the templated path.
- `hermes preview` cannot show the shell-script messages or the daily line,
  and its catalog omits `signal-emitted`.
- A code comment says "Telegram truncates long messages"; the Bot API limit
  is 4096 characters after entity parsing, far above the ~150-character
  target, so the target is a scan-time choice, not a platform limit.

## What this closes

- Which events have which templates, on which channel, and which are
  production-live versus preview-only.
- The exact current wording of every templated message and every shell alert.

## What this opens

- Whether the shell-script alerts should adopt the v2 look or stay plain as a
  fallback tier that works without the venv.
- Whether `RebalanceTriggered`, `TradeExecuted` and the lifecycle events get
  routing classes before templates are rewritten.
- Whether a guard test should reject outcome words in free-text `reason`
  fields while the phase is blind.
- Whether Slack should show color through attachment sidebars or emoji, since
  header blocks accept `plain_text` only.
