# Change: 1074-strategy-factory

**SDD change**: `1074-strategy-factory`
**Date**: 2026-10-17
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#1074)

Mirror of engram topic key `sdd/1074-strategy-factory/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-026 (PR #1055) backtests a batch of candidates from different families,
but serve, parity, the portfolio backtest driver and decision replay all build
`SwingStrategyAdapter()` by name, so only the Donchian breakout can run. This
change adds one strategy factory keyed by a `signal:` key in the roster entry.
Every entry point builds through it; none of them reads the batch document.
Main risk: v1 or v2 trading a different rule after the change. An entry with
no `signal:` builds today's adapter with its signed defaults, and a test proves
the replay is identical on the existing fixtures.

## Scope

### In

- `src/hermes/application/strategy_protocol/strategy_factory.py`:
  `StrategyKind`, `strategy_kind`, `known_signals`, `UnknownSignalError`,
  `DONCHIAN_BREAKOUT`.
- `StrategyConfig.signal` (optional) with a validator, and
  `StrategyConfig.strategy_kind()`.
- `hermes serve`, `hermes parity` (day and window), `scripts/backtest/run_portfolio_backtest.py`
  and `scripts/backtest/decision_replay.py` build through the factory.
- The driver records `signal` in a trial line only when the entry names one.

### Out

- Any new signal kind: each candidate PR (#1063 to #1066) adds one kind and its
  module and lists both in its `code_paths`.
- Neighbour replays for a new kind: `FUNNEL_REPLAYS` holds the Donchian
  neighbours; a candidate PR adds its own from its card.
- `scripts/backtest/run_calibration.py` (single-pair, v1 history only).
- No edit to `config/strategies.yaml`: v1 and v2 stay byte-identical.

## Decisions

- **D-1 Key by `signal:`, default Donchian.** A missing key means the Donchian
  breakout (`donchian_breakout`), so the signed v1/v2 entries need no edit and
  their pre-registration fingerprints do not move.
- **D-2 Unknown signal fails closed.** `strategy_kind` raises
  `UnknownSignalError`; the config validator turns it into a `ConfigError` at
  load, so serve and parity exit with their config-error code and never fall
  back to the default rule. The driver keeps its documented exit 1 for every
  config error.
- **D-3 Parameters by name.** `StrategyKind.create(mapping)` passes the
  mapping as keyword arguments over the builder's keyword-only defaults and
  refuses a name the builder does not take, so a neighbour replay cannot run
  the base silently. `default_parameters()` reads the builder's keyword
  defaults, the same values `_strategy_parameters()` recorded before.
- **D-4 Signal in the trial only when set.** v1/v2 trial lines keep their
  exact keys; a candidate's line names its signal, so DSR's trial count sees
  which family ran.
- **D-5 Application layer.** The factory lives in
  `application/strategy_protocol`: it imports domain adapters and is imported
  by infrastructure config and entry points, which the import contracts allow.

## Requirements

- R-1 An entry with no `signal:` builds `SwingStrategyAdapter` with its
  pre-registered parameters.
- R-2 Replaying the same bars through the default kind and through
  `SwingStrategyAdapter()` gives the same signals, fills, refusals and equity.
- R-3 An unknown `signal:` is refused at config load and by `strategy_kind`.
- R-4 A parameter the kind does not take is refused.
- R-5 serve, parity, the driver and decision replay build through the factory.
- R-6 A trial line carries `signal` only when the entry names one.

## Design

`StrategyKind[S]` is a frozen dataclass of a signal name and a builder
callable. `_KINDS` is a read-only mapping from signal name to kind, holding
only the Donchian breakout. `StrategyConfig.strategy_kind()` returns the
entry's kind; serve builds `kind.create()` per pair, parity passes
`kind.create` as its strategy maker, the driver uses `kind.default_parameters()`
as the base trial parameters and `kind.create(parameters)` per pair, and
decision replay builds `kind.create()` per pair.

## Test plan

- Unit: `tests/unit/application/strategy_protocol/test_strategy_factory.py`
  (R-1, R-3, R-4), `tests/unit/infrastructure/config/test_models_property.py`
  (R-3 at config load, property over unknown names),
  `tests/unit/scripts_backtest/test_run_portfolio_backtest.py` (R-6).
- Integration: `tests/integration/backtest/test_portfolio_backtest.py`
  (R-2, real kernel, synthetic bars).
- Existing serve, parity, driver and decision replay suites cover R-5.

## Risks

- A future kind whose builder takes positional parameters would not be
  described by `default_parameters()`; kinds use keyword-only parameters.
- The driver's neighbour replays are Donchian-specific; a candidate kind
  without its own neighbours fails the `--funnel-evidence-out` run with a
  named error rather than running the wrong neighbours.
