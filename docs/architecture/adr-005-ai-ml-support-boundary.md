# ADR-005: AI/ML support boundary — ML supports the deterministic engine, monitors our behavior, and assists development; it never generates trade decisions

**Date**: 2026-08-20
**Status**: Accepted
**Accepted**: 2026-08-21
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: —

## Context

ADR-001 established that the LLM does not generate trade decisions. That
decision was grounded in specific evidence about *language models* as agents.
It leaves an adjacent question unanswered: **what about non-LLM machine
learning?** Random forests, HMMs, LightGBM, autoencoders, HRP, Bayesian
optimization, change-point detectors — these are not LLMs, and the arguments
against LLM agents (memorization, prompt injection, cost-per-token) do not
apply symmetrically.

The temptation exists to reach for ML any time a decision surface looks
"interesting". FreqTrade's FreqAI module is a mature FOSS example: plug in
LightGBM or PyTorch, walk-forward retrain nightly, generate a probability
score, act on the score. It works as a tool. The community consensus is that
FreqAI does not confer profitability — strategy design is still 90% of the
work — but the mechanical answer to "can I add ML to a Python trading bot?"
is *yes, trivially*.

Hermes deliberately chooses a narrower posture. The question is not whether
ML can be plugged in; it is which uses of ML at *our* scale earn their
complexity, which are cargo-culting, and which cross the ADR-001 boundary in
non-obvious ways.

## Decision

ML at hermes is permitted in **four categories**, each with an explicit
scale-based unlock schedule and an ADR-001-derived rule. Everything outside
these categories is rejected until an explicit follow-up ADR justifies it.

### Category A — Support the deterministic engine (permitted; ADR-001 rule: outputs sizing/eligibility, never signals)

ML in this category **feeds parameters into deterministic rules**. It does not
say "buy" or "sell". It says "the deterministic rule that already exists
should use these numbers this period" or "this asset is or isn't eligible for
the deterministic filter to consider".

- **Volatility forecasting** (EWMA λ=0.94 for position sizing; Yang-Zhang
  realized-vol for dashboards). Output flows into `SizingPolicy` which the
  rebalancer + Donchian engine consume. ML never chooses direction.
- **Regime filter** (rule-based: ADX + realized-vol thresholds initially; HMM
  2-3 state model at €10k+ AUM). Output is a binary "engine active / paused"
  or "widen band / tighten band" — never a directional call.
- **Portfolio construction** at basket expansion time (HRP allocation once
  the satellite exceeds 8 assets). Output is `{asset → weight}`; the
  rebalancer executes to hit weights. ML never says "buy X at time T".

### Category B — Analyze OUR behavior (permitted; ADR-001 rule: post-trade observation only)

ML in this category **watches what the engine already did** and flags
anomalies. It is monitoring, not decisioning.

- **SPC control charts** (EWMA + CUSUM) on fee ratio, slippage vs expected,
  fill latency, order-rejection rate. Anomaly → notification, engine
  optionally paused pending review.
- **Execution-quality tracking**: arrival price vs fill price per trade,
  implementation shortfall aggregated weekly, benchmark against fee floor.
  No decisioning — just measurement.
- **Autoencoders / Isolation Forest** on engine-behavior vectors — permitted
  *only* after 6+ months of clean production data. Explicitly rejected in
  Phase 0/1 because false-positive rate on endogenous noise makes them worse
  than SPC at low data volumes.

### Category C — Development assistance (permitted; ADR-001 rule: offline, never on production data at trade time)

ML in this category **helps humans design parameters**. It never runs in
production during a live cycle.

- **Bayesian hyperparameter search** (Optuna with TPE sampler) on
  historical data, gated by Combinatorial Purged Cross-Validation
  (Lopez de Prado 2018) to prevent backtest overfitting. Run offline;
  best-per-CPCV-fold configs get promoted to `config/strategy.yaml` after
  human review.
- **Historical-data change-point exploration** (`ruptures` library, offline)
  to identify past regime shifts for calibration research. Not live.
