# Change: 1158-live-risk-inputs

**SDD change**: `1158-live-risk-inputs`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1158-live-risk-inputs/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

With real money (`allow_mainnet`), serve feeds three pre-registered inputs
that shadow keeps neutral: RO-02 (spread spike) reads the pair's 1-hour
median spread from the recorded spread samples, RO-05 and RO-06 (depeg) read
a USDT price polled every 60 seconds from Kraken's public USDT/USD book, and
the §10 recovery kill reads the days since the equity high. Shadow does not
change: no live inputs, no RO-02, no poll, as the #514 shadow-set ruling
requires. The pick is a new port, `LiveRiskInputsPort`, that hands the
executor plain numbers, so the decision modules still never import the
record-only risk input store. The main risk is that a missing input reads as
"halt" for RO-02 but as "skip" for the depeg rules (Risks).

## Scope

### In

- `hermes.ports.live_risk_inputs.LiveRiskInputsPort`: the three inputs.
- `hermes.application.live_risk_inputs`: `StablePriceWatch` (the 60-second
  poll) and `LedgerLiveRiskInputs` (spread samples, portfolio marks, watch).
- `KrakenPublicAdapter.get_usd_price(coin)`: the mid of a stablecoin's USD
  book (USDT `USDTZUSD`, USDC `USDCUSD`).
- `ShadowExecutor(live_inputs=...)` and `live_risk_engine()` (shadow set plus
  RO-02).
- serve: with `allow_mainnet`, build the watch and the live inputs, run the
  kernel on `live_risk_engine()`, poll beside the bar loop, close the Kraken
  client on release.
- The rule list and the failure-mode catalogue name the new tests.

### Out

- USDC: the domain's `MarketSnapshot.stable_price` holds one price, and the
  book is denominated in USDT, so only USDT is fed. A second stablecoin needs
  a field change in the domain (no follow-up filed; nothing trades in USDC).
