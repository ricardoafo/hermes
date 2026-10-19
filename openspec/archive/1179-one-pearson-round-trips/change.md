# Change: 1179-one-pearson-round-trips

**SDD change**: `1179-one-pearson-round-trips`
**Date**: 2026-10-18
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1179-one-pearson-round-trips/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Three rules were written more than once (repo audit 18 Oct, item 9;
findings D-3, D-4, D-9). Pearson's coefficient was computed four ways: the
domain `pearson` used by the RO-01 lock, and `statistics.correlation` in
M-09, the batch judge and the funnel's correlation gate, each with its own
error handling. A flat series off zero, such as three returns of 0.1, has a
float mean one ulp from its value, so `statistics.correlation` returned
rounding noise (0.0 or -1.6e-16) where the domain function returned
`None`. The average-cost round-trip loop was written twice, on both sides
of the M-09 parity check, neither checking the fee currency and both
dividing in the default 28-digit context. The `RiskAction` severity order
was a table in the domain type and another in the risk engine.

Each rule now has one home. No gate threshold, metric or pre-registered
rule changes; a flat series is undefined at every gate, as the RO-01 lock
already treated it.

## Scope

### In

- `domain/statistics/correlation.py`: `pearson`, moved from
  `domain/portfolio/correlation.py`; also `None` for a non-finite input or
  an overflowing variance.
- M-09 (`shadow_parity`), the batch judge and the funnel's correlation
  gate call it.
- `domain/portfolio/round_trips.py`: one loop over a small
  `RoundTripFill`, refusing a fee outside the quote, in `EXACT_CONTEXT`;
  `performance.round_trips` and `shadow_window.round_trips` adapt to it.
- `RiskAction.severity`, used by `is_worse_than` and the risk engine.
- Guards (see Test plan).

### Out

- The repo-wide duplicate-block ratchet (issue #1201).
- Shared time and money helpers (issue #1180).

## Decisions

### D-1: An undefined coefficient fails closed where it did before

- **Decision**: each caller keeps its existing meaning of "undefined":
  M-09 INSUFFICIENT, the batch judge counts it as correlation 1, the funnel
  row is "not computed" and fails.
- **Alternatives**: one shared meaning (would change a gate's rule).
- **Rationale**: only the function changes; the handling each gate's spec
  wrote stays.
- **Cost of reversal**: LOW.

### D-2: The fee-currency check raises

- **Decision**: a fill whose fee is not in the pair's quote raises
  `RoundTripError` (a `ValueError`); the shadow window re-raises it as
  `ShadowWindowError`, which `hermes parity` already reports.
- **Alternatives**: convert the fee (no rate is at hand in the loop);
  skip the fill (would hide a cost).
- **Rationale**: every adapter records fees in the quote today, so the
  check fires only on a bad record; per the issue, if it ever fires on
  real data the Code Owner decides before any figure changes.
- **Cost of reversal**: LOW.

### D-3: The shadow window keeps its net-only record

- **Decision**: `shadow_window.RoundTrip(pair, entered_at, net_pnl)` stays
  as the matching record, built from the domain trip's `net_pnl`.
- **Alternatives**: expose the domain type (would change the matching
  tests' fixtures for no behaviour gain).
- **Cost of reversal**: LOW.

### D-4: Severity is the declaration order

- **Decision**: `RiskAction.severity` is the member's index in declaration
  order (OK, WARN, HALT).
- **Alternatives**: an explicit value table (a second place to edit).
- **Cost of reversal**: LOW.

## Requirements

### R-1: One Pearson

Every correlation in `src/hermes` is `domain.statistics.correlation.pearson`;
a constant series gives `None` at any level and length.

### R-2: One round-trip loop

For any valid fill sequence, the performance file and the shadow window
return the same round trips; gross is exact at fill precision; a fee outside
the quote and a sell beyond the holding are refused.

### R-3: One severity order

`is_worse_than` and the risk engine both read `RiskAction.severity`.

## Design

`pearson` checks length, then returns `None` below two points, for a flat
series (by value), for any non-finite value, or for a zero or overflowing
variance; otherwise the clamped coefficient. `round_trips` keeps an `_Open`
per pair with quantity, cost, gross and fees, all in `EXACT_CONTEXT`, and
emits `ClosedRoundTrip(pair, entered_at, gross_pnl, fees)` with a `net_pnl`
property. The performance file maps a `Fill` and the shadow window a
`TradeExecuted` to `RoundTripFill`.

## Test plan

- `tests/unit/domain/statistics/test_correlation.py`: the moved Pearson
  tests, plus non-finite, overflow and constant-series (property) cases.
- `tests/unit/domain/portfolio/test_round_trips.py`: unit cases, fee
  currency, and exactness (property and a 36-digit example).
- `tests/unit/application/calibration/test_round_trips_both_sides.py`: both
  sides agree for any fill sequence (property), and a source scan that each
  rule has one home.
- Flat-series cases off zero for the funnel gate and the batch judge (both
  fail on the old code), and a flat M-09 curve.
- `RiskAction.severity` order and agreement with `is_worse_than`.

## Risks

- A flat running strategy off zero used to pass the funnel's correlation
  gate with a noise value; it now fails as "not computed", as a flat series
  at zero already did. The batch judge now ranks such a candidate last in a
  DSR tie, as it already ranked one at zero.
