---
name: hermes-scenario
description: Use when adding or changing an end-to-end scenario, a chaos case or a generated-path seed in tests/integration/scenarios, to drive the real serve through the fake market and check it with the oracle.
---

# Scenarios: the real serve on a fake market

Scenario tests run the real `hermes serve` (real adapters, real ledger) against
a fake exchange speaking the real wire format. Nothing reaches a network.
They live in `tests/integration/scenarios/`.

## Pieces

- `tests/support/fake_market/`: the fake exchange (klines, depth, FX) and the
  `harness.serve(...)` entry point. It can delay or never publish bars
  (`late_bars`), send corrupt rows (`corrupt_bars`), drop the book
  (`book_outages`), take FX down (`fx_down`) and fail the notifier.
- `markets.py`: hand-scripted markets, one per engine path
  (`chandelier_exit`, `time_stop`, `three_breakouts`, ...). Each returns a
  `Scenario` with the market and the boot instant.
- `conftest.py`: the `run_scenario` fixture. Calls in one test share one
  ledger, so a second call is a restart. Every run is held to the per-bar CPU
  budget.
- `ledger.py`: `outline(db)` reads back signals and trades.
- `faults.py`: chaos tools: fail the Nth ledger write or commit (a process
  kill), storage errors, a skewed clock.
- `paths.py`: seeded generators (`random_walk`, `hater`) for whole 30-day
  paths with faults and restarts. One seed reproduces the whole path.
- `oracle.py`: checks every pre-registration rule after every bar, and that
  a restarted run leaves the same ledger as a straight one.
- `rules.py` / `test_rule_list.py`: each signed pre-registration rule and the
  tests that prove it.
- `failure_modes.py` / `test_failure_mode_list.py`: every known way Hermes
  fails, and its answer.

## Adding a scripted scenario

1. Reuse a market from `markets.py`, or add a builder there. Prices are
   invented round numbers starting at 100 on 2024-01-01. Never use recorded
   or live prices, and nothing from 2025-09-01 on.
2. Drive it with `run_scenario(scenario, ...)` and assert on the ledger
   (`outline(run.db)`), the exit code and the log events
   (`run.log_events("serve....")`). Pin exact trades and signals.
3. For a restart, call `run_scenario` again with a later `live_from`.
4. Name the rule or catalogue entry in the docstring and update
   `rules.py` or `failure_modes.py` to point at the new test.

## Adding a generated-path seed or rule

- `HERMES_SCENARIO_SEEDS=N` or `N@START` runs more seeds locally; the PR seeds
  are fixed so CI is reproducible.
- A failing seed is a bug report: fix the code and add the seed as a fixed
  example in the same PR. It never lands as an xfail; if the fix needs a
  decision, file the issue with the seed and hold the example until then.
- A new rule goes in the oracle with a deliberate break in a scratch copy, to
  prove the oracle catches it.

## Budget

The integration tier has a time budget (ADR-018). Prefer one scenario that
proves several catalogue entries over several near-copies, and use CPU time,
not wall time, for any timing assertion.
