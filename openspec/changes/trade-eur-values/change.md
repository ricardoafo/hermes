# Change: trade-eur-values

**SDD change**: `trade-eur-values`
**Date**: 2026-10-03
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#386)

Mirror of engram topic key `sdd/trade-eur-values/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-002 §3 requires EUR values on every trade row from insert; ADR-016 items 3 and 5 say fees enter the FIFO values and the
EUR rate is the ECB EUR/USD rate with USDT read as USD. Today `trades` has no EUR columns, `SimulatedExchangeAdapter._open_lot`
labels the USDT fill price as the lot's EUR cost basis (`simulated.py:371-373`), and `LotConsumption.record` ignores fees
(`lot_consumption.py:112`). This change adds the EUR columns (migration 0003), a fill-path recorder that prices each fill
through `FxRateProviderPort.get_for_date` and stores `eur_status=pending` when the lookup fails, a `hermes fx backfill`
command, and a pure FIFO replay that derives every lot and consumption EUR value, fees included, from the trades. Strategy,
sizing, fee and kill code keep reading native USDT values. Main risk: the default Frankfurter URL answers 301 today (found
while recording fixtures), so the adapter fails every call until this change moves it to `https://api.frankfurter.dev/v1`.

## Scope

### In

- `adapters/ledger/migrations/0003_trade_eur_values.sql`: EUR columns on `trades`; `lots` and `lot_consumptions` rebuilt so
  EUR values may be NULL while pending, plus native-basis, link and fee columns.
- `domain/fx/eur_pricing.py`: `EurPricing` value object, `EurStatus`, `fill_date_madrid`, `rate_is_final`.
- `domain/lots/eur_fifo.py`: `acquisition_value`, `transmission_value`, `replay_fifo_eur` (pure, quantity FIFO per asset,
  exact EUR allocation).
- `domain/trading/trade_record.py`: `TradeRecord` (what one fill writes) and `TradeEur`.
- `domain/lots/lot.py`: `cost_basis_per_unit` may be `None` (pending); new `cost_basis_native_per_unit`.
- `domain/lots/lot_consumption.py`: `record` takes an optional EUR sell-fee share and subtracts it.
- `adapters/exchanges/simulated.py`: `_open_lot` stores the native basis (fee included) and no fake EUR basis.
- `adapters/fx/frankfurter.py`: default base URL `https://api.frankfurter.dev/v1`; optional `wire_codes` alias map
  (`USDT -> USD`). Same default in `infrastructure/config/models.py` and `config/frankfurter.yaml`.
- `ports/ledger.py`: new `TradeLedgerPort` (`record_trade`, `list_pending_trades`, `apply_trade_eur`).
- `adapters/ledger/sqlite_ledger.py`: implements `TradeLedgerPort`; re-projects the asset inside each write transaction.
- `application/ledger/`: `EurPricer` (per-day cache, failure -> `None`), `TradeRecorder`, `backfill_pending_eur`.
- `entrypoints/fx.py` + `cli.py`: `hermes fx backfill [--db PATH]`.
- `pyproject.toml`: import-linter contract `eur-values-are-measurement-only`.
- Tests (unit, property, recorded-fixture contract, byte-identical shadow test) and `changelog.d/wave-386-trade-eur-values.md`.

### Out

