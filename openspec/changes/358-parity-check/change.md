# Change: 358-parity-check

**SDD change**: `358-parity-check`
**Date**: 2026-10-04
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/358-parity-check/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes parity check` replays one UTC day of the bars serve stored in the ledger through the same
strategy and fill kernel the backtest uses, and compares the decisions with the ones serve
recorded: which signals fired and which were executed. It catches a strategy or ordering bug that
only shows up live (the #343 R-4 class). It is read-only: the ledger is opened `mode=ro`, nothing
is written, and no strategy, fill model, sizing, fee, metric or kill rule changes. The main risk is
history: serve stores a bar in `live_bars` only after it handles it, so the replay can only warm up
from bars stored since #421.

## Scope

### In

- `hermes parity check [--date YYYY-MM-DD] [--db PATH] [--config-dir DIR] [--alert] [--offline]`.
- A warm-up gap filled from the public Binance klines read serve warms up with.
- `order_for_delivery` in `domain/market_data/bar_schedule.py`: the one close-time, 1d-first
  order. Serve (warm-up and `plan_delivery`) and the parity replay call it.
- `ParitySourcePort` and a read-only SQLite adapter (`SqliteParityReader`).
- `application/parity`: the decision model, the comparison, the alert, and the one-day check.
- An opt-in alert on a mismatch through the notification router.

### Out

- The nightly `hermes-parity.timer` / `.service` pair under `deploy/systemd/` (PR 2, after this
  merges; it waits for #610).
- Comparing fill prices (#353 owns fill-quality drift).

## Decisions

### D-1: Compare decisions, not prices

- **Decision**: Match each `SignalEmitted` serve recorded for the day against the replay's signals
  by pair, kind, side and signal time, then compare executed against not executed.
- **Alternatives**: Compare fill prices (live books are not the §6.2 synthetic book, so they
  differ by design; that is #353); compare only signal counts (hides an executed-vs-declined
  split).
- **Rationale**: A parity bug changes what the system does, not by how many basis points a fill
  differs. Serve records no decline reason in the ledger, so a reason is shown for the replay side
  only and never decides parity.
- **Cost of reversal**: LOW.

### D-2: One ordering helper, close time with 1d first on ties

- **Decision**: `order_for_delivery(bars)` sorts by the existing `delivery_key`. Serve's warm-up
  sort and `plan_delivery` call it, as does the parity replay.
- **Alternatives**: A second sort in the parity module (can drift from serve); merging the streams
  by open time (the #343 R-4 bug).
- **Rationale**: Replacing `list.sort(key=delivery_key)` with `sorted(..., key=delivery_key)` is
  the same stable sort over the same key, so the order serve delivers is unchanged.
- **Cost of reversal**: LOW.

### D-3: The replay starts where serve was

- **Decision**: Strategies are seeded (`StrategyPort.seed`, which never emits) with the stored bars
  closing before the day; the open position comes from the latest recorded entry signal, as serve
  restores it; the portfolio is rebuilt from the recorded fills before the day. Only the day's
  bars go through `replay`, one bar at a time so each signal's outcome is known on its own.
- **Alternatives**: Replay all history through `on_bar` (it opens positions that never existed,
  per `StrategyPort.seed`).
- **Rationale**: This is the state serve held when the day began.
- **Cost of reversal**: LOW.

### D-4: Alert is opt-in and carries no money

- **Decision**: `--alert` sends one `AnomalyDetected` (WARNING, source `parity`) through
  `route(EventClass.ANOMALY_DETECTED, ...)`. The text names bar close times, pairs and decisions
  only. Without the flag the command is silent, so tests and manual runs stay quiet; the nightly
  timer passes the flag.
- **Alternatives**: Always alert (noisy for manual runs); a dedicated event class (a router table
  change for no gain).
- **Rationale**: Pushed surfaces stay blind to shadow money (ruling 1).
- **Cost of reversal**: LOW.

### D-5: Warm-up is stored bars first, the exchange fills the gap

- **Decision**: The strategy is seeded from the stored bars before the day. When a stream has fewer
  than the configured floors (45 4h, 200 1d), the gap is filled from the same public Binance
  klines read serve warms up with (`BinanceLiveKlinesAdapter.warm_up` through `ClosedBarFeedPort`;
  no new HTTP client, no API key). Stored bars win wherever both exist (same open time), and the
  day's own bars always come from the ledger. `--offline` skips the exchange and keeps exit 3 on a
  shortfall, naming it. An exchange failure while filling is exit 3 with the cause.
- **Alternatives**: Stored bars only (useless until 200 days of `live_bars` exist); replay from a
  short history (an under-seeded strategy gives false mismatches).
- **Rationale**: Serve warms up from the exchange, so the exchange is the same source the live
  strategy was seeded from. The read returns the latest closed bars, so a past day needs
  `warm-up + bars since the day` rows; a day too far back (999-row cap) is exit 3.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Decisions are compared, one line per mismatch

- **A-1.1**: Same bars through both paths exit 0 and print `OK — N signals match for DATE`.
- **A-1.2**: A signal on one side only, or executed on one side and not on the other, exits 1 with
  one line naming the bar close time, the pair and both decisions.

### R-2: One delivery order

- **A-2.1**: `order_for_delivery` orders by close time with 1d first on ties, for any input order.
- **A-2.2**: A replay ordered by open time diverges from serve's recorded decisions and the check
  names the bar.

### R-3: Read-only

- **A-3.1**: The ledger is opened `mode=ro`; a write through the connection raises.
- **A-3.2**: A missing ledger or a day with no data exits 3 and the ledger file is byte-identical.
- **A-3.3**: Stored bars short and the exchange fills the gap gives a verdict; `--offline` with a
  shortfall exits 3. Tests never reach the network (fake fetcher, and the faked klines host).

### R-4: Opt-in alert without money

- **A-4.1**: `--alert` on a mismatch sends one alert naming the bar and the differing decisions.
- **A-4.2**: The alert text has no price, equity or P&L; without `--alert` nothing is sent.

## Design

```text
entrypoints/parity.py ── warm-up gap ──> BinanceLiveKlinesAdapter.warm_up (public klines)
        │  composes ──> serve._build_kernel / _load_experiment / _fees_at
        │                              SwingStrategyAdapter, load_config
        ▼
