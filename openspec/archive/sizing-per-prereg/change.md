# Change: sizing-per-prereg

**SDD change**: `sizing-per-prereg`
**Date**: 2026-10-04
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/sizing-per-prereg/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`SizingCalculator` did not implement the signed pre-registration
(`docs/experiments/swing-mvp1/pre-registration-v1.md` §2, §5.4, §5.5). The
spec-3 design (`spec-3-order-router/change.md` D-2) sized from current equity
with `equity × risk_pct ÷ stop_atr_mult`, a formula that never reads the entry
price or the stop distance, and it applied a Kelly cap that §5.4 defers. It
also left the §5.5 total exposure cap unenforced. This change re-implements
sizing exactly as §5.4/§5.5 state it, from the fixed experiment capital, with
every percentage and the capital read from `config/strategies.yaml`. The main
risk is that the new formula almost always lands on the 5% hard cap for
chandelier stops (3 × ATR(22) is several percent below entry), which is what
the pre-registration says, not a defect.

## Scope

### In

- `src/hermes/application/order_router/sizing.py`: §5.4 risk sizing, §5.4
  per-position hard cap, §5.5 total exposure cap, Kelly removed.
- `src/hermes/infrastructure/config/models.py` + `config/strategies.yaml`:
  a required `sizing` block per strategy (`risk_pct`, `max_position_pct`,
  `max_total_exposure_pct`); capital stays `capital_budget`.
- `src/hermes/application/portfolio/tracker.py`: `open_notional()` accessor,
  the source of the open notional the exposure cap reads.
- Tests for the above; callers of `SizingInput` updated.

### Out

- Wiring the router into the serve loop or the backtest driver (#313).
- Correlation lock and concurrency limit (§5.5) — already enforced by the
  router guard and RO-01; unchanged.
- Kelly sizing — deferred by §5.4 until ≥ 30 OOS trades; reintroducing it is
  a pre-registration amendment, not a config flip.
- Exchange lot-step rounding of `qty`.

## Decisions

### D-1: Size from fixed experiment capital, not equity

- **Decision**: Every limit is a share of `capital_budget` (§2: 5,000 USDT).
- **Alternatives**: current equity (rejected — §2 states the risk % refers
  to the full capital denominator "regardless of actual deployed balance",
  and equity-based sizing compounds, which the experiment does not test).
- **Rationale**: §2, §5.4, §5.5 are binding (AGENTS.md precedence; ADR-009).
- **Cost of reversal**: LOW — one field in `SizingPolicy`.

### D-2: Notional = risk ÷ (entry − stop) × entry, then `min` with the caps

- **Decision**: `risk_notional = risk_pct × capital × entry ÷ (entry − stop)`;
  `notional = min(risk_notional, max_position_pct × capital)`. Supersedes
  spec-3 D-2 (ATR/stop-multiplier baseline and Kelly cap).
- **Alternatives**: keep the ATR baseline (rejected — not the signed
  formula; it ignores the stop, so realised risk is unbounded).
- **Rationale**: §5.4 verbatim. Divisions round toward zero so the sized
  notional never exceeds the exact formula: realised risk ≤ 1% of capital
  holds exactly, not within an epsilon.
- **Cost of reversal**: LOW — `sizing.py` only.

### D-3: Kelly removed, not gated by config

- **Decision**: Delete the Kelly term and `f_star_estimate` input.
- **Alternatives**: a `kelly_enabled: false` config flag (rejected — a flag
  that must never be flipped during the experiment is a footgun; §5.4 ties
  activation to ≥ 30 OOS trades, which is a new pre-registration).
- **Cost of reversal**: LOW — re-add one more `min` term.

### D-4: Total exposure cap blocks, it does not shrink

- **Decision**: If `open_notional + notional > max_total_exposure_pct ×
  capital`, sizing fails with `InvalidLot`; the router turns that into
  `RiskRejected(rule="sizing")`.
- **Alternatives**: clip the new position to the remaining headroom
  (rejected — it opens a position smaller than §5.4 prescribes, a silent
  sizing deviation; issue #480 acceptance says the fourth position is
  "blocked").
- **Cost of reversal**: LOW.

### D-5: Open notional comes in on `SizingInput`, sourced from the tracker

- **Decision**: `SizingInput.open_notional` is supplied by whoever builds the
  routing input, from `PortfolioTracker.open_notional()` (the snapshot's
  `market_value`: open positions at their latest marks).
- **Alternatives**: add a field to `PortfolioState` (rejected — a domain type
  shared with every risk-overlay rule; widening it touches the router and the
  snapshot mapping that #313 is also changing); keep a running total inside
  the router (rejected — the router is stateless by design, spec-3).
- **Rationale**: the tracker already owns the positions; marked value is the
  conservative choice when prices rise.
- **Cost of reversal**: LOW.

### D-6: Percentages live in `config/strategies.yaml`, required

- **Decision**: `StrategyConfig.sizing` (`PositionSizingConfig`) is required;
  no code defaults. `StrategyConfig.sizing_policy()` builds the application
  `SizingPolicy` from it and `capital_budget`.
- **Rationale**: the Code Owner asked (2026-10-03) that limits follow the
  configured capital; a missing block must fail at load, not fall back.
- **Cost of reversal**: LOW.

## Requirements

### R-1: §5.4 worked examples

- **A-1.1**: capital 5,000, entry 100, stop 50 → notional 100 USDT
  (50 ÷ 50 × 100). Note: issue #480 quotes "entry 100, stop 95 → 100 USDT";
  that arithmetic is off by 10× (50 ÷ 5 × 100 = 1,000), so the stop-95 case
  is pinned as the hard-cap example instead.
- **A-1.2**: entry 100, stop 95 → formula 1,000 → capped at 250 USDT.
- **A-1.3**: open notional 750 (or 600 + 250 > 750) → fourth entry blocked.

### R-2: Properties

- **A-2.1**: notional ≤ `max_position_pct × capital` for all valid inputs.
- **A-2.2**: notional × (entry − stop) ÷ entry ≤ `risk_pct × capital`
  exactly (Fraction arithmetic).
- **A-2.3**: open + notional ≤ `max_total_exposure_pct × capital` on success.

### R-3: Config drives limits

- **A-3.1**: loading `config/strategies.yaml` yields 1% / 5% / 15% / 5,000.
- **A-3.2**: changing `capital_budget` scales all three limits.

## Test plan

1. `uv run pytest tests/unit tests/integration -q` green.
2. `bash scripts/dev/pr-preflight.sh` green.

## Risks

- **Hard cap almost always binds**: chandelier stops sit several percent
  below entry, so `risk_notional` exceeds 250 for most entries. Per §5.4 this
  is intended; realised risk per trade is then below 1%.
- **Pre-registration example drift**: issue #480's first example contradicts
  §5.4's formula; the tests follow §5.4.
- **#313 overlap**: #313 wires the router; it must pass `open_notional` and
  build `SizingCalculator(strategy.sizing_policy())`.

## Dependencies on other changes

- Supersedes D-2 of `openspec/archive/spec-3-order-router/change.md`.
- #313 (backtest driver) consumes the new `SizingInput`.
