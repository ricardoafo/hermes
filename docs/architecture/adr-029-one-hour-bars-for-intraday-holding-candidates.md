# ADR-029: 1h bars for intraday-holding candidates, in backtest, shadow and prod together

**Date**: 2026-10-18
**Status**: Accepted (merged in PR #1114)
**Deciders**: Maintainer
**Supersedes**: nothing. Extends [ADR-026](adr-026-research-batch-and-signed-champion-rule.md) (what a candidate may
be) and the market-data config's `4h`/`1d`-only rule. Tracking issue: #1105.

---

## Context

Every strategy so far decides on 4h bar closes, with the daily close as a filter. The engine, serve and their config
accept only `4h` and `1d` (`MarketDataConfig` in `src/hermes/infrastructure/config/models.py`), although
`KlineInterval` already names `1m`, `5m` and `1h`, the backtest ingester already downloads `1h` bars by default, and
it fetches `1m` bars on request (issue #1104).

On 2026-10-18 the Code Owner asked for the research funnel to test any swing idea, including trades that open and
close within a day when the idea calls for it. Scalping on bars of 15 minutes or less stays out. He also asked
whether finer data could improve strategies without trading on it, and set one condition: whatever backtests can
do, shadow and prod must do too.

Three facts bound the answer:

- **Cost.** A round trip costs about 40 bps at our fee tier: 10 bps fee each side plus 5 bps entry and 15 bps exit
  slippage (pre-registration v1 §6.2). Issue #1104 measures whether 15 bps is realistic. A multi-day 4h swing
  barely notices 40 bps; a move of a few hours often does.
- **Tax posture.** ADR-002's guard pauses new signals above 500 trades in a rolling 90 days, the informal line where
  the tax office starts to see a day trader.
- **Statistics.** Every logged trial counts in DSR, and BH runs over every tested candidate (ADR-026 §4). More
  timeframes and more inputs mean more trials and a higher bar for each one.

## Decision

### 1. `1h` becomes a decision interval in backtest, shadow and prod, in one engine change

`1h` joins `4h` and `1d` in the market-data config, the backtest data on server-01 (ADR-023), the replay and serve.
No release enables `1h` decisions in one of them without the others. Backtest-serve parity (ADR-025) covers `1h`
candidates the same way it covers `4h` ones. It is an engine change, so the release carrying it is a MINOR bump
(ADR-027).

### 2. Nothing decides on bars shorter than 1h

A candidate opens and closes positions only on `1h`, `4h` or `1d` closes. `15m`, `5m` and `1m` are never decision
intervals. Changing this needs a new ADR, backed by `1h` results that earn it.

### 3. Finer bars may feed a decision, named up front

A candidate may read inputs derived from `1m` or `5m` bars inside its decision bar: for example volatility measured
from 5-minute returns, which is a steadier estimate than ATR on 4h bars, or whether a breakout held across the bar.
Each such input is named in the candidate's hypothesis card and pre-registration, with its formula, before any
trial runs; one that is not named is not used. A candidate that reads them makes serve follow that feed live, so
shadow and prod compute the same input (§1 applies).

### 4. Trade count stays clear of the tax line

ADR-002's 500 trades per rolling 90 days is an account-wide limit on real trades. A candidate's Train run fails its
gate if any rolling 90-day window holds more than 250 round trips, so a champion alone uses at most half the
account's room. The cap is a gate row in the candidate's pre-registration, checked by the generic gate (ADR-026).

### 5. Each candidate shows it clears its costs

A candidate holding for less than a day states in its card the median move per trade it expects and the round-trip
cost it is charged. Its pre-registered costs use the exit slippage measured in issue #1104, if the Code Owner
changes the assumption from that measurement; otherwise the current 15 bps. A 2x cost-stress replay stays part of
the funnel evidence.

### 6. Funnel accounting does not change

A `1h` candidate is a candidate like any other: one per family in a batch, its trials logged and counted in DSR, BH
across the batch. A `1h` version of an existing idea belongs to that idea's family and spends that family's
Validation budget (ADR-026 §3); a new timeframe is not a free new family. The first research batch is untouched.

### 7. Data, and the sealed window

Backtest data for `1h` comes from the same archive and ingester as `4h`; `1m` stays on request. Nothing changes in
ADR-023 §4: no data from the sealed out-of-sample window is fetched for research. Serve already stores every closed
live bar it processes (`LiveBarStorePort`); `1h` bars join that record. Those bars stay out of research until the
seal lifts.

### 8. Optional: minute prices around live stops

Serve may record `1m` prices around each stop trigger so the drift check can compare a paper stop with what an
exchange stop would have filled at, the live twin of issue #1104. Only the engine logs them; no AI session reads
them while the window is sealed (ADR-012 D-02). This is a separate build issue and is not needed for §1-§6.

## Why

- The Code Owner wants holdings shorter than a day to be testable; `1h` is the shortest bar where the cost and the
  tax line still leave room.
- One switch for all three runtimes keeps a candidate's shadow comparable with its backtest, which is what makes a
  shadow verdict mean anything.
- Naming finer inputs up front keeps their benefit (better volatility, better reading of a bar) without opening a
  search over unnamed features that the trial count would then have to pay for.

## Consequences

### Positive

- The funnel can test intraday-holding ideas and multi-day ones on the same footing.
- Better volatility inputs become available to every candidate, `4h` ones included.

### Negative

- More engine surface: a third decision interval in serve, warm-up depth for `1h`, and a live `1m`/`5m` feed when a
  candidate uses one.
- `1h` candidates trade more, so they reach DSR's trial burden and the cost hurdle sooner.

### Neutral

- Today's strategies, the signed first batch and v1's warm-up are unaffected.

## Build issues (filed when this ADR is accepted)

1. Market data: `1h` in `MarketDataConfig`, warm-up depth, serve scheduling for 24 bars a day, live bar store.
2. Replay and parity: `1h` candidates in the backtest replay and the ADR-025 decision replay.
3. Finer inputs: a `1m`/`5m` feed in serve and in the replay, read only by candidates that name it.
4. Gate: the 250-trades-per-90-days row in the generic gate and the batch format.
5. Optional (§8): live minute prices around stop triggers.

## What would change this decision

- Issue #1104 shows exit costs so high that `1h` holdings can't clear them at our fee tier.
- The tax posture changes, or a lower fee tier changes the cost arithmetic.

## Cross-references

- [ADR-002](adr-002-tax-posture.md) — trade-count guard (500 per 90 days)
- [ADR-012](adr-012-continuous-ai-evaluation.md) — D-02, no AI reads sealed data
- [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) — shadow Ready and Done, drift check
- [ADR-023](adr-023-backtest-runs-on-server-01.md) — backtest data on server-01, §4 sealed window
- [ADR-025](adr-025-train-decision-replay-on-engine-prs.md) — decision replay on engine PRs
- [ADR-026](adr-026-research-batch-and-signed-champion-rule.md) — research batches and trial accounting
- [ADR-027](adr-027-zero-major-versions-until-real-capital.md) — engine change is a MINOR bump
- Issue #1104 — stop-exit slippage measurement