- **USDT as its own FIFO asset and the shadow start-capital USDT lot (ADR-016 item 4, issue scope bullet 5).** Follow-up
  issue to be filed. Reasons: (a) nothing in the ledger knows the shadow start capital or its start date (serve wires no
  exchange and writes no trades yet), so the start lot has no source; (b) a USDT leg on every swap doubles the trade-to-lot
  mapping (one fill opens one lot and consumes another asset's lots) and needs a rule for fees charged in the received asset.
  `replay_fifo_eur` is asset-generic and `lots.native_currency` is in place, so the follow-up adds legs, not schema.
- Wiring `TradeRecorder` into `hermes serve`: serve wires no exchange today (`serve.py:33`), so there is no fill to record.
  It is wired with the first exchange in the loop.
- Retiring the legacy `LedgerPort.open_lot` / `consume_lot` / `select_lots_fifo` path (no production caller; tests only).
- Tax estimates, scales, profile (#394, #395, #396).

## Decisions

All decisions below: DECIDED by implementer, Code Owner reviews in PR.

### D-1: ECB daily rate via Frankfurter, USDT read as USD

- **Decision**: price a fill with `fx.get_for_date(EUR, quote, fill_date)`; the Frankfurter adapter sends `USD` on the wire
  for `USDT` through an explicit `wire_codes` map set by the composition root. `eur_rate` is stored as the ECB quotes it
  (quote units per 1 EUR, e.g. `1.1721`); `price_eur = price / eur_rate`, `fee_eur = fee / eur_rate`. `fill_date` is the
  Europe/Madrid calendar date of the fill (ADR-016 item 2). EUR-quoted pairs use rate 1 with source `identity`.
- **Alternatives**: add `Currency.USD` (changes the trading allowlist and its exact-set test for a wire concern); map USDT to
  USD unconditionally in the adapter (changes what existing callers send); ask `from=USD&to=EUR` (Frankfurter returns a
  rounded inverse, not the ECB figure).
- **Rationale**: ADR-016 item 5; ECB publishes no EUR/USDT. Storing the ECB number verbatim keeps the row auditable.
- **Cost of reversal**: LOW. A different source swaps the adapter or the alias; rows keep `eur_source`.

### D-2: Pending and backfill, never blocking the fill

- **Decision**: the recorder runs after the fill. Any `InfraError` from the FX port, or a fee in a currency the rate does not
  cover, stores the trade with `eur_status='pending'` and NULL EUR columns. At fill time a rate counts only if its date equals
  the fill date (`rate_is_final`); a same-day lookup before the ECB publishes (~16:00 CET) returns the prior day and is stored
  pending. `hermes fx backfill` re-prices pending rows and accepts the closest prior business day once the fill date is before
  today (Madrid), then re-projects the asset.
- **Alternatives**: block or retry the fill path until FX answers (delays the shadow loop on a hobbyist API); accept the prior
  day at fill time (a morning fill would keep yesterday's rate forever).
- **Rationale**: issue "never block or delay a fill"; ADR-016 item 5.
- **Cost of reversal**: LOW.

### D-3: Native columns stay beside EUR columns

- **Decision**: `trades.price_amount`/`fee_amount` stay native; `lots` gains `cost_basis_native_per_unit` and
  `native_currency`; strategy, sizing, fee and kill code read only native values. An import-linter contract forbids
  `hermes.application.order_router`, `hermes.application.strategy_protocol` and `hermes.domain.strategy` from importing the
  EUR modules.
- **Alternatives**: EUR-only rows (loses the exchange-native audit trail); no contract (the guarantee rests on review).
- **Rationale**: shadow-test safety (issue); ADR-016 item 9 spirit.
- **Cost of reversal**: LOW.

### D-4: USDT as a FIFO asset — deferred

- **Decision**: recorded, not built here (see Scope/Out). Gains on the BTC leg are computed in EUR; the EUR/USD drift of the
  USDT cash in between is not yet a gain or loss.
- **Rationale**: no start-capital source exists yet; the follow-up adds legs to the same replay.
- **Cost of reversal**: n/a (additive follow-up).

### D-5: Fees in acquisition and transmission value, allocated exactly

- **Decision**: acquisition value of a lot = `price_eur * qty + buy_fee_eur`; transmission value of a sell =
  `price_eur * qty - sell_fee_eur` (ADR-016 item 3). A consumption takes a pro-rata share of its lot's acquisition value and of
  its sell's gross proceeds and fee; the piece that closes a lot (or ends a sell) takes the remainder, so sums are exact.
  `gain = proceeds - cost - fee`. Arithmetic runs at 50 significant digits; nothing is rounded before display.
- **Alternatives**: per-unit cost basis times quantity (sums drift by rounding); rounding to cents per row (sums drift).
- **Rationale**: IRPF art. 35; the property test needs exact equality.
- **Cost of reversal**: LOW.

### D-6: Lot and consumption EUR values are a projection of `trades`

- **Decision**: lots link their opening trade (`lots.trade_id`), consumptions their selling trade. On every `record_trade` and
  `apply_trade_eur` the adapter replays all trade-linked rows of the asset through `replay_fifo_eur` inside the same
  transaction: lot `remaining_qty` and EUR columns are updated, trade-linked consumption rows are deleted and re-inserted.
- **Alternatives**: compute EUR once at write time and patch rows during backfill (two code paths for one formula; patching a
  lot's cost must also patch every consumption of it).
- **Rationale**: one formula, backfill is "update the trade, replay"; a few dozen swing trades a year make a full per-asset
  replay cheap.
- **Cost of reversal**: MEDIUM (consumption row ids are not stable across replays; nothing references them today).

### D-7: A lot's EUR basis may be pending

- **Decision**: `Lot.cost_basis_per_unit: Money | None` (EUR, buy fee included; `None` while pending). `select_lots_fifo`
  returns `Failure(InvalidLot)` for a pending lot. The simulated adapter stores `None` plus the native basis, since it has no FX.
- **Alternatives**: keep the fake EUR basis (the bug); give the simulated adapter an FX port (it is an exchange stand-in, not
  the ledger).
- **Cost of reversal**: LOW.

## Requirements

### R-1: Schema

- **A-1.1**: after `SqliteLedger(conn)`, `PRAGMA table_info(trades)` lists `eur_rate`, `eur_rate_date`, `eur_source`,
  `price_eur`, `fee_eur`, `eur_status` (NOT NULL, default `pending`, CHECK `ok|pending`).
- **A-1.2**: `lots` lists `cost_basis_native_per_unit`, `native_currency`, `trade_id`, and `cost_basis_per_unit` is nullable.
- **A-1.3**: `lot_consumptions` lists `proceeds_eur`, `cost_eur`, `fee_eur`, `trade_id`, and `realized_gain_eur` is nullable.
- **A-1.4**: rows written before 0003 survive the migration unchanged; old `trades` rows read `eur_status='pending'`.

### R-2: Fill path writes EUR or pending

- **A-2.1**: a fill recorded with a working FX port stores `eur_status='ok'` and `price_eur == price / eur_rate`.
- **A-2.2**: an FX port that raises `InfraError` yields a stored row with `eur_status='pending'` and NULL EUR columns; the
  recorder does not raise.
- **A-2.3**: a lookup that resolves to a date before the fill date is stored pending.
- **A-2.4**: the pricer calls the FX port at most once per (currency, day) for final rates.

### R-3: Backfill

- **A-3.1**: `backfill_pending_eur` turns every pending row whose rate is final into `ok` and fills lot and consumption EUR
  values that depended on it.
- **A-3.2**: a failing FX port leaves rows pending and reports them; the call does not raise.
- **A-3.3**: `hermes fx backfill --db PATH` exits 0 and prints filled/pending counts.

### R-4: Gain includes fees

- **A-4.1**: property test: for any valid trade sequence, `sum(gain) == sum(proceeds) - sum(cost) - sum(fee)` over all
  consumptions, and when every lot is closed `sum(gain) == sum(sell gross - sell fee) - sum(buy gross + buy fee)`, exactly.
- **A-4.2**: worked example: buy 1 @ 100 EUR fee 1, sell 1 @ 110 EUR fee 1 -> gain 8.
- **A-4.3**: `LotConsumption.record` with a sell-fee share subtracts it from the gain.

### R-5: Strategy and sizing unchanged

- **A-5.1**: a scripted entry/exit run produces byte-identical signals, sizing results, fills and balances with FX available
  and with FX failing.
- **A-5.2**: `uv run lint-imports` keeps the new contract.

### R-6: Frankfurter contract with recorded fixtures

- **A-6.1**: recorded `api.frankfurter.dev/v1` responses (business day, weekend, unknown currency) replayed through `respx`
  give `FxRate(EUR, USDT, 1.1721, 2026-01-02)`, the prior business day, and `FxRateError`.

## Design

### Migration `0003_trade_eur_values.sql`

```sql
BEGIN;
ALTER TABLE trades ADD COLUMN eur_rate      TEXT;
ALTER TABLE trades ADD COLUMN eur_rate_date TEXT;
ALTER TABLE trades ADD COLUMN eur_source    TEXT;
ALTER TABLE trades ADD COLUMN price_eur     TEXT;
ALTER TABLE trades ADD COLUMN fee_eur       TEXT;
ALTER TABLE trades ADD COLUMN eur_status    TEXT NOT NULL DEFAULT 'pending'
    CHECK (eur_status IN ('ok', 'pending'));

CREATE TABLE lots_new (
    lot_id TEXT PRIMARY KEY, asset TEXT NOT NULL, original_qty TEXT NOT NULL, remaining_qty TEXT NOT NULL,
    cost_basis_per_unit TEXT,                 -- EUR incl. buy fee; NULL while pending
    opened_at TEXT NOT NULL, strategy_id TEXT NOT NULL DEFAULT 'swing',
    cost_basis_native_per_unit TEXT, native_currency TEXT,
    trade_id INTEGER REFERENCES trades(id));
INSERT INTO lots_new (lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, opened_at, strategy_id)
    SELECT lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, opened_at, strategy_id FROM lots;
DROP TABLE lots; ALTER TABLE lots_new RENAME TO lots;
-- same rebuild for lot_consumptions: realized_gain_eur nullable + proceeds_eur, cost_eur, fee_eur, trade_id
-- indexes recreated; idx_lots_trade_id, idx_lot_consumptions_trade_id added
COMMIT;
```

### Domain

```python
# domain/fx/eur_pricing.py
class EurStatus(StrEnum): OK = "ok"; PENDING = "pending"
@dataclass(frozen=True, slots=True)
class EurPricing:
    quote: Currency; rate: Decimal; rate_date: date; source: str   # rate = quote units per 1 EUR
    @classmethod
    def identity(cls, on: date) -> EurPricing: ...
    @classmethod
    def from_fx_rate(cls, fx: FxRate, *, source: str) -> EurPricing: ...  # fx.from == EUR
    def to_eur(self, amount: Money) -> Money: ...                          # amount.currency == quote
def fill_date_madrid(at: datetime) -> date: ...
def rate_is_final(*, fill_date: date, rate_date: date, today: date) -> bool:
    return rate_date == fill_date or fill_date < today

# domain/trading/trade_record.py
@dataclass(frozen=True, slots=True)
class TradeEur: pricing: EurPricing; price_eur: Decimal; fee_eur: Decimal
    @classmethod
    def price(cls, pricing, *, price: Money, fee: Money) -> TradeEur | None: ...  # None if fee currency not covered
@dataclass(frozen=True, slots=True)
class TradeRecord:
    correlation_id: str; pair: Pair; side: OrderSide; qty: Decimal; price: Decimal; fee: Money
    executed_at: datetime; strategy_id: StrategyId; lot_id: LotId | None; eur: TradeEur | None
    @property
    def eur_status(self) -> EurStatus: ...

# domain/lots/eur_fifo.py
def acquisition_value(*, price: Decimal, qty: Decimal, fee: Decimal) -> Decimal: ...
def transmission_value(*, price: Decimal, qty: Decimal, fee: Decimal) -> Decimal: ...
@dataclass(frozen=True, slots=True)
class EurTrade: ref: str; side: OrderSide; qty: Decimal; executed_at: datetime; price_eur: Decimal | None; fee_eur: Decimal | None
@dataclass(frozen=True, slots=True)
class LotEurValue: buy_ref: str; remaining_qty: Decimal; acquisition_eur: Decimal | None; cost_basis_per_unit_eur: Decimal | None
@dataclass(frozen=True, slots=True)
class ConsumptionEurValue:
    sell_ref: str; buy_ref: str; qty: Decimal; consumed_at: datetime
    proceeds_eur: Decimal | None; cost_eur: Decimal | None; fee_eur: Decimal | None; gain_eur: Decimal | None
@dataclass(frozen=True, slots=True)
class FifoEurProjection: lots: tuple[LotEurValue, ...]; consumptions: tuple[ConsumptionEurValue, ...]
def replay_fifo_eur(trades: Sequence[EurTrade]) -> Result[FifoEurProjection, DomainError]: ...
    # trades in execution order, one asset; a SELL beyond inventory -> Failure(InsufficientInventory)
```

### Ports and adapters

```python
# ports/ledger.py
@runtime_checkable
class TradeLedgerPort(Protocol):
    def record_trade(self, trade: TradeRecord, event: DomainEvent) -> int: ...
    def list_pending_trades(self) -> tuple[PendingTrade, ...]: ...     # PendingTrade: trade_id, executed_at, price, fee
    def apply_trade_eur(self, trade_id: int, eur: TradeEur) -> None: ...
```

`SqliteLedger.record_trade`: one transaction = insert trade row (EUR columns or NULL + status) -> BUY: insert lot row with
`trade_id`, native basis `acquisition_value(price, qty, fee) / qty`, `native_currency = quote` -> `_reproject(asset)` -> event
-> commit. `_reproject(asset)`: load `trades` of the asset ordered by `executed_at, id`, `replay_fifo_eur`, update lots by
`trade_id`, delete and re-insert trade-linked consumptions (`consumed_qty`, `realized_gain_eur`, EUR parts, `trade_id`, lot
id looked up by buy trade). `Failure` -> `LedgerError`, rolled back.

`FrankfurterConfig.wire_codes: tuple[tuple[Currency, str], ...] = ()` and `FrankfurterConfig.USDT_AS_USD`; `_get_json` and
`_parse_response` use the wire code for the query and the `rates` key, and stamp the domain currency on the `FxRate`.

### Application

```python
# application/ledger/eur_pricer.py
class EurPricer:
    def __init__(self, fx: FxRateProviderPort, *, source: str = "ecb:frankfurter") -> None: ...
    def price(self, quote: Currency, fill_date: date, *, today: date) -> EurPricing | None: ...
# application/ledger/trade_recorder.py
class TradeRecorder:
    def __init__(self, ledger: TradeLedgerPort, pricer: EurPricer) -> None: ...
    def record(self, fill: Fill, *, correlation_id: str) -> int: ...  # appends TradeExecuted in the same txn
# application/ledger/eur_backfill.py
@dataclass(frozen=True, slots=True)
class BackfillReport: filled: tuple[int, ...]; pending: tuple[int, ...]
def backfill_pending_eur(ledger: TradeLedgerPort, pricer: EurPricer, *, today: date) -> BackfillReport: ...
```

### Entry point

`hermes fx backfill [--db PATH]` (`entrypoints/fx.py`): opens the ledger at `--db` (default `serve.DEFAULT_DB_PATH`), builds
`FrankfurterAdapter.create(FrankfurterConfig(wire_codes=FrankfurterConfig.USDT_AS_USD))`, runs the backfill with today in
Madrid, prints `filled=N pending=M`, exits 0 (1 on `LedgerError`).

### Commands run against the current tree (2026-10-03)

- Schema today: `trades` has no EUR column; `lots.cost_basis_per_unit` and `lot_consumptions.realized_gain_eur` are NOT NULL;
  `PRAGMA foreign_keys` is 0; SQLite 3.53.1.
- `uv run lint-imports`: `Contracts: 5 kept, 0 broken.`
- `curl -sI https://api.frankfurter.app/2026-01-02?from=EUR&to=USD` -> `301`, `Location: https://api.frankfurter.dev/v1/...`.
  The adapter's `httpx.Client` does not follow redirects, so every call fails today with "non-JSON response".
- `curl https://api.frankfurter.dev/v1/2026-01-02?from=EUR&to=USD` -> `{"amount":1.0,"base":"EUR","date":"2026-01-02",
  "rates":{"USD":1.1721}}`; `/2026-01-03` (Saturday) -> same body dated `2026-01-02`; `to=USDT` -> `404 {"message":"not found"}`.
- No production code writes `trades`, `lots` or `lot_consumptions` (`rg "INSERT INTO trades" src` is empty; `open_lot` /
  `consume_lot` have test callers only).

## Test plan

1. Unit: `EurPricing`, `rate_is_final`, `fill_date_madrid`, `TradeEur.price`, `replay_fifo_eur` worked examples + hypothesis
   property (R-4), `Lot` pending basis, `LotConsumption.record` fee share, simulated `_open_lot`, migration columns and data
   survival, `SqliteLedger` trade path, `EurPricer` cache/failure, `TradeRecorder`, `backfill_pending_eur`, CLI.
2. Integration: recorded-fixture Frankfurter replay (R-6); byte-identical shadow run with and without FX (R-5).
3. `uv run pytest tests/unit -q`, touched integration tests, `uv run ruff check`, `uv run ruff format --check src tests`,
   `uv run mypy src`, `uv run lint-imports`.

## Risks

- **Frankfurter outage or move**: rows stay pending and `hermes fx backfill` fills them later; the 301 found here shows the
  host can move under us — the recorded-fixture test pins the response shape, not the host.
- **USDT/USD de-peg**: EUR values inherit the error of reading USDT as USD (ADR-016 Consequences); `eur_source` names the
  approximation so a later re-pricing can find the rows.
- **Existing VPS ledger rows**: no code writes `trades`/`lots` today, so none should exist; any row that does reads
  `pending` after 0003 and is backfilled by its fill date. Legacy `lots` rows without `trade_id` are never re-projected.
- **Same-day fills**: a fill before ~16:00 CET is pending until a backfill runs on a later day; until then its gains show as
  pending, not wrong.
- **Two lot paths**: the legacy `open_lot`/`consume_lot` API still writes unlinked rows; mixing it with `record_trade` for one
  asset would split FIFO. Only tests use it; retire it in a follow-up.

## Dependencies on other changes

- ADR-016 (#393) — rules followed here; not yet on `main`.
- Blocks #395 (tax estimates) and the EUR portfolio in the console.
- USDT-as-asset follow-up (D-4) — to be filed.