- The single SA-9 and the 30-trade Sharpe kills stay neutral (not in #1158).
- The backtest: still neutral, as no pre-registration runs these there.

## Decisions

### D-1: A port of plain numbers, not the record-only store

- **Decision**: The executor takes `LiveRiskInputsPort` (Decimal / float
  answers); the ledger-backed implementation lives outside every decision
  module.
- **Alternatives**: Let `hermes.application.execution` read
  `ports.risk_inputs` directly (breaks the `risk-inputs-are-record-only`
  contract); compute the median inside the executor from its own book reads
  (it reads a book a few times a week, too few for a 1-hour median, #835).
- **Rationale**: The import contract keeps recorded rows out of decisions in
  shadow; real money opts in through one injected port, so shadow cannot pick
  the inputs up by accident.
- **Cost of reversal**: LOW.

### D-2: The 1-hour median is recomputed from the samples at order time

- **Decision**: `median_spread_1h(pair, now)` is the median of the pair's
  sample spreads with `now - 1h <= book_time < now`; no sample gives `None`,
  which the executor passes to RO-02 as a zero median.
- **Alternatives**: The newest sample's stored `median_spread_1h` (can be up
  to 5 minutes old and covers the hour before that sample, not before the
  order).
- **Rationale**: Pre-registration §5.5 "10x the 1H rolling median"; RO-02
  already halts conservatively on a zero median.
- **Cost of reversal**: LOW.

### D-3: USDT mid from Kraken's public ticker, polled every 60 seconds

- **Decision**: `StablePriceWatch` polls `get_usd_price(USDT)` on a worker
  thread every 60 seconds, the first poll at start, and answers `None` once
  the newest reading is over 3 minutes old.
- **Alternatives**: Last trade price (a thin book's last trade can be
  minutes old); the Binance USDC/USDT book (a ratio of two stablecoins, not a
  price in USD).
- **Rationale**: success-criteria Spec 8 names the Kraken public ticker and a
  60-second poll for RO-06; the adapter already exists and needs no key.
- **Cost of reversal**: LOW.

### D-4: The recovery days count only after an 8% drawdown

- **Decision**: `days_since_equity_high` is the time from the last equity
  high in the portfolio marks, in days, when the drawdown since that high
  reached 8%, else 0.0.
- **Alternatives**: Feed the raw days (the evaluator would then kill after
  90 days below a high of any depth, which §10 does not say); change the
  evaluator to read the drawdown (a kill-rule change, which needs the Code
  Owner).
- **Rationale**: §10 reads "time-to-recovery > 90 days from any drawdown
  >= 8%". Gating the input keeps the evaluator and its signed threshold as
  they are.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Each input reaches its rule with real money

- **A-1.1**: `uv run pytest tests/unit/application/execution/test_shadow.py -k "ro02 or usdt or ninety or shadow_fills"` exits 0: a spiked spread, a 4% depeg and 91 days each refuse an entry the neutral inputs fill.
- **A-1.2**: `uv run pytest tests/e2e/test_serve_cycle.py -k real_money` exits 0: over the recording, real money with USDT at $0.96 opens no position and logs RO-06 halts.

### R-2: Shadow stays on the backtest's set

- **A-2.1**: `uv run pytest tests/unit/application/risk_inputs/test_risk_inputs_unwired.py tests/unit/entrypoints/test_serve_live_risk_inputs.py` exits 0.
- **A-2.2**: `uv run lint-imports` keeps `risk-inputs-are-record-only`.

### R-3: The inputs are computed as pre-registered

- **A-3.1**: `uv run pytest tests/unit/application/test_live_risk_inputs.py tests/unit/adapters/exchanges/test_kraken_public.py` exits 0.

### R-4: The catalogue names the tests

- **A-4.1**: `uv run pytest tests/integration/scenarios -k "rules or failure"` exits 0, with no `issue #1158` gap left.

## Design

```text
src/hermes/ports/live_risk_inputs.py           new   LiveRiskInputsPort
src/hermes/application/live_risk_inputs.py     new   StablePriceWatch, LedgerLiveRiskInputs
src/hermes/adapters/exchanges/kraken_public.py edit  get_usd_price(coin) -> Decimal
src/hermes/application/execution/shadow.py     edit  live_inputs=, live_risk_engine()
src/hermes/entrypoints/serve.py                edit  wire when allow_mainnet; poll task; close
```

```python
class LiveRiskInputsPort(Protocol):
    def median_spread_1h(self, pair: Pair, now: datetime) -> Decimal | None: ...
    def stable_price(self, now: datetime) -> Decimal | None: ...
    def days_since_equity_high(self, now: datetime) -> float: ...
```

In `ShadowExecutor`, `live_inputs is None` keeps today's values
(`median_spread_1h = current spread`, `stable_price = None`,
`days_since_equity_high = 0.0`). Otherwise the snapshot takes
`live_inputs.median_spread_1h(pair, now) or 0`, `live_inputs.stable_price(now)`
and `live_inputs.days_since_equity_high(now)`.

serve, in `_wire_on_ledger`: `_stable_price_watch(config, ...)` returns
`(None, None)` without `allow_mainnet`; otherwise a watch over
`KrakenPublicAdapter.get_usd_price(Currency.USDT)` (its client closed by the
start-up exit stack on failure and by `_release` after a run). `run` adds
`watch.run(shutdown)` to the TaskGroup beside the bar loop and the spread
sampler; a failed poll is logged (`serve.stable_price.failed`) and skipped,
so the task never ends the group.

Run against the current tree:

```text
$ uv run pytest -q tests/unit/application/test_live_risk_inputs.py tests/unit/entrypoints/test_serve_live_risk_inputs.py tests/unit/application/execution/test_shadow.py tests/unit/adapters/exchanges/test_kraken_public.py
95 passed
$ uv run pytest -q tests/e2e/test_serve_cycle.py
6 passed
$ uv run lint-imports
Contracts: 13 kept, 0 broken.
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the PR.
4. Code Owner review.

Test tiers touched: unit, property, contract, integration, e2e

## Risks

- **Missing inputs act differently**: no spread sample in the hour halts
  every entry (RO-02's own zero-median rule), so the first 5 minutes after a
  boot take no entry; no fresh USDT price skips the depeg rules, as before
  the feed. A Kraken outage therefore lifts the depeg guard silently except
  for the `serve.stable_price.failed` warning. Failing closed would change
  RO-05/RO-06's pre-registered "skip on no price"; left to the Code Owner.
- **Boundary of the recovery kill**: the evaluator fires at `>= 90` days and
  is fed fractional days, while §10 says "> 90" and `hermes risk-inputs`
  counts whole days `> 90`. The two differ only at the 90-day instant; the
  evaluator's threshold is signed and not changed here.
- **Kraken symbol**: Kraken answers the USDT book as `USDTZUSD`; a rename
  would fail every poll. The contract test
  `test_usdt_usd_book_answers_under_the_mapped_symbol` checks the real
  endpoint.
- **Poll cadence vs a test clock**: the poll waits on wall seconds and stamps
  readings with serve's clock; under a fast test clock the reading goes stale
  between bars. Real money runs only in prod, on the wall clock.

## Dependencies on other changes

- None. No ADR changes; pre-registration v1 §5.5 and §10 are read, not
  changed.
