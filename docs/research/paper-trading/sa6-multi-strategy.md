# SA-6 Multi-Strategy Portfolio Management (MVP2-Preparatory)

**Date**: 2026-08-24
**Author**: SA-6 sub-agent
**Engram topic key**: `research/paper-trading-sa6-multi-strategy`
**Status**: FINAL

---

## 1. Executive Summary

Hermes MVP1 ships a single Swing strategy. MVP2 adds a Portfolio Balancer. The critical architectural risk is designing MVP1 domain aggregates in a way that forces a rewrite when the Balancer arrives. This document synthesises what production-grade multi-strategy engines actually do so that the MVP1 model carries the right fields from day one.

**The three things MVP1 domain model MUST have for MVP2 to plug in as a pure addition:**

1. **`strategy_id` on every domain aggregate that carries state**: `Position`, `Balance` (per-strategy slice), `Order`, and every event derived from them (`PositionOpened`, `PositionChanged`, `PositionClosed`, `OrderFilled`). NautilusTrader's Rust core embeds `strategy_id: StrategyId` directly in `Position` (see `crates/model/src/position.rs`, line 66) and in every derived event (`crates/model/src/events/position/opened.rs`). Without this, MVP2 cannot attribute PnL per strategy without a breaking schema migration.

2. **An `OmsType` (Order Management System type) concept per strategy**: NautilusTrader's `ExecutionEngine` carries `oms_overrides: HashMap<StrategyId, OmsType>` (see `crates/execution/src/engine/mod.rs`). This controls whether two strategies that hold the same instrument net their positions (NETTING) or keep them separately accounted (HEDGING). If hermes hard-codes one OMS behaviour at engine level, the Balancer and Swing strategy cannot coexist on the same instrument without corrupt position accounting.

3. **Capital allocation envelope per strategy**: Every system that supports shared capital (LEAN, NautilusTrader, Freqtrade) exposes either a weight (LEAN `PortfolioConstructionModel.DetermineTargetPercent`), a fixed capital slice (Freqtrade `capital_per_trade`), or a budget envelope tracked in the Cache. Without a `capital_budget` or `max_exposure` per `strategy_id`, MVP2's Balancer has no safe way to constrain the Swing strategy's capital usage when making rebalancing decisions.

**Bottom line**: add `strategy_id` to `Position`, `Order`, and all events now. Assign a `capital_budget` or `max_notional` limit per strategy in a config object now. Keep the `Portfolio` aggregate flat — track `unrealized_pnl` and `net_position` per instrument globally; track realized PnL per `(strategy_id, instrument_id)` for attribution. Do not attempt to make the Portfolio aggregate self-balance — that is the Balancer's job in MVP2.

---

## 2. Pattern Comparison

### 2.1 Allocator Pattern (LEAN)

**How it works**: A central `PortfolioConstructionModel` receives `Insight` objects from N independent `AlphaModel` instances. Each `Insight` carries a `SourceModel` string (see `Common/Algorithm/Framework/Alphas/Insight.cs`) identifying which alpha model generated it. The PCM aggregates all insights and computes a single set of `PortfolioTarget` weights. The `RiskManagementModel` then filters those targets. Finally the `ExecutionModel` turns targets into orders.

**Attribution chain**: `Insight.SourceModel` (string) → `Insight.Id` (GUID) → `PortfolioTarget` → `Order` → `Fill`. The attribution is carried at the Insight level, not at the Position level. Positions in LEAN are instrument-scoped, not strategy-scoped.

**Capital arbitration**: Implicit — the PCM's weight function (`DetermineTargetPercent`) produces a single weight per instrument that synthesises all alpha signals. `EqualWeightingPortfolioConstructionModel` gives each instrument with a non-flat signal `1/n` of the portfolio. `MeanVarianceOptimizationPortfolioConstructionModel` and `BlackLittermanOptimizationPortfolioConstructionModel` compute an optimal weight across all insights collectively.

**Strength**: Clean separation of concerns. Each alpha is ignorant of capital.
**Weakness**: All alpha models must produce `Insight` objects. Signal-driven (entry/exit) strategies and weight-based (allocator) strategies have fundamentally different output shapes — forcing them through the same `Insight` interface requires adapter layers.
**Verdict for hermes**: Useful reference for the Balancer's weight-computation logic. Not a good fit as the primary model because Swing is event-driven, not insight-emitting.

### 2.2 Sub-Portfolio Pattern (NautilusTrader)