application/parity/check.py ──> ports/parity_source.py <── adapters/ledger/parity_reader.py (mode=ro)
        │                       application/backtest/replay.replay
        ▼
application/parity/decisions.py (Decision, compare, parity_alert; pure)
domain/market_data/bar_schedule.order_for_delivery (shared by serve and the replay)
```

Day window: a bar belongs to the UTC day it closes in, `(00:00, 24:00]`; the day's 1d bar is the
one closing at its end. A fill belongs to a signal when it is the first unclaimed fill of the same
pair and side within one 4h bar after the signal time (a live fill carries the book snapshot time).
Exit codes: 0 parity, 1 mismatch, 2 usage, 3 no data, no ledger or bad config.

## Test plan

1. Same bars through both paths: parity. An open-time order in one path fails and names the bar.
2. `order_for_delivery` property test: close time, 1d first on ties, any input order.
3. Extra signal, missing signal and executed-vs-declined each mismatch.
4. `--alert` sends one alert with no money; the ledger is byte-identical after a run; a missing
   ledger exits 3.
5. Stored bars short with the exchange filling the gap gives a verdict; stored bars win; `--offline`
   short exits 3; the default wrapper is exercised against the faked klines host.
6. `bash scripts/dev/pr-preflight.sh` passes.

Test tiers touched: unit, property

TDD posture: tests written after the production code (spec-driven from the acceptance list on #358).

## Risks

- **Exchange history can differ from what serve saw.** Serve was seeded at its own start from the
  exchange; a warm-up read later may carry a revised bar. Stored bars win, so the gap shrinks as
  `live_bars` grows. The read is public and read-only (no key); a klines outage is exit 3.
- **Recent days only.** The read reaches back at most 999 bars, so a day older than that cannot be
  filled and exits 3 (`--offline` and stored history still work).
- **Synthetic book.** The replay fills against the §6.2 synthetic book, serve against live depth.
  An order that serve declined on thin live depth, or filled where the synthetic book declines,
  shows as an executed-vs-declined mismatch. That is a real difference in what the system did, but
  it is not always a bug, so the alert says what differs and leaves the judgement to the operator.
- **Frozen items untouched.** Read-only against the ledger. No change to strategy, fill model,
  sizing, fees, metrics or kill rules. Pre-registration v1 §6, §9, §10 and §11 were checked: the
  replay reuses serve's kernel composition (`_build_kernel`) and fee lookup unchanged.
- **Serve's order is unchanged.** `list.sort(key=delivery_key)` became
  `sorted(..., key=delivery_key)` through `order_for_delivery`; the key and the stability are the
  same.

## Dependencies on other changes

- PR 2 (the nightly timer) starts after this merges and waits for #610.
