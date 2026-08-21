# 05 — Signal Engines (Rebalancer + Donchian)

**Parent ADR**: [README.md](./README.md)
**Status**: Accepted
**Accepted**: 2026-08-21

## Question this section answers

How are the two engines structured in code? What does the Rebalancer's band logic look like? What does the Donchian engine actually compute? Both are pure — how does that shape their testability?

**Scope note**: This section covers the **architecture** of the engines. **Deep parameter selection** (which N-day breakout, what ATR multiple, what tax-defer thresholds) gets its own SDD change in Phase 1 — those are backtest-driven decisions, not architecture decisions.

## Rebalancer engine — pure Python, no I/O

```python
# src/hermes/domain/rebalancer.py

@dataclass(frozen=True)
class RebalanceContext:
    portfolio: Portfolio            # current holdings
    targets: dict[Asset, Weight]    # e.g., {BTC: 0.60, ETH: 0.30, USDC: 0.10}
    band: Percentage                # e.g., 0.10 (10%)
    prices: dict[Asset, Price]      # EUR-priced
    tax_state: TaxState             # YTD realized gain, current bracket

@dataclass(frozen=True)
class RebalanceDecision:
    action: Literal["NO_ACTION", "REBALANCE", "DEFER_TAX"]
    trades: list[TradeCommand]      # empty if NO_ACTION or DEFER_TAX
    reason: str                     # human-readable + machine-parseable tag

class Rebalancer:
    """Pure function: given context, decide what to do. No I/O."""

    def decide(self, ctx: RebalanceContext) -> RebalanceDecision: ...

    # Helpers (also pure):
    def compute_current_weights(self, portfolio, prices) -> dict[Asset, Weight]: ...
    def compute_drifts(self, current, targets) -> dict[Asset, Percentage]: ...
    def is_out_of_band(self, drifts, band) -> bool: ...
    def compute_trades_to_target(self, portfolio, targets, prices) -> list[TradeCommand]: ...
    def should_defer_for_tax(self, trades, tax_state) -> bool: ...
```

### Band logic (plain English)

```text
For each asset:
  drift_pct = |current_weight - target_weight| / target_weight
  if any drift_pct > band → REBALANCE trigger
  else → NO_ACTION

Trade computation:
  For each asset:
    target_value_eur = total_portfolio_eur × target_weight
    delta_value = target_value_eur - current_value_eur
    if delta_value > 0: BUY delta_value / price_eur
    if delta_value < 0: SELL |delta_value| / price_eur
```

### Tax-aware defer (parameters tuned in Phase 1)

```text
For each proposed rebalance:
  crossing_amount    = max(0, proposed_gain - (€6000 - YTD_gain))
  tax_cost           = 0.02 × crossing_amount
  opportunity_cost   = estimated_slippage_from_delay(drift, vol, days_to_year_end)

  DEFER if all three hold:
    tax_cost > opportunity_cost
    AND drift < 20% (never defer big drifts)
    AND time_to_year_end < 3 months (safe to defer)
```

Exact opportunity-cost formula gets designed + backtested in Phase 1. For MVP, keep the check pluggable behind an interface.

## Donchian engine — pure Python, no I/O

```python
# src/hermes/domain/donchian.py

@dataclass(frozen=True)
class DonchianConfig:
    entry_lookback: int = 55        # 55-day breakout (parameter, tuned in Phase 1)
    exit_lookback: int = 20         # 20-day opposite band for regime exit
    atr_period: int = 14
    atr_stop_multiple: Decimal = Decimal("2.0")   # 2× ATR trailing stop
    ema_regime_period: int = 200
    adx_period: int = 14
    adx_threshold: int = 20

@dataclass(frozen=True)
class DonchianDecision:
    action: Literal["HOLD", "ENTER", "EXIT", "UPDATE_STOP"]
    entry_price: Optional[Price] = None
    stop_price: Optional[Price] = None
    exit_reason: Optional[str] = None
    reason: str = ""

class DonchianEngine:
    """Pure function: given price history + open position, decide."""

    def check_signal(
        self,
        asset: Asset,
        candles: list[Candle],
        open_position: Optional[OpenPosition],
        config: DonchianConfig,
    ) -> DonchianDecision: ...

    # Helpers (all pure, pandas-backed under the hood):
    def donchian_upper(self, candles, lookback) -> Price: ...
    def donchian_lower(self, candles, lookback) -> Price: ...
    def ema(self, closes, period) -> Price: ...
    def atr(self, candles, period) -> Decimal: ...
    def adx(self, candles, period) -> Decimal: ...
    def regime_ok_for_entry(self, price, ema_200, adx) -> bool: ...
    def stop_price(self, entry, atr, multiple) -> Price: ...
    def trailing_stop_update(self, current_stop, current_price, atr, multiple) -> Optional[Price]: ...
```

### Decision logic (simplified)

```text
if no open_position:
    if price > donchian_upper(entry_lookback)
       AND price > ema(200)
       AND adx > adx_threshold:
        → ENTER at price, stop at price - (atr_stop_multiple × atr)
    else:
        → HOLD

if open_position exists:
    new_stop = trailing_stop_update(current_stop, price, atr, atr_stop_multiple)
    if new_stop > current_stop:
        → UPDATE_STOP (raise trailing stop)
    elif price < donchian_lower(exit_lookback):
        → EXIT (regime reversal, opposite breakout)
    else:
        → HOLD (stop-loss on exchange handles the down case)
```