**How it works**: Multiple strategy actors are registered against a single `Portfolio` + `Cache` + `ExecutionEngine`. Every `Order` and `Position` carries `strategy_id` as a first-class field. The `ExecutionEngine` contains `oms_overrides: HashMap<StrategyId, OmsType>` to control per-strategy netting. The `RiskEngine` enforces `max_notional_per_order: AHashMap<InstrumentId, Decimal>` as a cross-strategy instrument-level cap. Strategies are fully isolated actors; they cannot read each other's state directly, only through the shared `Cache`.

**Attribution chain**: `OrderFilled.strategy_id` → `Position.strategy_id` → `PositionOpened.strategy_id` → `Portfolio.realized_pnls` (keyed by `InstrumentId`, NOT by `StrategyId` — this is a gap; per-strategy PnL requires a cache query filtering by `strategy_id`).

**Capital arbitration**: Implicit — no explicit capital reservation per strategy. Strategies submit orders; the RiskEngine blocks orders that exceed the `max_notional_per_order` cap. There is no `capital_budget` per strategy. Capital starvation is possible if one strategy locks all buying power with open orders.

**Strength**: Cleanest strategy isolation. `strategy_id` propagates through the entire event chain. Hot-swap is supported (strategies can be added/removed from the trading node).
**Weakness**: No explicit capital reservation per strategy. `Portfolio.unrealized_pnls` is keyed by `InstrumentId` (see `crates/portfolio/src/portfolio.rs` — `unrealized_pnls: IndexMap<InstrumentId, Money>`), not by `(strategy_id, InstrumentId)`. This means the Portfolio cannot natively compute "how much unrealized PnL is attributable to the Swing strategy specifically" without post-processing the fill event log.
**Verdict for hermes**: Closest match. Use this pattern. The `strategy_id` propagation model is exactly right. Add explicit `capital_budget` per strategy that NautilusTrader lacks.

### 2.3 Capital-per-Strategy Pattern (Freqtrade, Jesse)

**How it works**: Each strategy receives a fixed capital slice (`capital_per_trade` × `max_open_trades`). There is no cross-strategy rebalancing. Capital gets stranded if one strategy is not in positions.

**Verdict for hermes**: Too rigid for a Swing + Balancer pairing. If Swing is in a drawdown period and holds no positions, its capital slice sits idle. The Balancer cannot use it.

### 2.4 Allocation Tree Pattern (bt, pmorissette)

**How it works**: A composable `AlgoStack` tree: `SelectAll` → `WeighEqually` / `WeighTarget` / `WeighMeanVar` → `Rebalance`. The `Rebalance` algo reads `target.temp['weights']` and calls `target.allocate(weight, child)` for each child. Children are `StrategyBase` nodes. Each `StrategyBase` has its own `value` (capital), `positions` (child securities), and tracks its own equity curve.

```python
# bt algos.py: WeighEqually.__call__
selected = target.temp["selected"]
n = len(selected)
w = 1.0 / n
target.temp["weights"] = {x: w for x in selected}

# bt algos.py: Rebalance.__call__
# reads target.temp['weights'], calls target.allocate() per child
# de-allocates children not in target weights
```

**Capital arbitration**: The parent strategy owns total capital and distributes to children via `allocate()`. Children cannot exceed their allocated slice. This is the cleanest model for a top-level Balancer.

**Verdict for hermes**: The `bt` tree structure is the correct mental model for the MVP2 Balancer. The Balancer is a parent node that holds total capital and redistributes weight between Swing sub-strategy and any other future sub-strategies. This maps cleanly onto a Go interface: `Allocator` receives `StrategyState[]` and returns `AllocationMap{strategy_id → weight}`.

### 2.5 Manual / External Pattern

