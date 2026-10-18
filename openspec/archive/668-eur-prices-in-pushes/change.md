# Change: 668-eur-prices-in-pushes

**SDD change**: `668-eur-prices-in-pushes`
**Date**: 2026-10-05
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/668-eur-prices-in-pushes/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Telegram and Slack pushes show prices in the pair's quote currency (USDT)
because events carry no FX rate, while the console, `hermes money` and the tax
surfaces all show EUR. This change converts the prices a push shows (entry
price, stop, fill price, value, fee) to EUR with the rate `serve` already holds
in its `EurPricer`, and falls back to the USDT text when no rate is known.

## Scope

### In

- `EurPricer.cached_rate`: a read-only lookup of an already-known rate.
- `build_note`, `telegram_template` and `slack_template` take an optional
  `eur_rate` (quote units per euro) and format the quote-currency prices in EUR.
- `TelegramAdapter` and `SlackAdapter` take an optional `eur_rate` lookup
  callable at construction; `serve` injects it from the pricer.
- `hermes preview --eur-rate RATE` renders the samples with a sample rate.

### Out

- Any P&L figure in a push (aggregate blind, ADR-012 ruling 1).
- `BalanceLow` amounts: they are balances in the asset, not prices.
- Refreshing the rate on the bar path or from the notify path (see D-1).
- Changing `NotifierPort.notify(event)`.

## Decisions

### D-1: no network on the notify path

The lookup is `EurPricer.cached_rate(quote, on_date)`. It returns a rate only
if one is already held: the final rate cached for that Madrid date, or else the
most recent rate fetched for that quote (final or provisional) when its
`rate_date` is at most 7 days from `on_date`. It never calls the provider. A
slow or failing FX source therefore cannot delay a push or a bar (PR #659 was
reverted for making serve 10x slower per bar). Rates reach the cache from the
fill path and from the boot backfill, both of which already run off the bar
path. No new boot lookup is added: boot EUR pricing must not delay catch-up
(issue #658), so warming the cache at boot belongs to that work.

**Staleness**: a rate older than 7 days (a long holiday weekend plus slack)
is treated as unknown, so the push shows USDT rather than a stale euro figure.

### D-2: the rate travels as data

Adapters take `eur_rate: Callable[[DomainEvent], Decimal | None] | None = None`
at construction. `serve` builds it from the pricer, the event's quote currency
and the Madrid date of `occurred_at`. `NotifierPort.notify(event)` is
unchanged. The note builders take `eur_rate: Decimal | None` (quote units per
euro, as the ECB quotes it) and divide. With no rate, or a quote that is
already EUR, they emit today's text, so the fallback is visible and never wrong.
A lookup that raises is treated as no rate.

### D-3: no P&L, no rendered text in logs

Only prices, stop, value and fee are converted. No profit, loss or return
figure is added, and the existing P&L-word scan keeps passing. Rendered push
text is not logged anywhere: structured logs keep event type, channel and error.

### D-4: preview shows both forms

`hermes preview <event> --eur-rate 1.1721` renders with that sample rate;
without the option it renders the USDT fallback. Both forms are tested.

### D-5: no dependence on `entry_*` bar-line fields

The change reads only `SignalEmitted` and `TradeExecuted` fields that exist
on main.

## Requirements

### R-1: with a rate, entry/exit signal prices, stop and fill price, value and fee read in EUR (`€52,410.20`)

### R-2: without a rate, or when the lookup returns None or raises, the text is the USDT text of today

### R-3: `cached_rate` never calls the provider, and returns None for an unknown quote or a stale rate

### R-4: the adapters pass the event to the injected lookup and render with its answer

### R-5: `hermes preview --eur-rate` renders EUR; a non-positive or non-numeric rate exits 2

### R-6: no P&L word reaches any rendered line

## Design

`format.fmt_quote_money(amount, quote, eur_rate)` returns
`fmt_money(amount / eur_rate, EUR)` when a rate is given and the quote is not
EUR, else `fmt_money(amount, quote)`. The signal and trade builders call it for
every price. `serve._eur_rate_lookup(pricer)` returns the callable that maps an
event with a `pair` to `pricer.cached_rate(pair.quote, fill_date_madrid(occurred_at))`.
Serve adds no new FX call; until a rate is cached the push keeps its USDT text.

## Test plan

- `test_eur_pricer.py`: `cached_rate` returns the cached final, the latest
  provisional within 7 days, None when stale or unknown; a counting provider
  proves it makes zero calls.
- `test_format.py`, `test_notes.py`, `test_telegram.py`, `test_slack.py`: EUR
  with a rate, USDT without, EUR quote untouched, stop/fee/value converted.
- `test_telegram_adapter.py`, `test_slack_adapter.py`: the lookup receives the
  event, its rate reaches the wire text; None and a raising lookup give USDT.
- `test_serve.py`: the lookup built by serve reads the pricer cache only.
- `test_preview.py`: `--eur-rate` and the fallback form.
- Template snapshots: unchanged by design (they render without a rate); a new
  EUR snapshot pair covers the with-rate form.
- The P&L-word scan runs over the EUR renderings too.

Test tiers touched: unit.

## Risks

- The cache can be empty or stale on a long-running serve with no fills, so
  pushes fall back to USDT. Visible, never wrong. Follow-up: a daily refresh
  off the bar path.
- Two rates can coexist (final for a past date, latest for today); the
  exact-date final wins.

## Dependencies

- Issue #658 (boot EUR pricing) owns any boot-time warm-up of the pricer cache.
