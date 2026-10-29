# Change: 1080-exchange-stops

**SDD change**: `1080-exchange-stops`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1080-exchange-stops/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Every real-money position now carries a stop-loss sell held by the
exchange, so a hermes outage never leaves it unprotected (ADR-024 §7). The
stop is a backstop at the strategy's own signed stop level (Code Owner
decision, 2026-10-19, "All A"), not a new exit rule: no strategy, sizing,
fee, stop-level or kill rule changes, and the strategy's exit stays the one
the ledger records. One application service, `ExchangeStopKeeper`, makes the
exchange hold exactly the stop a position wants, through a new
`ExchangeStopsPort` the Binance spot adapter implements, and journals every
change as an `ExchangeStopChanged` row in the existing events table. Serve
runs it at boot (reconcile before any bar), before an exit order, and after
each 4h bar, and only with real money: a shadow places nothing. The main
risk is the stop-loss-limit gap: a crash through the stop and its 1% limit
leaves the order resting unfilled, and only the strategy's own exit sells.

## Scope

### In

- `domain/trading/exchange_stop.py`: `Protection` (the stop a position
  wants), `RestingStop` (one the exchange holds), `StopFate`.
- `domain/events/exchange_stop_changed.py`: the `ExchangeStopChanged` row
  (placed, adopted, cancelled, filled, gone, failed), schema v1, no price
  in its reason.
- `domain/errors.py`: `OrderNotPlacedError` and `OrderStatusUnknownError`
  move here from the Binance adapter (still importable from it), so the
  application layer can tell a refusal from a lost answer.
- `ports/exchange_stops.py`: `ExchangeStopsPort` (place, cancel, list
  resting stop-loss sells, fate of one) and `ExchangeStopJournalPort`.
- `adapters/exchanges/binance_spot.py`: `resting_stop_losses` (GET
  `openOrders`) and `stop_loss_fate` (GET `order`).
- `adapters/ledger/exchange_stop_rows.py`: the SQLite journal over the
  events table. No migration.
- `application/execution/exchange_stops.py`: `ExchangeStopKeeper`.
- `entrypoints/_exchange_stops.py` and three calls in `entrypoints/serve.py`;
  `Composition.stop_exchange`.
- `entrypoints/commands/serve.py`: `hermes serve` refuses `allow_mainnet`
  until a venue is wired (#1220), exit 3.
- Scenarios against the fake market's spot endpoints and failure-mode
  catalogue entries.

### Out

- Wiring a real venue into `hermes serve` and recording an exchange fill as
  the ledger's trade: #1220 (MVP2).
- Exchange filters (tick size, `LOT_SIZE`, minimum notional): the stop is
  rounded down to eight decimals only.
- A stop for the shadow or paper path: unchanged by design.

## Decisions

### D-1: The exchange stop sits at the strategy's stop, rounded down

- **Decision**: the exchange holds a stop-loss-limit sell at the strategy's
  stop in force, for the quantity held, both rounded down to the exchange
  step (1e-8).
- **Alternatives**: a stop a fixed distance below the strategy's (a second
  exit rule, rejected by the Code Owner); rounding to nearest (could rest a
  step above the signed stop and fire first).
- **Rationale**: Code Owner decision 2026-10-19 ("All A"); never fire before
  the signed exit, never sell more than held.
- **Cost of reversal**: LOW (one method, `Protection.on_exchange_steps`).

### D-2: One keeper, one target per pass

- **Decision**: every call point hands the keeper the stop the position
  wants (or `None` when flat); the keeper lists what rests, cancels every
  other stop-loss sell on the pair, places the wanted one if missing and
  records the difference.
- **Alternatives**: separate place, move, cancel and reconcile flows (four
  paths to keep consistent; the restart path would be the least exercised).
