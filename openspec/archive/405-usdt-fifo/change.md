# Change: 405-usdt-fifo

**SDD change**: `405-usdt-fifo`
**Date**: 2026-10-04
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#405)

Mirror of engram topic key `sdd/405-usdt-fifo/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-016 item 4 makes USDT its own FIFO asset: buying BTC with USDT disposes of USDT, selling BTC for USDT acquires
USDT, and the shadow start capital opens one USDT lot at the start-date rate. #386 (trade-eur-values D-4) deferred
this. This change records the start capital once in the ledger, derives the quote-currency leg of every trade, and
replays it through the existing asset-generic `replay_fifo_eur`, so EUR/USD drift on held USDT becomes a gain or loss.
Strategy, sizing, signals and fills are untouched; the byte-identical shadow-safety test stays green. Main risk: the
quote leg changes realized EUR gains on the ledger, so the two decisions below (D-1, D-2) are flagged for the Code
Owner's review.

## Scope

### In

- Migration `0006_capital_seeds.sql` (additive): `capital_seeds` table; each seed row links its lot
  (`capital_seeds.lot_id`). The link sits on the seed row, not on `lots`, so a later rebuild of `lots`
  (the pending destructive 0005 copies an explicit column list) cannot drop it.
- Domain: `ShadowCapitalSeeded` event; `CapitalSeed` value object; `seed_timestamp(earliest, now)`; pure
  `quote_leg_trades(...)` deriving the quote asset's `EurTrade` sequence from a seed and the trades.
- `TradeEur.price` accepts a fee in the base currency (D-2).
- `SqliteLedger`: `record_capital_seed`, `capital_seed`, `earliest_activity`, quote-leg lot rows and re-projection;
  base-asset consumptions keyed by `consumed_asset` so the two legs never delete each other's rows.
- `backfill_pending_eur` also prices a pending seed.
- `hermes serve` writes the seed once at start when the ledger has none (D-1), always EUR-pending; `hermes fx backfill`
  prices it like a pending trade.
- Property test over all assets (R-3) and unit tests.

### Out

- Fees in a third asset (e.g. BNB): rejected with a clear `LedgerError` once quote-leg tracking is active; nothing holds
  BNB in MVP1.
- Fees in the base currency on a SELL: rejected the same way (not a received-asset fee).

## Decisions

### D-1: The start capital is a one-time ledger event, dated by the ledger's earliest activity

- **Decision**: Serve writes `ShadowCapitalSeeded` (strategy, amount = `capital_budget` in the strategy's quote,
  `seeded_at`) and a `capital_seeds` row once, when the ledger has no seed for the strategy. `seeded_at` is the
  earliest `occurred_at` of any `BarProcessed`, `SignalEmitted` or `TradeExecuted` event already in the ledger, or the
  clock's now for a ledger with none of them. The seed opens one USDT lot of the full amount. Its EUR value is always
  written pending and priced by `hermes fx backfill` at the ECB rate of the seed's Madrid date (ADR-016 item 5).
- **Alternatives**: A `shadow_start_date` key in `config/strategies.yaml` (drifts if the file is edited; a config
  change invalidates the run per pre-registration §2). Seeding at "now" on first start (would stamp the next restart,
  not the real start: the VPS shadow has run since 2026-10-03 11:45 UTC).
- **Rationale**: Immutable and auditable; the date comes from evidence already in the ledger.
- **Cost of reversal**: MEDIUM — the seed row and event would need a corrective migration.
- **Flagged for the Code Owner.**

### D-2: A fee charged in the received asset reduces the quantity, not the EUR cost

- **Decision**: On a BUY whose fee is in the base asset, the lot quantity is `qty - fee` and its acquisition value
  is still `price_eur × qty` (the fee's EUR value is added back as `fee_eur`, so EUR cost is what was paid). On a
  SELL the received asset is the quote; its USDT lot is `price × qty - fee` at the fill rate. A fee in any other
  asset is rejected while quote-leg tracking is active.
- **Alternatives**: Treat the base fee as a separate disposal (extra FIFO consumption for a fraction of a unit).
- **Rationale**: Matches how the venue delivers the asset (net quantity) and keeps EUR value conserved (R-3).
- **Cost of reversal**: LOW.
- **Flagged for the Code Owner.**

### D-3: Quote-leg tracking starts with the seed

- **Decision**: The quote asset is projected only when a seed exists for it. Ledgers without a seed (tests, legacy
  rows) behave exactly as today.
- **Rationale**: Without the seed the first BUY would consume USDT nobody recorded, an `InsufficientInventory` failure.
- **Cost of reversal**: LOW.

### D-4: Quote-leg valuation

- **Decision**: For a trade with EUR rate `r` (quote units per EUR), one quote unit is worth `1/r` EUR. BUY of base:
  disposes `price × qty + quote_fee` quote units, transmission value at `1/r`, no fee (already in the base
  acquisition). SELL of base: acquires `price × qty - quote_fee` quote units at `1/r`, no fee (already in the base
  transmission). Seed: acquires `amount` at `1/r_seed`. A pending trade or seed leaves its leg's values `None`.
- **Rationale**: EUR value moves between legs exactly, so total basis changes only by realized gains (R-3).

## Requirements

### R-1: Seed recorded once, dated by evidence

- **A-1.1**: Serve start on a ledger with `BarProcessed` rows and no seed writes one seed dated at the earliest such
  row; a second start writes nothing.
- **A-1.2**: A ledger with no events gets a seed dated at the clock's now.

### R-2: Quote legs

- **A-2.1**: With a seed, a BTC/USDT BUY consumes USDT lots FIFO; a SELL opens a USDT lot; consumptions carry
  `consumed_asset = 'USDT'`; base-asset rows are unchanged by the quote re-projection and vice versa.
- **A-2.2**: Without a seed, nothing changes (existing tests unchanged).

### R-3: Conservation property

- **A-3.1**: Hypothesis, any sequence of priced BUY/SELL trades with quote fees (and base fees on BUY) after a seed:
  sum of realized EUR gains over all assets == sum of remaining lots' EUR acquisition values over all assets - seed EUR
  value, within 1e-18.

### R-4: Shadow safety

- **A-4.1**: `tests/integration/test_eur_values_shadow_safety.py` passes unchanged.

## Design

```text
src/hermes/adapters/ledger/migrations/0006_capital_seeds.sql   new (additive)
src/hermes/domain/events/shadow_capital_seeded.py              new
src/hermes/domain/lots/quote_leg.py                            new: CapitalSeed, quote_leg_trades, seed_timestamp
src/hermes/domain/trading/trade_record.py                      TradeEur.price(..., base=None)
src/hermes/ports/ledger.py                                     TradeLedgerPort: record_capital_seed, capital_seed,
                                                               earliest_activity, list_pending_seeds, apply_seed_eur
src/hermes/adapters/ledger/sqlite_ledger.py                    quote-leg rows + _reproject_quote
src/hermes/application/ledger/eur_backfill.py                  prices pending seeds
src/hermes/entrypoints/serve.py                                seed once at start
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

## Risks

- **Realized gains change once a seed exists.** Intended (ADR-016 item 4); D-1 and D-2 flagged for review.
- **Seed EUR stays pending until `hermes fx backfill` runs** for the seed date; quote-leg values stay `None` until then.

## Dependencies on other changes

- #386 (trade-eur-values, merged) for `replay_fifo_eur`, `TradeEur`, the backfill and the `hermes fx` command.
