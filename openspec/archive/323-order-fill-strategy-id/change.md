# Change: 323-order-fill-strategy-id

**SDD change**: `323-order-fill-strategy-id`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#323, follow-up of #308)

Mirror of engram topic key `sdd/323-order-fill-strategy-id/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`Order` and `Fill` have no strategy id; only `Lot` carries one (`order.py:44-69`, `fill.py:35-65`, `lot.py:68`). ADR-011 D-03
(line 93) says every order and fill carries the strategy that produced it, and the "What this forbids" list (line 573) rejects
an `Order`/`Fill` without one. This change adds a required `Order.strategy_id` and a read-only `Fill.strategy_id` derived from
its order, passes the router's id into the `Order` it builds, and makes the simulated exchange stamp lots from the fill instead
of from its own constructor field. No persisted schema changes: no table or event stores an `Order` or `Fill` today. Main risk:
the required field touches every `Order(...)` call site (3 in `src`/`scripts`, 11 test files), a mechanical but wide edit.

## Scope

### In

- `domain/trading/order.py`: required `strategy_id: StrategyId`.
- `domain/trading/fill.py`: `Fill.strategy_id` property returning `order.strategy_id`.
- Callers that build an `Order`: `application/order_router/router.py`, `adapters/exchanges/simulated.py`,
  `scripts/backtest/run_calibration.py`. `adapters/fills/orderbook_walk.py` builds a `Fill` from the order and needs no edit.
- `SimulatedExchangeAdapter`: drop its `strategy_id` field and the `"simulated-default"` sentinel; lots use `fill.strategy_id`.
- Tests: new domain tests, propagation tests, every existing fixture updated; `changelog.d/wave-order-fill-strategy-id.md`.

### Out

- A `strategy_id` column on the unused `trades` table, and `NOT NULL` enforcement in the ledger: do it with the first writer
  of fills or orders to the ledger (#322 consumes fills; a ledger fill writer is not scheduled). See D-3.
- `Position`, `Balance` partitioning, `Portfolio.strategy_pnl()` (rest of ADR-011 D-03).
- Checking `fill.strategy_id == snapshot.strategy_id` inside `PortfolioSnapshot.apply_fill` (follow-up candidate).
- #322 (tracker rebuild from ledger fills, still OPEN): not a prerequisite, only a consumer.

## Decisions

All decisions below: DECIDED by implementer, Code Owner reviews in PR.

### D-1: `Order.strategy_id` is required, with no default (DECIDED by implementer, Code Owner reviews in PR)

- **Decision**: `strategy_id: StrategyId` sits after `qty` and before the defaulted `limit_price`. Omitting it is a `TypeError`.
- **Alternatives**: a default such as `StrategyId("unassigned")` (silently attributes orders to a fake bucket, defeats D-03 and
  line 573); `StrategyId | None` (every consumer must handle `None`).
- **Rationale**: ADR-011 D-03 says it is set at submission; line 573 forbids an order without one. Order is not persisted, so
  there are no old rows to default for.
- **Cost of reversal**: LOW. Loosening to a default breaks nothing; tightening later would repeat this edit.

### D-2: `Fill.strategy_id` is derived from `order`, not stored (DECIDED by implementer, Code Owner reviews in PR)

- **Decision**: a `@property` returns `self.order.strategy_id`. `Fill(...)` takes no `strategy_id` argument.
- **Alternatives**: a stored field plus a `__post_init__` check that it equals the order's (two sources of one fact, every
  `Fill(...)` call site grows an argument, a mismatch is representable until validated).
- **Rationale**: D-03 says the fill's id is "copied from `Order` at fill time". A property is that copy with the invariant
  true by construction. `Fill` already holds the full `order` for traceability (`fill.py:44`).
- **Cost of reversal**: LOW. A stored field can replace the property; call sites that read `fill.strategy_id` do not change.

### D-3: No persisted schema or event payload changes (DECIDED by implementer, Code Owner reviews in PR)

- **Decision**: no migration. Existing rows are untouched.
- **Alternatives**: add `trades.strategy_id TEXT NOT NULL DEFAULT 'swing'` now (mirrors `0002`; no code writes `trades`, so the
  column would be dead and the default would invent attribution).
- **Rationale**: no table or event stores an `Order` or `Fill`: `trades` is in `0001_initial_schema.sql:17` but has no Python
  reader or writer, and no event in `domain/events/` references `Order` or `Fill` (checks under Design). The NOT NULL
  constraint of line 573 belongs to the change that adds the first writer.
- **Cost of reversal**: LOW. A later migration adds the column; ADR-006 §2 keeps migrations append-only.

### D-4: Lots take the strategy from the fill; the adapter loses its own `strategy_id` (DECIDED by implementer, Code Owner reviews in PR)

- **Decision**: `SimulatedExchangeAdapter._open_lot` passes `fill.strategy_id` to `Lot.open`. The `strategy_id` constructor field
  and `_DEFAULT_STRATEGY_ID` are removed. `place_market_order` copies `order.strategy_id` into the side-adjusted order it builds.
- **Alternatives**: keep the adapter field as a second source (an order from strategy A could open a lot labelled B);
  keep it as a fallback (resurrects a default for a field that is now required).
- **Rationale**: one source of truth, D-03. No `src` caller passed the field (the only constructor call sites are in tests);
  tests now put the id on the order.
- **Cost of reversal**: LOW.

## Requirements

### R-1: An order cannot exist without a strategy

- **A-1.1**: `uv run pytest tests/unit/domain/trading -q -k "without_strategy_id or keeps_the_strategy or differing_only"` exits 0.
- **A-1.2**: `rg -n "strategy_id: StrategyId$" src/hermes/domain/trading/order.py` prints one match with no `=` default.

### R-2: A fill's strategy is always its order's

- **A-2.1**: `uv run pytest tests/unit/domain/trading -q -m property` exits 0 (property: any id round-trips order to fill).
- **A-2.2**: `uv run pytest tests/unit/domain/trading -q -k "could_disagree"` exits 0 (`Fill` accepts no `strategy_id` argument).

### R-3: The id flows router to order, and order to fill and lot

- **A-3.1**: `uv run pytest tests/unit/application/order_router -q -k strategy_attribution` exits 0.
- **A-3.2**: `uv run pytest tests/unit/adapters -q -k "strategy_attribution or lot_strategy_id"` exits 0.
- **A-3.3**: `rg -n "simulated-default|_DEFAULT_STRATEGY_ID" src` exits 1.

### R-4: Nothing else moves

- **A-4.1**: `git diff --exit-code b02d38f -- src/hermes/adapters/ledger src/hermes/entrypoints src/hermes/domain/events` exits 0.
- **A-4.2**: `uv run lint-imports` exits 0 with 5 contracts kept.
- **A-4.3**: `uv run pytest tests/unit tests/integration -n auto -q` exits 0.

## Design

```python
# domain/trading/order.py
@dataclass(frozen=True, slots=True)
class Order:
    side: OrderSide
    pair: Pair
    qty: Quantity
    strategy_id: StrategyId  # required; before the defaulted limit_price
    limit_price: Decimal | None = None