**Note**: the primary exit is the **exchange-side stop-loss**. Domain-side EXIT only fires for regime-reversal (opposite breakout). Stop-hit is detected by the daemon reconciling with the exchange, not by the engine watching prices.

## Position sizing (pure)

```python
# src/hermes/domain/position_sizing.py

def compute_position_size(
    satellite_capital_eur: Decimal,
    risk_per_trade_pct: Decimal,       # e.g., 0.01 (1%)
    entry_price_eur: Price,
    stop_price_eur: Price,
    max_position_pct: Decimal,          # e.g., 0.40 (40% cap of satellite)
    trading_rules: TradingRules,        # MIN_NOTIONAL, lot_step
) -> Optional[Decimal]:
    """
    Position size such that if stop hits, we lose exactly risk_per_trade_pct
    of satellite capital. Returns None if the resulting size is below MIN_NOTIONAL
    or below fee-to-benefit threshold.
    """
    risk_budget = satellite_capital_eur * risk_per_trade_pct
    stop_distance = entry_price_eur - stop_price_eur
    raw_qty = risk_budget / stop_distance
    raw_notional_eur = raw_qty * entry_price_eur

    # Guardrails:
    #   - Below MIN_NOTIONAL → skip trade
    #   - Above max_position_pct × satellite → cap
    #   - Fee > 15% of risk budget → skip trade
    return apply_guards(...)
```

## Testability — the payoff for pureness

```python
def test_rebalancer_no_action_when_in_band():
    ctx = RebalanceContext(
        portfolio=Portfolio({BTC: 0.62, ETH: 0.29, USDC: 0.09}),
        targets={BTC: 0.60, ETH: 0.30, USDC: 0.10},
        band=Percentage("0.10"),
        prices={BTC: Price("68000"), ETH: Price("3600"), USDC: Price("1")},
        tax_state=TaxState.empty(),
    )
    result = Rebalancer().decide(ctx)
    assert result.action == "NO_ACTION"


def test_donchian_enter_on_breakout_with_regime_ok():
    candles = fixtures.uptrending_series(200)   # deterministic price series
    result = DonchianEngine().check_signal(
        asset=ETH,
        candles=candles,
        open_position=None,
        config=DonchianConfig(),
    )
    assert result.action == "ENTER"
    assert result.stop_price is not None
```

**No mocks. No fixtures. No database. No HTTP. No clock.** Given inputs → deterministic outputs. This is 90% of the money-critical logic, tested cheaply.

## Configuration surface (YAML, loaded via pydantic-settings)

```yaml
# config/strategies.yaml
rebalancer:
  targets:
    BTC: 0.60
    ETH: 0.30
    USDC: 0.10
  band: 0.10
  tax_aware_defer:
    enabled: true
    savings_base_ceiling_eur: 6000
    max_drift_before_forced_trade: 0.20
    max_defer_months: 3

donchian:
  entry_lookback: 55
  exit_lookback: 20
  atr_period: 14
  atr_stop_multiple: 2.0
  ema_regime_period: 200
  adx_period: 14
  adx_threshold: 20

position_sizing:
  risk_per_trade_pct: 0.01
  max_position_pct: 0.40
  min_fee_to_risk_ratio: 0.15   # skip trade if fee > 15% of risk budget
```

**Every parameter change** goes through the decision journal at `docs/decisions/` with dated reason. Never edit YAML without a journal entry.

## Deferred to Phase 1 (dedicated SDD design)

Per Ricardo's explicit request, these get their own SDD change with proper depth:

- **Donchian parameter sweep**: 20/55/100-day entry lookbacks per asset, per-regime performance, per-asset vs global params
- **Tax-aware defer formula**: opportunity-cost model, real backtest with vs without defer
- **LLM memorization guard for backtests**: which data is safe (post-Jan-2026), walk-forward methodology, out-of-sample validation
- **Backtest engine design**: replay infrastructure, fee/slippage modeling, EUR conversion

This section captures the **architecture** — the shape of the engines and their pure-function contract. **Parameters and backtests are Phase 1 design work.**

## Alternatives considered

| Option                                     | Why rejected                                                                          |
| ------------------------------------------ | ------------------------------------------------------------------------------------- |
| **Signals as SQL views**                   | Loses testability, mixes concerns, no version control on logic                        |
| **ML-based signal generation**             | Prohibited by ADR-001 (Nof1 evidence)                                                 |
| **Live parameter adaptation**              | Explicitly prohibited — parameter changes go through decision journal, backtest first |
| **Separate signal service (microservice)** | Overkill; a function call is enough                                                   |

## Cross-references

- [01-code-organization.md](./01-code-organization.md) — where Rebalancer + DonchianEngine live in domain/
- [02-data-flow.md](./02-data-flow.md) — how RebalanceUC and SwingSignalUC call these engines
- [03-persistence-schema.md](./03-persistence-schema.md) — where TaxState comes from (query on tax_lots + lot_consumptions)
- [04-exchange-adapters.md](./04-exchange-adapters.md) — where prices, rules, and stops come from
- Phase 1 SDD change (planned) — deep parameter selection
