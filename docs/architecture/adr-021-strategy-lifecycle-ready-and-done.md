# ADR-021: Strategy lifecycle — Ready and Done rules for every stage, a valid shadow clock for v1, and the rules for a multi-strategy lab

**Date**: 2026-10-06
**Status**: Accepted (merged in PR #799)
**Deciders**: Maintainer
**Supersedes**: none. It applies [pre-registration v1](../experiments/swing-mvp1/pre-registration-v1.md) §7 and §10 as
written and builds on [ADR-012](adr-012-continuous-ai-evaluation.md) D-11 (research loop) and
[ADR-014](adr-014-pre-registration-binds-real-capital.md) (real capital).

---

## Context

Shadow mode started on 2026-10-04 (PR #617), but the backtest gate that pre-registration v1 puts before it never ran:
the lab notebook has no `train` or `validation` entry, no experiment trial is logged, and `hermes certify` has no
record, so v1 is still `UNTRUSTED` (issue #735). Pre-registration §10 says a strategy that fails its backtest does not
proceed to shadow, and §7 orders `BACKTEST_CERTIFIED` before `SHADOW_CERTIFIED`. The days run so far therefore prove
nothing about v1.

Checking that found two more gaps. The shadow Sharpe kill (§10) and the M-09/M-10 drift metrics exist in code, but
`hermes serve` never calls them. And no rule says when a strategy is ready to enter a stage or done with it. That
matters more once the challenger lab (issue #361) runs many strategies.

The Code Owner asked for one scientific lifecycle with a Definition of Ready and a Definition of Done per stage. He
approved the plan, every recommendation plus a tenth decision on shadow length, on 2026-10-06.

A literature check informed the plan (sources below):

- **A short forward test cannot prove an edge.** Telling an annual Sharpe of 1 from 0 at 95% confidence takes about
  5,900 4h bars, roughly 2.7 years (minimum track record length). After 90 days of a true Sharpe of 1, the
  probabilistic Sharpe ratio is only about 0.69.
- **History is a budget.** About 5 years of data supports roughly 45 independent configurations before the best one
  is expected to look good by luck (minimum backtest length).
- **DSR is a probability.** Its authors use 0.95 as the bar, not 0.7.
- **Many strategies need family-wise statistics.** False discovery rate control is suited to choosing candidates.
  Stepdown, SPA or model-confidence-set tests are suited to declaring a winner. Shrinkage within a family keeps lucky
  variants from ranking first.

## Decision

### 1. Eight stages, each entered only when Ready and left only when Done

| # | Stage | Ready when | Done when | Decides |
| --- | --- | --- | --- | --- |
| 1 | Idea | A hypothesis card names the mechanism, the expected sign, a cap on free parameters and a falsification test | The card is hashed and committed before any run | Code Owner picks cards |
| 2 | Research on Train | Card committed; trial budget declared | Every run logged as a trial; one frozen rule set, or the card is dropped | Code scores; AI may propose from Train only |
| 3 | Pre-registration | Rules, parameters, universe, sizing, fees, kill rules, thresholds, shadow length frozen | Signed; hash in the lab notebook | Code Owner |
| 4 | Backtest gate | Signed; engine version pinned | §9 metrics pass on Train + Validation and DSR over all trials passes; one logged Validation touch; `hermes certify` records it | Code |
| 5 | Shadow | Shadow Ready (§2) | Shadow Done (§2) | Code; Code Owner confirms |
| 6 | Exam on unseen data | Shadow certified; the exam window has closed | One shot, the pre-registered H1 test | Code |
| 7 | Live approval | Exam passed | Code Owner types the approval phrase | Code Owner |
| 8 | Live certified | Live approved | 90 live days, no kill fired | Code |

Stages 4 to 8 are the pre-registration §7 states that `hermes certify` already enforces. A failure at any stage ends
that pre-registration: a post-mortem within 7 days, a row in the lessons register, and, if the idea survives, a new
card. A changed strategy is a new variant and starts again at stage 1. A running variant is never edited.

### 2. Shadow Ready and Shadow Done

Shadow day 0 is the first bar after every Ready rule holds.

**Ready**, all of:

1. Signed pre-registration with its hash in the lab notebook.
2. Backtest gate recorded by `hermes certify`.
3. Known defects that change trading decisions fixed.
4. The §10 shadow kills and the §7 drift check run inside `hermes serve` on their own.
5. Engine version, config hash, pre-registration hash and data manifest hash recorded at day 0.
6. Backtest and serve make the same decisions on the same bars (`hermes parity check`, PR #698).
7. The strategy has its own strategy id, virtual capital and ledger rows.

**Done**, all of:

1. 90 counted days since day 0.
2. At most 1% of bars missed, and every gap recovered.
3. Sim-vs-live drift p50 below 30 bps, and M-09 and M-10 pass (decision of 2026-10-03).
4. No §10 shadow kill fired.
5. Every engine change during the window showed zero decision changes (§4).
6. A closing lab notebook entry, and `SHADOW_CERTIFIED` recorded.

**Ends early.** A drift breach sends the strategy back to `UNTRUSTED`; the engine is fixed and the clock restarts. A
kill rule firing kills the strategy. An engine change that alters decisions restarts the clock.

**What shadow proves.** Shadow verifies that the engine trades the strategy the way the backtest says. It does not
prove the strategy has an edge, which would take years of forward data. Evidence of edge comes from the backtest
statistics and, for v1, the one-shot exam on the sealed window.

### 3. v1: the shadow clock restarts

- The shadow run since 2026-10-04 is a warm-up. It keeps running for engine checks and none of its days count.
- v1's day 0 is the day every Ready rule holds. The open items are the backtest gate (issue #735, after issue #707
  and issue #723), issue #706, the serve kill and drift wiring (issue #796), and the day-0 record (issue #797).
- At day 0 the warm-up run is closed. Its ledger is archived read-only, and a fresh run starts with an empty ledger
  and its own run id. Only the Code Owner runs that step on the VPS.
- v1 keeps its 90 days, which pre-registration §7 fixes. At the end, `hermes certify` reports the shadow trade count.
  Under 30 round trips it flags the execution check as weak, and no pass or fail rule changes. The Train replay of
  2026-10-05 (317 trades in about 3.8 years across BTC, ETH and SOL) suggests about 20 trades in 90 days.
- Certification can't start until shadow is certified, so v1's one-shot exam moves from 2027-01-01 to day 0 plus 90
  days. For example, day 0 on 2026-10-15 gives 2027-01-13.
- None of this edits pre-registration v1 or changes a strategy, sizing, fee, metric or window. It follows the §7 and
  §10 order as signed, so it is not a §11 deviation.

### 4. The engine improves without contaminating experiments

Engine and strategy versions are separate. Engine fixes land through normal PRs. Before merge, CI replays every
strategy in shadow over the Train window and reports changed decisions (issue #798). After deploy, `hermes parity
check` replays the shadow days on the VPS, comparing decisions only, never prices, and no AI reads it. Zero changes
merges normally. Any change goes to the Code Owner and restarts the affected shadow clocks. Recalibrating the fill
model from real fills later is a new engine version, checked the same way.

### 5. Rules for every strategy after v1

These bind pre-registrations from v2 on. v1 stays bound by what it signed.

- **Backtest funnel before shadow.** All of:
  - every pre-registration §9 metric on Train + Validation;
  - DSR ≥ 0.95, with N = the implied independent trials across every logged trial on that dataset (ADR-012 D-11);
  - probability of backtest overfitting (CSCV) ≤ 0.05;
  - t-statistic above 3;
  - at least 100 trades across Train + Validation;
  - still profitable at 2× fees and slippage;
  - positive in most years and in both bull and bear stretches;
  - Validation return at least half of Train;
  - neighbouring parameter values also pass;
  - return correlation below 0.7 with strategies already running.

  The last six are conventions without a literature threshold. Each pre-registration states its exact numbers.
- **Validation budget.** Each idea family gets at most 5 logged Validation touches. Past that, Validation counts as
  in-sample for the family.
- **Shadow slots.** At most 5 strategies in shadow at once, each with its own virtual capital.
- **Shadow length.** Shadow ends at 90 days and 30 round trips, whichever comes later, with a 180-day cap. Hitting
  the cap first is inconclusive, which counts as a fail.
- **Champion and challengers.** Real money runs one champion.
  - Promotion to shadow comes from the backtest statistics.
  - Promotion to real money needs a paired forward comparison with the champion over the same days, for at least a
    year.
  - The challenger must be in the set of statistically best strategies (Romano–Wolf stepdown or Hansen SPA) and beat
    the champion by the margin its own pre-registration states.
- **Combining results.**
  - Shadow candidates are chosen with false discovery rate control (Benjamini–Hochberg).
  - Estimates are shrunk within idea family, then variant, then asset before ranking.
  - Forward results are compared only on the same days, as paired differences.
  - Ablations, removing one rule at a time, are logged as trials.
- **Inconclusive results** extend once by the amount the pre-registration states, and fail if still inconclusive.

### 6. The learning loop

Each finished experiment adds one row to a lessons register next to the lab notebook: the hypothesis, the outcome,
what it taught, and which ideas it rules out. The research loop reads the register and Train diagnostics only, and
never Validation, exam or shadow outcomes (ADR-012 D-06, D-11). Parameters never update themselves (ADR-005
Category D, ADR-012 D-03). The system improves between versions, one signed variant at a time.

## Why

- **The protocol only works if its order is kept.** Shadow before the backtest gate measures an unchecked strategy,
  so its days cannot count.
- **Ready and Done rules turn judgement into checks.** With N strategies, "looks ready" is how a lab promotes noise.
- **The statistics set the limits, not the calendar.** The forward record needed to prove an edge and the backtest
  budget both come from published results, so the rules follow them instead of a target date.

Alternatives considered:

- **Count the warm-up days.** Rejected by the Code Owner; the strategy had not passed its backtest.
- **Stop shadow until day 0.** Rejected; the warm-up still finds engine defects at no cost to the experiment.
- **Lengthen v1's shadow to prove edge.** Rejected; even a year gives a PSR of about 0.84, and §7 fixes 90 days.
- **A separate repository for the lab.** Rejected; strategies must share one engine, one trial log and one parity
  check.
- **Self-tuning parameters on live results.** Rejected; ADR-005 Category D, and nothing tuned that way can be
  certified.

## Consequences

### Positive

- One clear answer to "is this strategy ready, and is it done", checked in code where possible.
- v1's shadow, once restarted, measures the backtested strategy on a pinned engine.
- The lab can screen many ideas without drowning in false winners.

### Negative

- v1's exam moves to day 0 plus 90 days, about two weeks past 2027-01-01 if day 0 lands mid-October.
- Challengers need a year beside the champion before real money.
- More statistics code: CSCV, stepdown or SPA tests, and shrinkage (issue #361).

### Neutral

- Pre-registration v1, its windows and its thresholds are unchanged.
- Forward testing inside the OOS window stays allowed under the 2026-10-02 guardrail.

## What would change this decision

- A signed pre-registration that sets its own Ready or Done rules within these bounds; the stricter rule wins.
- Evidence that the funnel's convention numbers reject sound strategies or pass overfit ones. The numbers change by
  decision record, not a new ADR.

## Sources

- Bailey, López de Prado — [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- Bailey, López de Prado — [The Sharpe Ratio Efficient Frontier](https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf) (minimum track record length)
- Bailey, Borwein, López de Prado, Zhu — [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
- Bailey, Borwein, López de Prado, Zhu — [Pseudo-Mathematics and Financial Charlatanism](https://carmamaths.org/resources/jon/backtest.pdf) (minimum backtest length)
- Harvey, Liu, Zhu — [... and the Cross-Section of Expected Returns](https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF)
- Arnott, Harvey, Markowitz — [A Backtesting Protocol in the Era of Machine Learning](https://people.duke.edu/~charvey/Research/Published_Papers/SSRN-id3275654.pdf)
- Jensen, Kelly, Pedersen — [Is There a Replication Crisis in Finance?](https://www.nber.org/system/files/working_papers/w28432/w28432.pdf)

## Cross-references

- [Pre-registration v1](../experiments/swing-mvp1/pre-registration-v1.md) §4, §7, §8, §9, §10, §11
- [ADR-005](adr-005-ai-ml-support-boundary.md) — Category D stays rejected
- [ADR-011](adr-011-paper-trading-engine.md) — certification lifecycle in the engine
- [ADR-012](adr-012-continuous-ai-evaluation.md) — research loop, blindness, trial counting
- [ADR-014](adr-014-pre-registration-binds-real-capital.md) — real-capital gate
- [2026-10-02 forward testing during the OOS window](../decisions/2026-10-02-forward-testing-during-oos-window.md)
- [2026-10-03 calibration metrics and the shadow Sharpe kill](../decisions/2026-10-03-calibration-metrics-and-shadow-sharpe-kill.md)
- Issues: #735 backtest gate, #723 and #707 replay fixes, #706, #796 serve kill wiring, #797 day 0 and fresh run,
  #798 engine replay check, #361 challenger lab

## Pending

- Issue #796, issue #797 and issue #798, then the backtest gate (issue #735) and v1's day 0.
- Issue #361 picks up §5 and §6.