Multiple independent processes, each with their own capital. No shared state. Capital rebalancing is an ops problem, not an engineering one (Freqtrade issue #8449). Not viable for hermes.

---

## 3. LEAN Pipeline Deep-Dive

### 3.1 Flow: Alpha Signal → Portfolio Construction → Risk → Execution

```text
AlphaModel(s)
   └── Generate(data) → []Insight{SourceModel, Symbol, Direction, Magnitude, Confidence}
         │
PortfolioConstructionModel
   └── CreateTargets(insights) → []PortfolioTarget{Symbol, Percent}
         │  DetermineTargetPercent(activeInsights):
         │    EqualWeight: weight = 1/n per non-flat insight
         │    MeanVariance: returns matrix → optimizer.Optimize() → weights
         │    BlackLitterman: groups insights by SourceModel, builds P/Q view matrices,
         │                    runs BL formula, optimizer → weights
         │
RiskManagementModel
   └── ManageRisk(targets) → []PortfolioTarget (filtered/zeroed)
         │  MaximumDrawdownPercentPerSecurity: yields PortfolioTarget(qty=0) + Insights.Cancel()
         │  when UnrealizedProfitPercent < -threshold
         │
ExecutionModel
   └── Execute(targets) → Orders submitted to broker
```

### 3.2 Key Code Citations

**Insight with SourceModel** (`Common/Algorithm/Framework/Alphas/Insight.cs`):

```csharp
public string SourceModel { get; internal set; }  // alpha model identifier
public Guid Id { get; }                            // unique insight GUID
public Symbol Symbol { get; }
public InsightDirection Direction { get; }
public double? Magnitude { get; }
public double? Confidence { get; }
public double? Weight { get; }
```

**BlackLitterman multi-model view construction** (`Algorithm.Framework/Portfolio/BlackLittermanOptimizationPortfolioConstructionModel.cs`):

```csharp
// Groups insights by SourceModel
// Sums directional magnitudes per model
// Normalizes: upInsightsSum > dnInsightsSum → positive view strength
// Pads missing symbols with zeros across all models
// Runs posterior blending: BL formula reconciles conflicting model views
```

**EqualWeighting** (`Algorithm.Framework/Portfolio/EqualWeightingPortfolioConstructionModel.cs`):

```csharp
protected override Dictionary<Insight, double> DetermineTargetPercent(
    List<Insight> activeInsights)
{
    // Filter by portfolio bias (LongShort/Long/Short)
    // weight = 1 / count(non-flat insights)
    // return insight → weight * direction
}
```

### 3.3 What hermes can borrow from LEAN

- The `Insight.SourceModel` string pattern → `Order.strategy_id` in hermes (orders carry origin)
- The `PortfolioTarget{Percent}` abstraction → `AllocationTarget{strategy_id, weight_pct}` for the MVP2 Balancer
- The `RiskManagementModel.ManageRisk()` pipeline stage → a Go `RiskFilter` interface that the Balancer calls before committing rebalance orders

---

## 4. NautilusTrader Sub-Portfolio Deep-Dive

### 4.1 Strategy Attribution Chain (confirmed from Rust source)

**`Position` struct** (`crates/model/src/position.rs`, lines 66–98):

```rust
pub struct Position {
    pub trader_id: TraderId,
    pub strategy_id: StrategyId,          // ← first-class field
    pub instrument_id: InstrumentId,
    pub id: PositionId,
    pub account_id: AccountId,
    pub opening_order_id: ClientOrderId,  // ← traces to originating order
    pub closing_order_id: Option<ClientOrderId>,
    pub entry: OrderSide,
    pub side: PositionSide,
    pub signed_qty: f64,
    pub quantity: Quantity,
    pub avg_px_open: f64,
    pub avg_px_close: Option<f64>,
    pub realized_return: f64,
    pub realized_pnl: Option<Money>,
    pub commissions: IndexMap<Currency, Money>,
    // ... instrument metadata fields
}
```

**`PositionOpened` event** (`crates/model/src/events/position/opened.rs`):

```rust
pub struct PositionOpened {
    pub trader_id: TraderId,
    pub strategy_id: StrategyId,   // ← propagated from Position
    pub instrument_id: InstrumentId,
    pub position_id: PositionId,
    pub account_id: AccountId,
    pub opening_order_id: ClientOrderId,
    // ...
}
```

The `strategy_id` propagates from `OrderFilled.strategy_id` → `Position::new(instrument, fill)` constructor → `PositionOpened::create(position, fill, ...)`.

### 4.2 ExecutionEngine OMS Override

**`ExecutionEngine`** (`crates/execution/src/engine/mod.rs`):

```rust
pub struct ExecutionEngine {
    oms_overrides: HashMap<StrategyId, OmsType>,   // ← per-strategy netting mode
    external_order_claims: HashMap<InstrumentId, StrategyId>,
    pos_id_generator: PositionIdGenerator,
    // ...
}
```

`OmsType` controls whether fills for a given `strategy_id` on a given instrument open a new independent position (HEDGING) or net into the existing position (NETTING). This is the mechanism that lets two strategies hold positions in the same instrument independently.

### 4.3 Portfolio Aggregate (shared, instrument-keyed)

**`PortfolioState`** (`crates/portfolio/src/portfolio.rs`):

```rust
struct PortfolioState {
    unrealized_pnls: IndexMap<InstrumentId, Money>,   // ← instrument-scoped only
    realized_pnls: IndexMap<InstrumentId, Money>,     // ← instrument-scoped only
    net_positions: IndexMap<InstrumentId, Decimal>,   // ← net across all strategies
    // snapshot tracking per PositionId
    snapshot_sum_per_position: AHashMap<PositionId, Money>,
    // ...
}
```

**Critical gap**: `unrealized_pnls` and `realized_pnls` are keyed by `InstrumentId` only — not by `(StrategyId, InstrumentId)`. The Portfolio does not natively answer "what is Swing strategy's total unrealized PnL?". That requires querying the Cache for positions filtered by `strategy_id` and summing. This is the key design gap in NautilusTrader that hermes can improve on.

### 4.4 RiskEngine Pre-Trade Checks

**`RiskEngine`** (`crates/risk/src/engine/mod.rs`):

```rust
pub struct RiskEngine {
    max_notional_per_order: AHashMap<InstrumentId, Decimal>,  // ← instrument-level cap
    trading_state: TradingState,                               // ← global halt switch
    portfolio: Portfolio,
    // throttled_submit: rate limiter
}
```

Pre-trade flow: `handle_submit_order` → `check_order(instrument, order)` → `check_orders_risk(instrument, [order])` → `execution_gateway`. The `max_notional_per_order` check is per-instrument globally — not per-strategy. There is no concept of "Strategy A has used 40% of its budget, deny this order".

**Implication for hermes**: The RiskEngine needs a `capital_budget: HashMap<StrategyId, Money>` and a `current_exposure: HashMap<StrategyId, Money>` to implement proper per-strategy capital limits. NautilusTrader punts this to the strategy itself via `PositionIdGenerator` count checking.

### 4.5 Hot-Swap and Multi-Strategy Coexistence

NautilusTrader strategies are actor-style: each receives its own event subscription and manages its own state. Adding a new strategy at runtime is a `node.add_actor(new_strategy)` call — no existing strategy is disrupted. Removing a strategy requires the strategy to close its own positions first (by convention, not enforcement). There is no forced liquidation on deregistration.

---

## 5. VectorBT Array-Accounting Deep-Dive

VectorBT is a vectorized (non-event-driven) engine. Its multi-strategy accounting model is instructive for analytics, not for execution.

### 5.1 Array Dimensions as Strategy Identity

Each strategy is a **column** in a 2-D array. Time is the row axis. A portfolio of N strategies over T bars is a `(T, N)` array of prices, signals, and positions.

```python
# from_signals factory
Portfolio.from_signals(
    close,  # (T, N) price array
    entries,  # (T, N) boolean signal array
    exits,  # (T, N) boolean signal array
    init_cash=10000,  # scalar (replicated per column) or (N,) array
    cash_sharing=False,  # False = isolated capital per column
    group_by=None,  # None = no capital sharing
)
```

### 5.2 Cash Sharing vs. Isolated Capital

When `cash_sharing=False` (default): each column has its own cash balance and position. PnL is column-independent. This is the standard multi-strategy comparison mode.

When `cash_sharing=True` with `group_by`: columns within a group share a single cash pool. Orders in column A consume cash that column B also competes for. This is the array equivalent of the sub-portfolio pattern. The framework auto-sets `group_by=True` if `cash_sharing=True` and no group is specified.

### 5.3 Per-Strategy PnL Attribution

```python
pf.total_profit(group_by=False)  # Series indexed by column (strategy)
pf.returns(group_by=False)  # (T, N) returns array per strategy
pf.stats(agg_func=None)  # DataFrame of stats per strategy
```

The `group_by` parameter is the key: set to `False` to get per-column metrics; pass a grouping Series to aggregate sub-groups.

### 5.4 Trade Attribution

Each trade is recorded as an `OrderRecord` (numpy structured array) with fields: `col` (column index = strategy), `idx` (bar index), `size`, `price`, `fees`, `side`. The `col` field is the strategy attribution mechanism — equivalent to `strategy_id` in the event-driven world.

### 5.5 What hermes can borrow from VectorBT

- The `(strategy_id, instrument_id)` composite key pattern for PnL attribution — mirror this in the analytics layer even if the execution engine uses event-driven accounting
- The `cash_sharing` flag concept → maps to "shared pool with budget caps" in hermes
- The `group_by` pattern → when the Balancer queries portfolio analytics, it needs the ability to aggregate per-strategy OR total — design the analytics API with this in mind from the start

---

## 6. bt Allocation-Tree Deep-Dive

### 6.1 Algo Tree Structure

`bt` is a composable pipeline of `Algo` objects arranged in an `AlgoStack`. Each `Algo` reads and writes `target.temp` (a shared scratchpad) and returns `True` to continue the stack or `False` to abort.

```text
Strategy(algos=[
    RunMonthly(),         # gate: only execute on month change
    SelectAll(),          # sets temp['selected'] = [all securities]
    SelectMomentum(n=5),  # filters temp['selected'] to top 5 by return
    WeighEqually(),       # sets temp['weights'] = {ticker: 0.2 for each}
    Rebalance(),          # executes allocate() calls to match weights
])
```

### 6.2 WeighEqually (from `bt/algos.py`)

```python
class WeighEqually(Algo):
    def __call__(self, target):
        selected = target.temp["selected"]
        n = len(selected)
        w = 1.0 / n if n > 0 else 0
        target.temp["weights"] = {x: w for x in selected}
        return True
```

### 6.3 Rebalance (from `bt/algos.py`)

The `Rebalance` algo reads `target.temp['weights']` and calls `target.allocate(weight, child)` for each child in the target set. It also **de-allocates** children not in the target weights (closes positions). This is the mechanism that ensures capital is reclaimed from strategies no longer in the allocation.

### 6.4 Nested Strategy Trees

The critical `bt` insight for hermes MVP2: strategies can be **nested**. A parent strategy holds total capital and allocates to child `StrategyBase` nodes. Each child runs its own `AlgoStack`. This is the exact model for:

```text
PortfolioBalancer (parent, RunMonthly)
├── SwingStrategy  (child, event-driven)
└── MomentumStrategy (child, future MVP3)
```

The parent Balancer only handles allocation percentages. The child Swing strategy handles entry/exit signals. The two concerns never cross.

### 6.5 What hermes can borrow from bt

- The `temp` scratchpad pattern → pass `AllocationContext{available_capital, current_positions, market_data}` to the Balancer's computation function without polluting the strategy actor's state
- The `Rebalance` de-allocation mechanic → when the Balancer reduces Swing's weight, it generates close orders for excess positions; this must be an explicit step, not a side effect
- The `RunMonthly/RunWeekly` gate algos → the Balancer needs a `RebalanceTrigger` concept (time-based, drift-based, or signal-based) that is separate from the Swing strategy's signal generation

---

## 7. Cross-Cutting Design Questions

### Q1: Order Origination Attribution — How do systems track strategy → fill → position → PnL?

**NautilusTrader (definitive)**: `OrderFilled.strategy_id` is set at order submission time (from the strategy actor's `StrategyId`). `Position::new(instrument, fill)` constructor sets `position.strategy_id = fill.strategy_id`. `PositionOpened::create(position, fill)` propagates `strategy_id`. The chain is: `strategy actor → SubmitOrder{strategy_id} → OrderFilled{strategy_id} → Position{strategy_id} → PositionOpened{strategy_id}`. Every event in the chain carries the attribution.

**LEAN**: Attribution at the `Insight.SourceModel` level (string). Positions in LEAN are not strategy-attributed — the PCM synthesises all signals into a single position per instrument.

**bt**: Attribution is implicit — each `StrategyBase` node owns its own positions within its subtree. There is no cross-strategy position attribution because strategies don't share instruments by default.

**Conclusion for hermes**: Follow NautilusTrader. `strategy_id` must be on `Order`, `Fill/Trade`, `Position`, and all derived events. It should be set at order creation and flow forward immutably.

### Q2: Shared vs. Partitioned Capital — API contract when strategies must arbitrate?

**NautilusTrader**: No explicit arbitration. The RiskEngine's `max_notional_per_order` is a global per-instrument cap. Strategies compete implicitly — first-come-first-served by submission order. If both strategies submit simultaneously and together would exceed available balance, the second order gets denied.

**LEAN Allocator**: Explicit arbitration — the PCM runs once and allocates weights. No two strategies can independently request capital; they contribute views and the PCM decides.

**bt**: Explicit arbitration — parent Balancer controls child capital via `allocate()`. Children cannot exceed their allocation.

**Conclusion for hermes**: Implement a `CapitalBudget` per strategy (explicit envelope). When a strategy submits an order, the RiskEngine checks: `current_exposure(strategy_id) + new_order_notional <= capital_budget(strategy_id)`. If the Balancer reduces a strategy's budget, it must also enforce position liquidation. This is a synchronous, deterministic arbitration — not first-come-first-served.

### Q3: Rebalancing Frequency — Periodic vs. Drift-Threshold vs. Event-Driven?

**Periodic (bt's RunMonthly/RunWeekly/RunDaily)**: Simple. Predictable transaction costs (one rebalance event per period). Ignores intraperiod drift. Best for low-turnover allocation strategies.

**Drift threshold (industry standard for target-weight rebalancing)**: Rebalance when `|current_weight - target_weight| > threshold` (e.g., 5%). Fewer transactions than periodic in stable markets. Requires continuous position monitoring. More complex to implement.

**Event-driven (on new alpha signal)**: The Balancer rebalances when the Swing strategy emits a signal that changes its expected capital consumption. Most responsive but highest transaction cost risk.

**Recommendation for hermes MVP2**: Start with **periodic** (weekly or monthly). Implement as a `RebalanceTrigger` interface with a `TimeBasedTrigger(interval)` implementation. Add `DriftTrigger(threshold)` in MVP3. Never couple the Balancer's trigger to the Swing strategy's signal directly — that creates an implicit dependency.

### Q4: Strategy Hot-Swap — Enable/Disable at runtime?

**NautilusTrader**: Yes. Strategies are actors registered with a `TradingNode`. `node.add_actor(strategy)` at any time. No framework-level enforcement of position cleanup on removal.

**LEAN**: Yes, via algorithm composition changes. The framework does not handle position cleanup on removal.

**What actually needs to happen when a strategy is disabled**:

1. Stop generating new orders for that strategy
2. Decide policy: (a) close all positions immediately, (b) allow existing positions to run to their natural exit, (c) transfer positions to another strategy
3. Reallocate the disabled strategy's `capital_budget` to remaining strategies

**Conclusion for hermes**: Design `StrategyState{ACTIVE, DRAINING, DISABLED}`. `DRAINING` = no new entries, existing positions run to exit. `DISABLED` = no orders, capital_budget set to 0, Balancer reallocates on next cycle. This is a state machine on the strategy configuration record, not on the strategy actor itself.

### Q5: Cross-Strategy Risk Limits — Max total exposure, max correlation, max sector?

**NautilusTrader RiskEngine**: `max_notional_per_order: AHashMap<InstrumentId, Decimal>` — per-instrument absolute limit, applies globally across all strategies. No per-strategy total exposure cap. No correlation or sector limits in the core engine.

**LEAN RiskManagementModel**: `ManageRisk(targets)` receives all portfolio targets and can cancel any of them. `MaximumDrawdownPercentPerSecurity` checks `security.Holdings.UnrealizedProfitPercent` against a threshold and liquidates. There is no cross-strategy correlation check in the standard models.

**Industry practice**: Cross-strategy limits are enforced at the `Portfolio` level, not the strategy level. The standard approach:

- **Max total gross exposure**: `sum(|position_notional|) <= K * total_capital`
- **Max net exposure per instrument**: regardless of which strategy holds it
- **Max correlation**: requires correlation matrix computation — typically in a separate risk monitor, not the hot path
- **Sector concentration**: requires instrument metadata tagging

**Conclusion for hermes**: Implement in two layers:

- **Fast path (pre-trade)**: `max_notional_per_order` per instrument (NautilusTrader model) + `capital_budget` per strategy
- **Slow path (post-trade monitor)**: Total exposure check, per-instrument net limit across strategies. Run this asynchronously, not in the order submission hot path.

---

## 8. MVP1 Domain Model Requirements for MVP2 Balancer Plug-in

These are concrete field and interface requirements. Everything listed here must be in MVP1. Nothing here requires MVP2 to be implemented — only that the scaffolding is present.

### 8.1 Required Fields on Domain Aggregates

**`Order` aggregate**:

```go
type Order struct {
    ID         OrderID
    StrategyID StrategyID   // REQUIRED: which strategy originated this order
    InstrumentID InstrumentID
    Side       OrderSide
    Quantity   Decimal
    Price      Decimal      // optional for market orders
    // ... status, timestamps
}
```

**`Fill` / `Trade` value object**:

```go
type Fill struct {
    ID          FillID
    OrderID     OrderID
    StrategyID  StrategyID  // REQUIRED: propagated from Order
    InstrumentID InstrumentID
    Quantity    Decimal
    Price       Decimal
    Commission  Money
    Timestamp   time.Time
}
```

**`Position` aggregate**:

```go
type Position struct {
    ID              PositionID
    StrategyID      StrategyID   // REQUIRED: from opening Fill
    InstrumentID    InstrumentID
    Side            PositionSide
    Quantity        Decimal
    AvgOpenPrice    Decimal
    AvgClosePrice   *Decimal
    RealizedPnL     *Money
    OpeningOrderID  OrderID      // REQUIRED: traceability to origin
    Fills           []Fill
    // ... timestamps
}
```

**`Balance` / `CapitalBudget` config object**:

```go
type StrategyConfig struct {
    StrategyID     StrategyID
    CapitalBudget  Money       // REQUIRED: max total notional for this strategy
    MaxPositionPct Decimal     // REQUIRED: max % of budget in any single position
    Status         StrategyStatus  // ACTIVE | DRAINING | DISABLED
    OmsType        OmsType     // NETTING | HEDGING (per-strategy)
}
```

**`Portfolio` aggregate** (shared, owns the totals):

```go
type Portfolio struct {
    TotalCapital       Money
    UnrealizedPnL      map[InstrumentID]Money              // instrument-level totals
    RealizedPnL        map[InstrumentID]Money              // instrument-level totals
    StrategyPnL        map[StrategyID]map[InstrumentID]Money  // REQUIRED for MVP2 attribution
    NetPositions       map[InstrumentID]Decimal            // net across all strategies
    StrategyExposure   map[StrategyID]Money                // REQUIRED: for budget enforcement
}
```

The `StrategyPnL` and `StrategyExposure` maps can be empty in MVP1 (single strategy = trivial). They MUST exist so MVP2's Balancer can read them without a schema change.

### 8.2 Required Events

All position lifecycle events must carry `strategy_id`:

```go
type PositionOpenedEvent struct {
    StrategyID StrategyID
    // ... standard fields
}

type PositionChangedEvent struct {
    StrategyID StrategyID
    // ...
}

type PositionClosedEvent struct {
    StrategyID  StrategyID
    RealizedPnL Money
    // ...
}

type OrderFilledEvent struct {
    StrategyID StrategyID
    // ...
}
```

### 8.3 Required Interfaces for MVP2 Plug-in

```go
// MVP2 Balancer implements this interface
type Allocator interface {
    // Called by the Balancer on each rebalance trigger
    // Returns target weights for each strategy
    ComputeTargets(ctx AllocationContext) AllocationMap
}

type AllocationContext struct {
    TotalCapital    Money
    StrategyStates  []StrategyState
    MarketData      map[InstrumentID]Price
    Timestamp       time.Time
}

type AllocationMap map[StrategyID]Decimal  // strategy → weight (0.0 to 1.0)

// RiskEngine must support per-strategy budget enforcement
type CapitalBudgetEnforcer interface {
    CheckOrder(strategyID StrategyID, notional Money) error
    UpdateExposure(strategyID StrategyID, delta Money)
}
```

### 8.4 Invariants to Enforce from Day 1

1. **Attribution invariant**: Every `Position` MUST have a non-empty `StrategyID`. Enforce at `Position` construction time.
2. **Budget invariant**: `StrategyExposure[strategy_id]` MUST NOT exceed `StrategyConfig[strategy_id].CapitalBudget`. Enforce in `RiskEngine.CheckOrder()`.
3. **Sum invariant**: `sum(StrategyConfig[strategy_id].CapitalBudget for all active strategies)` SHOULD NOT exceed `Portfolio.TotalCapital`. The Balancer enforces this; the domain model just stores the invariant state.
4. **OMS invariant**: If `OmsType == NETTING`, two fills for the same `(strategy_id, instrument_id)` must update the SAME position. If `OmsType == HEDGING`, they create separate positions.
5. **Event propagation invariant**: `StrategyID` flows forward only, never backward. A fill cannot change its `strategy_id` after creation.

---

## 9. Confidence

| Topic | Confidence | Basis |
| ------- | ------------ | ------- |
| NautilusTrader Position/Event attribution chain | HIGH | Direct Rust source reading (`crates/model/src/position.rs`, `events/position/opened.rs`) — lines confirmed |
| NautilusTrader ExecutionEngine OMS overrides | HIGH | Direct source reading (`crates/execution/src/engine/mod.rs`) — `oms_overrides: HashMap<StrategyId, OmsType>` confirmed |
| NautilusTrader Portfolio PnL keying gap | HIGH | Direct source reading (`crates/portfolio/src/portfolio.rs`) — `unrealized_pnls: IndexMap<InstrumentId, Money>` not `(StrategyId, InstrumentId)` |
| LEAN Insight.SourceModel attribution | HIGH | WebFetch of `Common/Algorithm/Framework/Alphas/Insight.cs` |
| LEAN BlackLitterman multi-model view construction | HIGH | WebFetch of `BlackLittermanOptimizationPortfolioConstructionModel.cs` |
| bt AlgoStack composition pattern | HIGH | Direct source reading (`bt/algos.py`, `bt/core.py`) via GitHub API |
| VectorBT cash_sharing and group_by semantics | MEDIUM | WebFetch of vectorbt.dev docs — confirmed conceptually but OSS source 404 |
| Cross-strategy risk limit industry patterns | MEDIUM | Synthesised from multiple systems (NautilusTrader, LEAN); no single definitive source |
| MVP1 domain model field recommendations | HIGH | Derived directly from confirmed source evidence above |
| OmsType per-strategy semantics | HIGH | Confirmed in NautilusTrader Rust source |

---

## 10. Citations

### Source Code (direct reads)

1. **NautilusTrader `Position` struct** — `crates/model/src/position.rs`, nautechsystems/nautilus_trader, develop branch. Fields `strategy_id: StrategyId`, `opening_order_id: ClientOrderId`, `realized_pnl: Option<Money>` confirmed.

2. **NautilusTrader `PositionOpened` event** — `crates/model/src/events/position/opened.rs`, nautechsystems/nautilus_trader. Fields `strategy_id: StrategyId` and creation from `Position::strategy_id` confirmed.

3. **NautilusTrader `ExecutionEngine`** — `crates/execution/src/engine/mod.rs`, nautechsystems/nautilus_trader. `oms_overrides: HashMap<StrategyId, OmsType>` and `external_order_claims: HashMap<InstrumentId, StrategyId>` confirmed.

4. **NautilusTrader `RiskEngine`** — `crates/risk/src/engine/mod.rs`, nautechsystems/nautilus_trader. `max_notional_per_order: AHashMap<InstrumentId, Decimal>` (instrument-level, not strategy-level) confirmed.

5. **NautilusTrader `PortfolioState`** — `crates/portfolio/src/portfolio.rs`, nautechsystems/nautilus_trader. `unrealized_pnls: IndexMap<InstrumentId, Money>` — gap: no `(StrategyId, InstrumentId)` keying confirmed.

6. **NautilusTrader `Portfolio` manager** — `crates/portfolio/src/manager.rs`, nautechsystems/nautilus_trader. `AccountsManager.update_balances()` and `calculate_pnls()` confirmed as fill-event-driven.

7. **bt `WeighEqually`** — `bt/algos.py`, pmorissette/bt. `w = 1.0/n; target.temp['weights'] = {x: w for x in selected}` confirmed.

8. **bt `Rebalance`** — `bt/algos.py`, pmorissette/bt. Reads `target.temp['weights']`, calls `target.allocate()`, de-allocates children not in weights confirmed.

9. **bt `StrategyBase`** — `bt/core.py`, pmorissette/bt. Node tree with `children`, `_value`, `_weight`, `commission_fn` confirmed.

10. **bt `SelectAll`/`SelectMomentum`** — `bt/algos.py`, pmorissette/bt. Composable selector algo pattern confirmed.

### WebFetch (processed summaries)

 1. **LEAN `EqualWeightingPortfolioConstructionModel`** — GitHub raw, QuantConnect/Lean master. `DetermineTargetPercent` method, portfolio bias filtering confirmed.

 2. **LEAN `MeanVarianceOptimizationPortfolioConstructionModel`** — GitHub raw, QuantConnect/Lean master. `returns matrix → optimizer.Optimize() → weights` pipeline confirmed.

 3. **LEAN `BlackLittermanOptimizationPortfolioConstructionModel`** — WebFetch processed. View matrix construction grouping by `SourceModel`, P/Q matrix, posterior blending confirmed.

 4. **LEAN `Insight.SourceModel`** — GitHub raw, QuantConnect/Lean master. Fields `SourceModel`, `Id`, `Direction`, `Magnitude`, `Confidence`, `Weight` confirmed.

 5. **LEAN `MaximumDrawdownPercentPerSecurity`** — WebFetch processed. `UnrealizedProfitPercent < -threshold → PortfolioTarget(qty=0)` + `Insights.Cancel()` confirmed.

 6. **VectorBT Portfolio** — vectorbt.dev API docs. `cash_sharing`, `group_by`, column-as-strategy, `total_profit(group_by=False)` pattern confirmed.

### SA-0 Landscape Report

 1. **SA-0 Multi-Strategy Support Matrix** — `docs/research/paper-trading/sa0-landscape.md`, this repository. Freqtrade issue #8449 (manual capital rebalancing), NautilusTrader multi-strategy native support, bt allocator pattern descriptions.