- **Feature-engineering exploration** — free-form Jupyter research on
  historical data. Zero production path.

### Category D — Rejected (does NOT cross the ADR-001 boundary safely)

- **Direct signal generation** (LightGBM / PyTorch / XGBoost predicting
  "up" or "down") — this IS the ADR-001 line, wearing a different hat.
  FreqAI, TensorTrade-style DRL agents, Reinforcement Learning strategy
  discovery all fall here.
- **LLM-generated code paths in production** (LLM writes strategy code
  that runs against real capital). ADR-001 covers this by transitive
  extension: if a human hasn't reviewed and merged the code, it doesn't
  execute against real money.
- **Adversarial or generative models for market simulation used to seed
  decisions** — synthetic data for testing is fine; synthetic-data-driven
  live trading is not.
- **Online-learning strategies** (parameters that update themselves
  intra-cycle without human approval). Every parameter change is a config
  PR; no autonomous drift.

### Non-ML techniques we deliberately keep in this ADR's scope (permitted, straightforward)

- Yang-Zhang realized-volatility estimator (statistical formula, not
  learned)
- ATR / EMA / SMA / Donchian channels (deterministic technical indicators)
- Threshold rebalancing (deterministic algorithm)
- FIFO tax accounting (deterministic algorithm)

These are not ML but are called out to distinguish them from the ML
techniques above and to make it explicit that no "learned model" sits
in the trade path.

## Why

### Why permit Category A at all — isn't the point that the engine is deterministic?

The engine is deterministic **at the decision surface**: "if position weight
< target - band, buy to target" is a rule that produces the same output given
the same inputs. What the rule receives as input can legitimately be a
statistically-estimated parameter: today's target position size, computed
from today's forecast volatility. That does not make the engine
non-deterministic — it makes the inputs current.

The alternative is worse: hardcode a position size that ignores volatility,
overtrade in low-vol periods, undersize in high-vol periods, and pretend
that ignoring information is a virtue.

The rule that keeps Category A safe: **the ML output is a parameter to a
deterministic rule; the rule's logic is human-written and reviewable.** If
the day comes when someone proposes "let the model output the decision
directly", that is a Category D violation and blocked.

### Why SPC and execution logging *before* fancier anomaly detection

Two reasons:

1. **Interpretability at low data volumes.** SPC control limits are set from
   in-control historical variance; a point outside the limit is a
   statistically-explicit anomaly with a p-value interpretation. Isolation
   Forest at 30-100 trades/year worth of behavior signal is over-fitting to
   noise.
2. **Cheap and boring wins.** EWMA-CUSUM implementations are ~50 lines of
   Python and have decades of industrial deployment history. Autoencoders
   are ~500 lines and require tuning that nobody has time for at solo-retail
   scale.

The unlock: after 6+ months of clean SPC data — enough baseline to know
what "normal" looks like — autoencoders become considerable. Not before.

### Why Optuna+CPCV, not raw grid search

Walk-forward alone allows a subtle form of overfit: choosing the parameter
set that had the highest average across walk-forward windows biases toward
parameters that happened to fit the *particular sequence* of windows. CPCV
(Combinatorial Purged Cross-Validation) is Lopez de Prado's standard mitigation:
sample non-adjacent train/test splits with an embargo, average across many
sampled configurations, choose the config with best worst-case rather than
best-average.

Optuna's TPE sampler is the standard implementation and is what the FreqAI
community uses. There is no need to reinvent it.

The consequence for hermes: Phase 1 Donchian parameter selection uses
Optuna+CPCV as its default workflow, not manual grid search.

### Why HRP requires 8+ assets to be worthwhile

Hierarchical Risk Parity (Lopez de Prado 2016; extended by
Antonov/Lipton 2024) allocates capital using the correlation-clustered
dendrogram rather than covariance inversion. It is genuinely better
than mean-variance out-of-sample when the covariance matrix is
ill-conditioned — which is exactly what happens in high-dimensional
crypto baskets.