# domain/trading/fill.py
@property
def strategy_id(self) -> StrategyId:
    return self.order.strategy_id
```

Edits by file:

| File | Edit |
| ---- | ---- |
| `application/order_router/router.py:339` | `strategy_id=inp.strategy_id` in the `Order(...)` |
| `adapters/exchanges/simulated.py:173` | copy `order.strategy_id` into `order_with_side` |
| `adapters/exchanges/simulated.py:389` | `Lot.open(strategy_id=fill.strategy_id)`; remove field and sentinel |
| `scripts/backtest/run_calibration.py:152` | `_synthesise_fill(..., strategy_id)`; main builds `StrategyId(f"swing-mvp1-phase1-{pair}")` and reuses it for the harness |
| tests | `_STRATEGY_ID = StrategyId("test-strategy")` per file; adapter fixtures lose the `strategy_id` kwarg |

Design-phase checks, run against `b02d38f` (output as observed):

```text
$ git grep -nE "\b(Order|Fill)\(" b02d38f -- src scripts    # constructor sites, class lines excluded
scripts/backtest/run_calibration.py:152          Order(
scripts/backtest/run_calibration.py:160          Fill(
src/hermes/adapters/exchanges/simulated.py:173   Order(
src/hermes/adapters/fills/orderbook_walk.py:178  Fill(     # takes the order as given, no edit
src/hermes/application/order_router/router.py:339  Order(
$ git grep -lE "\bOrder\(" b02d38f -- tests | wc -l
11
$ rg -n "CREATE TABLE" src/hermes/adapters/ledger/migrations/
0001_initial_schema.sql: trades (17), lots (34), lot_consumptions (48), events (63)
$ rg -n "trades" src --glob "*.py"        # no ledger code touches the trades table (hits are unrelated message fields)
```

## Test plan

1. R-1..R-4 acceptance commands pass.
2. New: `tests/unit/domain/trading/test_order_fill_strategy_id.py` (5 example tests, 1 hypothesis property), plus one
   propagation test each for the router, the simulated exchange (market and limit) and the orderbook-walk model.
3. Existing tests changed: `test_lot_strategy_id_set` asserted "Lot.strategy_id matches the adapter's strategy_id". That
   behaviour is gone by design (D-4); the test now asserts the lot takes the order's id. All other edits only add the new
   required argument.
4. `bash scripts/dev/pr-preflight.sh` green; domain coverage stays at or above 95%.

## Risks

- **Silent relabel in shadow mode**: a lot used to take the adapter's id; now it takes the order's. If the Phase-2 router or
  composition root builds orders with the wrong id, lots follow it. Mitigation: the router test pins `inp.strategy_id` to the
  order; nothing in `src` constructed the adapter with a strategy id before this change.
- **Two ids in the calibration script**: the synthesised fills now carry `swing-mvp1-phase1-<pair>`, the same string the harness
  already used as its report id, not `swing-donchian-4h`. Mitigation: they are one variable in `main`, so they cannot diverge.
- **Snapshot does not cross-check**: `PortfolioSnapshot.apply_fill` accepts a fill from any strategy. Listed in Out.

## Dependencies on other changes

- Follows #308 (introduced `StrategyId("swing-donchian-4h")` and the portfolio snapshot). Merged on main.
- #322 (tracker rebuild from ledger fills, still OPEN) is NOT a prerequisite; it consumes `fill.strategy_id` once it lands.
- No ADR edits. ADR-011 D-03 and line 573 are the authority; this change implements their Order/Fill part.