- **Rationale**: the restart reconcile (#858) is the same pass as every
  bar's, so it is exercised on every bar.
- **Cost of reversal**: MEDIUM.

### D-3: The ledger row is the memory; the exchange is the truth

- **Decision**: each change is an `ExchangeStopChanged` row in the events
  table; a recorded stop missing from the listing is asked for its fate
  (filled or gone) before anything else; a resting stop matching the wanted
  one but missing from the ledger is adopted, never duplicated.
- **Alternatives**: a new table and migration; trusting the ledger alone.
- **Rationale**: the events table is append-only and already restored at
  boot; adoption makes a crash between placement and record harmless.
- **Cost of reversal**: LOW (rows are versioned events).

### D-4: Real money only, and fail closed

- **Decision**: the keeper runs only when `allow_mainnet` is set and the
  composition has a venue; `hermes serve` refuses `allow_mainnet` until
  #1220 wires one (exit 3).
- **Alternatives**: start with real money and no exchange stop (violates
  ADR-024 §7); place stops in the shadow too (breaks "shadow places no
  order").
- **Rationale**: ADR-024 §7, invariant I-08, AGENTS.md rule 7.
- **Cost of reversal**: LOW (the refusal is one guard).

### D-5: A failure never stops the bar loop

- **Decision**: a refused, lost or unreadable call ends the pass with a
  `FAILED` row and a CRITICAL alert; the next bar retries. A placement with
  no answer is retried once; one whose status is unknown is not retried (it
  may rest) and the next listing adopts it.
- **Alternatives**: kill the position (a new exit rule); retry in a loop
  (blocks the bar).
- **Rationale**: the strategy's own exit stays authoritative; the operator
  hears at once.
- **Cost of reversal**: LOW.

## Requirements

### R-1: A stop rests once a real-money entry fills, at the strategy's stop

- **A-1.1**: `uv run pytest -q tests/integration/scenarios/test_exchange_stops.py -k placed_at_entry`

### R-2: The stop moves when the strategy moves it, and is cancelled on exit

- **A-2.1**: `uv run pytest -q tests/integration/scenarios/test_exchange_stops.py -k "moved or before_the_exit"`
- **A-2.2**: `uv run pytest -q tests/unit/application/execution/test_exchange_stops_state_machine.py`

### R-3: A restart reconciles the exchange with the ledger before any bar

- **A-3.1**: `uv run pytest -q tests/integration/scenarios/test_exchange_stops.py -k downtime`
- **A-3.2**: `uv run pytest -q tests/integration/scenarios/test_exchange_stops.py -k crash`

### R-4: A lost answer never leaves two stops or none

- **A-4.1**: `uv run pytest -q tests/integration/scenarios/test_exchange_stops.py -k lost`

### R-5: A shadow places nothing

- **A-5.1**: `uv run pytest -q tests/integration/scenarios/test_exchange_stops.py -k shadow`

### R-6: `hermes serve` refuses real money until a venue is wired

- **A-6.1**: `uv run pytest -q tests/unit/entrypoints/test_cli.py -k real_money`

## Design

```text
serve._BarLoop
  warm_up_and_catch_up ── stops.reconcile ──┐
  _fill (EXIT signal) ─── stops.before_exit ─┼─> ExchangeStopKeeper.keep(pair, wanted)
  handle (4h bar) ─────── stops.after_bar ───┘        │
                                                      ├─ ExchangeStopsPort (BinanceSpotAdapter)
                                                      └─ ExchangeStopJournalPort (SqliteExchangeStopJournal)
```

One pass of `keep(pair, wanted)`:

1. Round `wanted` down to the exchange step.
2. List the pair's resting stop-loss sells.
3. The recorded stop rests in the ledger but not on the exchange: ask its
   fate; `FILLED` records "sold on exchange", `GONE` "no longer on
   exchange", both with a WARNING alert.
4. The position the recorded fill sold (same entry time) wants nothing.
5. Cancel every resting stop that is not the wanted one (`not_in_ledger`,
   `position_closed`, `stop_moved`).
6. Place the wanted stop if none matches (`protect_position`), or adopt the
   matching one the ledger does not name (`found_on_exchange`).

Any call that fails ends the pass with a `FAILED` row and a CRITICAL alert
that names the pair and the reason, never a price or quantity (ADR-012
D-02). The keeper is synchronous, so a pass is deterministic under the sim
clock.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. Mutants checked by hand, each killed: on the keeper and the rounding
   (skip cancel, skip the already-sold check, no quantity rounding,
   half-even rounding, one placement attempt, skip the fate check); on the
   wiring (no reconcile at boot, no cancel before the exit, no keep after a
   bar, keeping stops without `allow_mainnet`, reconcile wanting nothing);
   on the adapter (stop-loss buys read as stops, executed quantity ignored);
   on the CLI refusal.

Scenarios (`tests/integration/scenarios/test_exchange_stops.py`): placed,
moved and cancelled on exit; cancelled before the exit sells; the exchange
stop fills on the chandelier gap while the ledger keeps the signed exit; a
stop that sold during downtime; a stop cancelled outside hermes during
downtime; a lost answer (`Lost.PLACED`, `Lost.DROPPED`); a crash between
placement and record; a shadow with a venue wired. The harness gains
`real_money` and `spot`; with real money it records each pair's spread from
the fake book one minute before each 4h close, because serve's sampler waits
on wall seconds that a simulated run never gives it, and RO-02 would halt
every entry on an empty median.

Tests came before the keeper code for the unit tier; the scenarios were
written after the serve wiring.

Test tiers touched: unit, property, integration

## Risks

- **Stop-loss-limit gap**: the exchange order is a stop-loss-limit with a
  1% buffer. A bar that gaps through the stop and the limit leaves it
  resting unfilled; the strategy's own exit (its signed `STOP_ORDER`
  pricing) stays authoritative, cancels the resting stop and sells. The
  backstop protects an outage, not a gap. No rule changes to cover it.
- **The exchange fills before the strategy**: on the bar where the market
  reaches the stop, the exchange stop sells at its stop while the strategy
  exits at its own signed fill. Until #1220 records the exchange fill as
  the ledger's trade, the keeper records `FILLED` and the strategy's exit
  stays in the ledger as the authoritative exit.
- **A moved stop leaves a brief gap**: the old stop is cancelled before the
  new one is placed (the exchange locks the quantity), so for one round
  trip nothing rests.
- **Filters are not applied**: tick size and `LOT_SIZE` beyond eight
  decimals are not read; a pair with a coarser step refuses the stop, which
  alerts every bar. The adapter's limit price (`stop * 0.99`) is sent with
  up to ten decimals today; the fake accepts it, Binance's `PRICE_FILTER`
  would not. #1220 must round it before a real venue is wired.
- **Quantity after fees**: the stop sells the tracker's quantity; a fee
  paid in the base asset would make the exchange refuse it for balance.
- **Unknown status**: a stop placed with no answer that fills before the
  next listing adopts it is recorded as gone, not sold.
- **Synchronous calls**: each pass blocks the bar loop for its HTTP calls.
- **Dedicated account**: every stop-loss sell on a followed pair is treated
  as hermes's own and cancelled when it is not the wanted one.
- **Repeated alerts**: a stop that cannot be kept alerts on every bar until
  it can.
- **A stop above market is refused**: the position then relies on the
  strategy's exit, with a CRITICAL alert.

## Dependencies on other changes

- #971 (fake market spot endpoints): the scenarios run against them.
- #1220 wires a real venue into `hermes serve` and records exchange fills;
  until then `hermes serve` refuses `allow_mainnet`.
- #858 (closed): restart reconcile, here for the exchange's stops.
