# Change: v2-strategy-config

**SDD change**: `v2-strategy-config`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/v2-strategy-config/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Adds the `swing-mvp1-v2` roster entry and an exit pricing that the strategy
config selects. Signed pre-registration v2 (§0 rows 2 and 5, §5.3, §6.2, §7)
sells every exit at market at the close of the 4h bar the exit fired on, filled
5 bps under that close, in the backtest and in paper shadow alike; v1 keeps its
stop-market pricing (`min(stop, open)` less 15 bps, PR #955). The pricing lives
in the one place both paths already share, `ExecutionModel.reference()`, so the
backtest fill, the serve fallback fill and the serve drift model price
(`FillQualityRecorded.model_price`) cannot disagree. The main risk is a v2 run
priced as v1 by a missing config field; serve refuses a roster whose pricing is
not the one its strategy name pre-registered.

## Scope

### In

- `ExitPricing` (`stop_order` for v1, `bar_close` for v2) and an
  `ExecutionModel.exit_pricing` field in
  `src/hermes/application/execution/prereg_book.py`.
- `StrategyConfig.exit_pricing` (default `stop_order`, the v1 signed
  behaviour) and `StrategyConfig.execution_model()` in
  `src/hermes/infrastructure/config/models.py`.
- A disabled `swing-mvp1-v2` entry in `config/strategies.yaml` with the v2
  §2 / §5.4 / §5.5 values (unchanged from v1) and `exit_pricing: bar_close`.
- Backtest drivers (`scripts/backtest/run_portfolio_backtest.py`,
  `scripts/backtest/run_calibration_full.py`): `--strategy NAME` selects a
  roster entry, and the execution model comes from that entry.
- Serve (`src/hermes/entrypoints/serve.py`): the shadow executor's model comes
  from the enabled roster entry; `check_startup` accepts either pre-registered
  strategy and refuses an entry whose exit pricing differs from its own
  pre-registration; the mainnet approval reads the enabled strategy's record.
- `hermes reconcile` (`src/hermes/entrypoints/reconcile.py`) seeds the book
  from enabled roster entries only (D-5).

### Out

- Starting v2 in shadow, its certification record and reading
  `pre-registration-v2.md` in serve (`hermes shadow-start`, issue #797).
  Serve's kill evaluator keeps reading v1's file.
- The funnel evidence file and the `backtest.yml` funnel step (PR #964).
- `hermes certify` reading v2's DSR ≥ 0.95 (#361).
- Any change to the swing adapter: v2's §5.1 / §5.2 rules are v1's as they
  stand on main after PR #955 (daily stop) and PR #959 (300 daily bars).

## Decisions

### D-1: the pricing is a field of the shared `ExecutionModel`

- **Decision**: `ExecutionModel` gains `exit_pricing: ExitPricing`; under
  `bar_close` every exit's reference is `bar.close` with the market-order
  half-spread (`entry_bps`, 5 bps), under `stop_order` the v1 rule stands.
- **Alternatives**: a second model class for v2 (two §6.2 implementations,
  the thing #353 D-2 removed); stamping the fill price on the exit signal
  (the strategy would carry execution policy, and the synthetic book would
  still be centred elsewhere).
- **Rationale**: the backtest (`replay._execute`), the serve fallback book
  (`ShadowExecutor._book`) and the serve drift row (`ShadowExecutor._quality`)
  all call `ExecutionModel.book()` / `reference()` already. One field changes
  all three together (pre-registration v2 §0 row 5, §7).
- **Cost of reversal**: LOW.

### D-2: the strategy config selects the pricing, defaulting to v1

- **Decision**: `StrategyConfig.exit_pricing` defaults to `stop_order`; the
  shipped YAML sets it explicitly on both entries.
- **Alternatives**: a required field (a server config without it would stop
  the running v1 shadow at its next deploy); deriving it from the name (two
  sources of truth for one setting).
- **Rationale**: v1's behaviour and its exit prices must not change; a roster
  file written before this change still means v1. Serve's startup check
  closes the remaining hole (D-3).
- **Cost of reversal**: LOW.

### D-3: serve pins each pre-registered strategy to its pricing

- **Decision**: `serve.PRE_REGISTERED_EXITS` maps `swing-donchian-4h` to
  `stop_order` and `swing-mvp1-v2` to `bar_close`; `check_startup` refuses
  an enabled roster that is not exactly one of them, or one whose
  `exit_pricing` differs.
- **Alternatives**: accept any name (serve could trade an unregistered
  strategy); keep v1-only (v2 could never reach shadow).
- **Rationale**: a v2 shadow priced as v1 would make every exit's drift wrong
  (§7). The mainnet check then loads the enabled strategy's own certification
  record, so a v1 approval can never unlock v2.
- **Cost of reversal**: LOW.

### D-4: the drivers add the pricing to trial records only for v2

- **Decision**: `execution.exit_pricing` is written to the trial parameters
  and report only when it is not `stop_order`.
- **Alternatives**: always write it (every v1 trial and report changes shape
  mid-experiment).
- **Rationale**: v1's records keep their shape; a v2 trial still names the
  pricing it ran with, and its `strategy_id` already separates it.
- **Cost of reversal**: LOW.

### D-5: reconcile seeds only enabled strategies

- **Decision**: `hermes reconcile` builds its starting capital from the
  enabled roster entries.
- **Alternatives**: seed every entry (the disabled v2 entry would add 5000
  USDT to the book and every v1 month would show USDT drift against the
  ledger's lots).
- **Rationale**: a disabled entry has funded nothing; v1's reconciliation
  must read as it did before the v2 entry was added.
- **Cost of reversal**: LOW.

## Requirements

### R-1: v2 exits fill at close × 0.9995 in both paths

- **A-1.1**: `uv run pytest tests/unit/application/execution/test_prereg_book.py`
  passes, including the v2 stop, time-stop and regime-break cases and the
  hypothesis property.
- **A-1.2**: `uv run pytest tests/unit/application/execution/test_shadow.py -k v2`
  passes: `replay()` and `ShadowExecutor` book the same v2 stop exit price on
  the same bar, and the quality row's `model_price` is that price.

### R-2: v1 is unchanged

- **A-2.1**: every existing test of `prereg_book`, `replay`, `shadow`, the
  drivers and `tests/e2e/test_serve_cycle.py` passes unmodified.
- **A-2.2**: `ExecutionModel()` keeps `exit_pricing == ExitPricing.STOP_ORDER`.

### R-3: the config selects the pricing

- **A-3.1**: `config/strategies.yaml` loads with a disabled `swing-mvp1-v2`
  entry whose `execution_model()` prices `bar_close`.
- **A-3.2**: `--strategy swing-mvp1-v2` makes a driver run that entry.

### R-4: serve trades v2 with v2 pricing

- **A-4.1**: `uv run pytest tests/e2e/test_serve_v2_exit.py` passes: serve
  booted on the repository config with v2 enabled sells a synthetic stop exit
  at close × 0.9995, with that price as the drift model price.
- **A-4.2**: `check_startup` refuses v2 priced `stop_order` and v1 priced
  `bar_close`.

## Design

```python
class ExitPricing(StrEnum):
    STOP_ORDER = "stop_order"  # v1 §5.3: stop-market, min(stop, open) − stop_bps
    BAR_CLOSE = "bar_close"  # v2 §5.3: market sale at the exit bar's close − entry_bps


@dataclass(frozen=True, slots=True)
class ExecutionModel:
    entry_bps: Decimal = Decimal("5")
    stop_bps: Decimal = Decimal("15")
    depth_frac: Decimal = Decimal("0.30")
    exit_pricing: ExitPricing = ExitPricing.STOP_ORDER

    def reference(self, signal, bar):
        if (
            self.exit_pricing is ExitPricing.STOP_ORDER
            and signal.kind is EXIT
            and signal.exit_reason == "chandelier"
        ):
            return min(signal.stop_price, bar.open), self.stop_bps
        return bar.close, self.entry_bps
```

`StrategyConfig.execution_model(depth_frac=None) -> ExecutionModel`.

```yaml
  - name: swing-mvp1-v2
    enabled: false
    weight: "1.0"
    capital_budget: "5000"
    quote: USDT
    exit_pricing: bar_close
    sizing: {risk_pct: "0.01", max_position_pct: "0.05", max_total_exposure_pct: "0.15"}
```

The exit bar is the adapter's, unchanged: the stop exits on the 4h bar whose
low reaches the stop in force when it opened (moved only at daily closes,
never down); the time stop on the first 4h bar closing at or after entry
close + 90 days; the regime break on the 4h bar evaluated once a 1D close
below EMA(200) is buffered, which in delivery order (1D first on ties, #311)
is the bar closing at 00:00 UTC.

Command check run against the tree while designing:
`uv run python -c "from hermes.application.execution.prereg_book import ExecutionModel; print(ExecutionModel())"`
prints `ExecutionModel(entry_bps=Decimal('5'), stop_bps=Decimal('15'), depth_frac=Decimal('0.30'))`
on main, so the v1 default is the one field set being preserved.

## Test plan

1. Unit and hypothesis tests of `ExecutionModel.reference()` / `book()`
   under both pricings (`test_prereg_book.py`).
2. Config tests: the shipped roster, the default and the YAML values
   (`test_loader.py`, `test_models_property.py`).
3. Parity: `replay()` and `ShadowExecutor` on the same v2 stop bar
   (`test_shadow.py`).
4. Serve: `check_startup` and the composition's model (`test_serve.py`).
5. Drivers: `--strategy` selection and the v2 trial fields.
6. e2e: `tests/e2e/test_serve_v2_exit.py` on synthetic bars.

Test tiers touched: unit, property, e2e

## Risks

- **A server roster without `exit_pricing`**: it reads as v1, which is what a
  pre-change file meant; enabling v2 without the field is refused at boot (D-3).
- **Live book in serve**: a fresh bar still fills on the live book, so a v2
  shadow fill can differ from close × 0.9995; that difference is the drift §7
  measures against the close-based model price, as signed.
- **Regime break after a restart**: the adapter tests a level, not an event,
  so a position restored while the daily close is already under EMA(200)
  exits on the first 4h bar after boot. Same in v1; unchanged here.

## Dependencies on other changes

- PR #955 (daily stop) and PR #959 (300 daily bars), both on main.
- Signed `docs/experiments/swing-mvp1/pre-registration-v2.md` (PR #983).
