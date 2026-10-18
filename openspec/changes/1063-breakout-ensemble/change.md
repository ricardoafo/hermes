# Change: 1063-breakout-ensemble

**SDD change**: `1063-breakout-ensemble`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#1063)

Mirror of engram topic key `sdd/1063-breakout-ensemble/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Research batch swing-b1 (ADR-026) names four candidates. This change builds C1,
`swing-b1-ensemble`, to the batch's §C1 words: a long entry when several 4h
Donchian breakout lengths agree and the daily close is above its EMA(200), with
v2's exits. It adds the signal kind `breakout_ensemble`, its domain module and a
disabled roster entry. No backtest runs here; the batch's Train run does that
once the batch is signed and merged. Main risk: code that trades a rule other
than the signed one. Each rule and parameter is pinned by a hand-computed test.

## Scope

### In

- `src/hermes/domain/strategy/swing/ensemble.py`: `BreakoutEnsembleAdapter`.
- `breakout_ensemble` registered in
  `src/hermes/application/strategy_protocol/strategy_factory.py`.
- `config/strategies.yaml`: `swing-b1-ensemble`, disabled, with v2's capital,
  sizing and close-priced exits (batch Common).

The batch's `code_paths` for C1 are the module, `adapter.py` (its exits), the
factory and `config/strategies.yaml`.

### Out

- The batch document and the card (issue #1062, PR #1067).
- Any backtest run, metric or trial line.

## Decisions

- **D-1 Subclass v2's adapter, replace only the entry check.** C1's exits are
  v2 §5.2's, so `BreakoutEnsembleAdapter` extends `SwingStrategyAdapter` and
  overrides `_entry_check`. The chandelier posting, ratchet, time stop, regime
  break, stop records and restart behaviour are shared code, not a copy.
- **D-2 Parameter names and bases from the batch.** `agree_min` (int, base 2),
  `chandelier_atr_mult` (Decimal, base 3.0) and `longest_lookback` (int, base
  120) are keyword-only, so `default_parameters()` returns exactly the batch's
  base values and the driver's signed-base check passes.
- **D-3 The fixed lookbacks are 20 and 55.** The card fixes them; only the
  third is free. `longest_lookback` must exceed 55 and `agree_min` must be
  1 to 3; anything else is refused at build.
- **D-4 The journal record.** `EntryCheck.breakout` is the agreement
  condition and `momentum` is always true, because C1 has no ADX filter.
- **D-5 Warm-up.** The 4h buffer holds 300 bars, more than the longest
  neighbour (180) needs, and the driver loads 300 days of warm-up.

## Requirements

- R-1 Entry when at least `agree_min` of {20, 55, `longest_lookback`} break out
  and the daily close is above its EMA(200); never otherwise.
- R-2 A close equal to an old high is not a breakout.
- R-3 Exits are v2's, with `chandelier_atr_mult` as the multiple.
- R-4 The kind's defaults are the batch's base values, and every neighbour builds.
- R-5 The roster entry is disabled and names `signal: breakout_ensemble`.

## Design

`_entry_check` counts `DonchianEngine.high_break(bars, period=L)` over the three
lookbacks and compares the count with `agree_min`; the regime is the base class's
`_regime_above_ema`. Everything else is inherited.

## Test plan

- Unit, `tests/unit/domain/strategy/swing/test_ensemble.py`: synthetic bars with
  two old highs placed so the agreeing count is known by hand (R-1, R-2), the
  neighbours of `agree_min` and `longest_lookback`, the regime filter, the entry
  stop at each multiple (R-3), the defaults and neighbours (R-4), the roster
  entry (R-5) and refusals.
- Property: entering is monotone in `agree_min`; at `agree_min` 3 the adapter
  enters exactly when the close beats both old highs.

## Risks

- `momentum` reads "yes" in serve's journal for this kind; it is never run in
  serve while disabled.