At **6 assets or fewer**, the covariance matrix is small enough that
mean-variance and equal-weight are within noise of each other. HRP's
value emerges as dimensionality grows and clusters become meaningful.

The hermes satellite launches with 6 assets by design. HRP is
therefore staged as a **when-we-expand** capability, not a Day 1
requirement.

### Why FreqAI is explicitly rejected

FreqAI is not a bad tool. It works as advertised: LightGBM or PyTorch
model, walk-forward retrain, prediction score, act on the score. The
issue is not quality; it is what it does.

FreqAI **generates signals**. A LightGBM predicting "next-hour direction"
and the strategy acting on the prediction is functionally identical to
an LLM outputting "BUY". The mechanics differ; the ADR-001 concern
(a learned model whose interior is opaque to the operator makes trade
decisions) applies identically.

Rejecting FreqAI is not rejecting ML — it is holding the ADR-001 line
consistently against the ML variant of the same failure mode.

### Why "cargo-culting" is a real risk at retail

Retail traders systematically over-reach for complex tooling. Every
GitHub trending list this year has a "LSTM crypto predictor". Almost
none of them produce profit; almost all of them look impressive.

The hermes discipline: **every ML addition must show, on out-of-sample
data with CPCV, a measurable improvement over the deterministic
baseline it replaces**. Absent that evidence, the ML addition is
overhead to maintain without clear payoff.

## Consequences

### Positive

- **The ADR-001 boundary is preserved coherently.** Non-LLM ML is not a
  loophole; the same "learned model does not decide trades" rule
  applies.
- **Prioritization is explicit.** SPC + EWMA vol + execution logging
  are P0. Fancier tools (HRP, autoencoders, HMM) unlock at documented
  AUM thresholds. No one wastes time on Phase 2 tooling in Phase 0.
- **Every Category A output is human-inspectable.** Volatility forecast
  = one number. Regime state = one label. HRP weights = one table.
  Nothing is a black box the operator can't sanity-check in five
  minutes.
- **Category D is bright-line.** New tooling proposals get evaluated
  against the four-category grid; if it lands in D, it doesn't happen
  without an ADR that supersedes this one.

### Negative

- **We give up whatever alpha FreqAI-style signal ML might produce.**
  This is deliberate; the evidence base (StockBench, Nof1 Arena for
  LLMs; FreqAI community reports for classical ML) does not support
  the tradeoff at retail scale.
- **CPCV + Optuna have a learning curve.** Neither is included in
  scipy/sklearn by default; the first parameter-sweep run is
  investment.
- **HMM is unlocked only at €10k+.** Below that, the rule-based
  regime filter is the only tool available even if HMM would offer
  marginal improvement.
- **SPC baseline requires 6+ months of clean data.** Until then, the
  monitoring is minimally more than "did the trade fire? did it fill?
  what was the fee?" — enough to catch first-order issues, not enough
  for statistical anomaly detection.

### Neutral

- **The ML packages we depend on** are all mainstream and permissively
  licensed: `numpy`, `pandas`, `scipy`, `scikit-learn`, `optuna`,
  `ruptures` (later). No exotic dependencies, no supply-chain
  surprises.
- **Every Category A model has a "fallback to constant" mode** wired
  in from Day 1 — if the model fails or returns something implausible
  (a volatility forecast that is negative or 100×), the deterministic
  fallback constant kicks in and the anomaly is logged. Fallback is
  not a nice-to-have; it is required.
- **Category C runs on the user's laptop, not on the VPS.** Optuna
  sweeps are computationally heavy; the VPS has 2 vCPU / 4 GB and
  is reserved for engine + notifications.

## Setup priority order (ESSENTIAL first)

Locked from research findings; each item unlocks the next once shipped.

