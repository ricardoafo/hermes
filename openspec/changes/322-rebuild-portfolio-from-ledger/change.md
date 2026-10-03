# Change: 322-rebuild-portfolio-from-ledger

**SDD change**: `322-rebuild-portfolio-from-ledger`
**Date**: 2026-10-03
**Status**: Proposed
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/322-rebuild-portfolio-from-ledger/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes serve` builds its `PortfolioTracker` from the configured seed on every start, so once fills are recorded (#353)
a restart would reset cash and positions. This change rebuilds the tracker on startup by replaying the strategy's
`TradeExecuted` events from the ledger through the same `PortfolioSnapshot.after_fill` accounting the live loop uses
(ADR-003 §10, reconcile on startup). To attribute a fill to a strategy, `TradeExecuted` gains `strategy_id` (schema
version 2). A ledger that cannot be replayed stops startup instead of trading on wrong state. The main risk is that
the fill kernel in serve (#353) records fills in a different shape; D-1 fixes the shape it must write.

## Scope

### In

- `TradeExecuted` gains `strategy_id: StrategyId`; `__event_schema_version__` 1 → 2.
- `LedgerPort.trade_events(strategy_id)` and its `SqliteLedger` implementation: every `TradeExecuted` row of one
  strategy, oldest first.
- `PortfolioTracker.rebuilt(seed, trades)`: replays the trades over the seed and returns the tracker, or the first
  `DomainError`.
- `serve.compose` builds the tracker with `rebuilt`; a failure raises `StartupError` with the failing event.
- Unit tests for each piece; callers of `TradeExecuted` updated for the new field.

### Out

- Writing `TradeExecuted` from the live loop: #353 (fill kernel in `hermes serve`).
- Restoring marks: the first bar after start re-marks each held pair (`after_bar`), as it does today.
- Upcasting v1 `TradeExecuted` rows: none exist in any ledger (nothing writes fills yet), so the reader refuses them
  (D-3).
- Rebuilding the swing adapter's in-memory buffers: they warm up from the bar feed as today.

## Decisions

### D-1: `TradeExecuted` is the fill record the rebuild reads, extended with `strategy_id`

- **Decision**: Add `strategy_id` to `TradeExecuted` (schema v2) and replay those rows.
- **Alternatives**: A new `FillRecorded` event (two events for one fill, breaking `TradeExecuted` as the single
  source of truth for a trade, ADR-003 §00 I-06); rebuilding from the `lots` table (lots carry no proceeds or fees,
  so cash cannot be rebuilt).
- **Rationale**: `TradeExecuted` already carries pair, side, quantity, price and fee, which is exactly what
  `after_fill` reads. The tracker is per strategy (308 D-4), so the event must name its strategy, as `Order` and
  `Fill` do since #323.
- **Cost of reversal**: LOW. Nothing writes `TradeExecuted` in production yet.

### D-2: Replay builds a `Fill` from each event

- **Decision**: The tracker converts each event to a `Fill` whose order has the event's side, pair, quantity and
  strategy, `executed_qty` = quantity, `avg_price` = price, `fee` = fee, `timestamp` = `occurred_at`, then calls
  the existing `apply_fill`.
- **Alternatives**: A second accounting path on `PortfolioSnapshot` taking events (two code paths that can drift).
- **Rationale**: One accounting path for live and replay, the same principle as ADR-011's single fill kernel.
  `limit_price` does not affect accounting, so its absence from the event is harmless.
- **Cost of reversal**: LOW.

### D-3: Fail closed on an unreplayable ledger

- **Decision**: Any replay failure (unreadable payload, v1 row, currency mismatch, a sell without a position)
  stops `compose` with `StartupError` naming the event's correlation id. hermes does not start on the seed.
- **Alternatives**: Skip the bad row and log (positions silently wrong); fall back to the seed (the bug this issue
  exists to fix).
- **Rationale**: Wrong portfolio state feeds the risk overlay and position sizing. Refusing to start is the safe
  direction and surfaces through the systemd failure alert.
- **Cost of reversal**: LOW.

### D-4: Order of replay

- **Decision**: `ORDER BY occurred_at ASC, id ASC`.
- **Rationale**: Fill time first; the row id breaks ties in insert order, which is the order the live loop applied
  them.

## Requirements

### R-1: `TradeExecuted` names its strategy

- **A-1.1**: `TradeExecuted.__event_schema_version__ == 2` and the class has a required `strategy_id` field.
- **A-1.2**: `uv run pytest tests/unit/domain/events/test_trade_executed.py` passes.

### R-2: The ledger returns one strategy's trades in order

- **A-2.1**: A test writes trades of two strategies out of time order and `trade_events` returns only the asked
  strategy's, oldest first, with every field restored.
- **A-2.2**: A v1 row or an unreadable payload raises `LedgerError`.

### R-3: The tracker rebuilds from trades

- **A-3.1**: Replaying a BUY then a partial SELL gives the same snapshot as applying the same fills live.
- **A-3.2**: An empty trade list returns the seed unchanged.
- **A-3.3**: A SELL with no position returns `Failure`.
- **A-3.4**: A hypothesis property test: for any valid sequence of fills, the rebuilt tracker equals the live one.

### R-4: `hermes serve` starts from the rebuilt state

- **A-4.1**: `compose` against a ledger holding a BUY shows one open position and reduced cash.
- **A-4.2**: `compose` against a ledger holding a SELL with no prior BUY raises `StartupError`.

## Design

```text
src/hermes/domain/events/trade_executed.py   + strategy_id: StrategyId; schema version 2
src/hermes/ports/ledger.py                   + trade_events(strategy_id) -> tuple[TradeExecuted, ...]
src/hermes/adapters/ledger/sqlite_ledger.py  + trade_events, _trade_from_payload
src/hermes/application/portfolio/tracker.py  + PortfolioTracker.rebuilt(seed, trades)
src/hermes/entrypoints/serve.py              compose: tracker = rebuilt(...) or StartupError
```

```python
# ports/ledger.py
def trade_events(self, strategy_id: StrategyId) -> tuple[TradeExecuted, ...]: ...

# adapters/ledger/sqlite_ledger.py
"SELECT event_schema_version, payload_json FROM events "
"WHERE event_type = 'TradeExecuted' "
"  AND json_extract(payload_json, '$.strategy_id.value') = ? "
"ORDER BY occurred_at ASC, id ASC"

# application/portfolio/tracker.py
@classmethod
def rebuilt(
    cls, seed: PortfolioSnapshot, trades: Iterable[TradeExecuted]
) -> Result[PortfolioTracker, DomainError]: ...
```

`TradeSide` maps to `OrderSide` by value (BUY/SELL).

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the PR.
4. Code Owner review.

## Risks

- **#353 writes fills another way**: then nothing is rebuilt and restarts still reset state. Mitigation: D-1 fixes
  the contract; #353's spec must emit `TradeExecuted` v2 per fill, and its integration test should restart serve
  and assert the position survives.
- **Rows without a strategy**: a fill written by another path without `strategy_id` is never replayed. Mitigation:
  the field is required, so no such event can be constructed.
- **Replay cost**: linear in fills. At swing frequency (a few trades a month) this stays negligible for years.

## Dependencies on other changes

- #308 (tracker), #323 (`strategy_id` on `Order` and `Fill`), both merged.
- #353 consumes the D-1 contract.