| Order | Capability                                             | Category | AUM unlock           | Rationale                                                                       |
| ----- | ------------------------------------------------------ | -------- | -------------------- | ------------------------------------------------------------------------------- |
| 1     | Yang-Zhang realized vol + EWMA(λ=0.94) forecast        | A        | Day 1                | Sizing depends on volatility; not having it is a bug, not a feature             |
| 2     | Execution logging (arrival + fill price per trade)     | B        | Day 1                | Retail slippage is +0.4% vs institutional; cannot improve what we don't measure |
| 3     | SPC charts (EWMA + CUSUM) on fees / slippage / latency | B        | Phase 1              | Anomaly detection with an interpretable baseline                                |
| 4     | Optuna + CPCV for parameter sweeps                     | C        | Phase 1              | Prevents overfit-by-grid-search on Donchian and rebalance-band tuning           |
| 5     | Rule-based regime filter (ADX + vol thresholds)        | A        | Phase 1              | Pauses engine in extreme regimes; simple, robust, human-readable                |
| 6     | HRP allocation                                         | A        | 8+ assets            | Adds value only when basket exceeds mean-variance-stable dimensionality         |
| 7     | HMM regime model                                       | A        | €10k+                | Captures state transitions that rule-based filter misses                        |
| 8     | Autoencoders / Isolation Forest on behavior            | B        | 6+ months clean data | Only interpretable once SPC baseline exists                                     |
| 9     | BOCPD live change-point detector                       | A        | €50k+                | Real-time regime shifts; heavy-tail-tolerant                                    |
| 10    | Execution algorithms (TWAP / VWAP)                     | A        | €10k+ ticket         | Payoff only when order size moves the market                                    |

## What would change this decision

- **Credible published evidence that classical-ML signal generators
  beat deterministic + volatility-aware baselines out of sample net of
  costs.** The current published evidence does not support this
  (StockBench 2025, FINSABER 2025, various FreqAI community
  post-mortems). If that changes, Category D re-opens for a targeted
  supersession ADR — not blanket reversal.
- **User's AUM grows past €100k+.** Some Category D techniques
  (execution algorithms, cross-venue routing) start earning their
  complexity at that scale. Would still not permit signal-generating
  ML; would consider execution ML.
- **Regulatory requirement to log model predictions and inputs for
  automated trading.** Would tighten Category C's audit trail and
  require a `model_predictions` table, but does not change the
  category boundaries themselves.

## References

- Lopez de Prado, *Advances in Financial Machine Learning* (2018) —
  CPCV, HRP, meta-labeling framework
- Antonov, Lipton, Lopez de Prado (2024) — updated HRP variants
- Tandfonline 2022 — EWMA competitive with GARCH for crypto VaR/ES
- Binance Research 2024 — retail slippage benchmarks
- StockBench (arXiv:2510.02209, 2025) — LLM agents fail to beat
  buy-and-hold on contamination-free data
- FINSABER (arXiv:2505.07078) — LLM advantages deteriorate on long
  histories
- FreqTrade FreqAI docs and community post-mortems

## Cross-references

- [ADR-001](./adr-001-llm-as-supervisor.md) — the parent principle
  (learned models do not decide trades) that this ADR extends
- [ADR-002](./adr-002-tax-posture.md) — trade frequency implications
  bear on recharacterization guard; ML-driven regime pauses reduce
  frequency
- [ADR-003 section 05](./adr-003-solution-architecture/05-signal-engines.md)
  — where rule-based regime filter and vol-aware sizing plug in
- [ADR-003 section 08](./adr-003-solution-architecture/08-llm-supervisor.md)
  — LLM supervisor is orthogonal to (and coordinated with) ML monitoring
- ADR-007 — Testing strategy (property-based tests for statistical
  computations) *(pending)*

## Pending

- **`config/ml.yaml` — parameters for EWMA λ, Yang-Zhang window,
  SPC control limits, Optuna trial budget.** Values default to
  research recommendations; per-parameter tuning is a Phase 1
  activity.
- **Fallback-to-constant tests** — every Category A model needs a
  test that verifies fallback kicks in when the model returns
  implausible output. Written alongside the model.
- **CPCV embargo period** — Lopez de Prado suggests roughly the
  serial-correlation window of returns; for hermes' hourly bar
  data, a 24-72 hour embargo is a reasonable starting point.
  To be finalized in Phase 1 tuning work.
