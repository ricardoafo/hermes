# Paper-Trading Engine Research — SYNTHESIS AMENDMENT

**Date**: 2026-08-25
**Author**: Synthesis amendment agent
**Engram topic key**: `research/paper-trading-synthesis-amendment`
**Status**: DRAFT — awaiting operator review
**Supersedes / augments**: `docs/research/paper-trading/SYNTHESIS.md` (2026-08-24)
**Additive only**: does NOT edit the base SYNTHESIS in place. Operator merges when ready.

---

## 0. Purpose

The base SYNTHESIS (2026-08-24) was finalised after SA-1 through SA-8. Two material
inputs have landed since:

1. **SA-9 — Portfolio Shocks + Risk Overlay** (`sa9-portfolio-shocks.md`). Surfaces
   8 concrete risk-overlay rules that are CROSS-POSITION and therefore cannot live
   inside Spec 3's per-order OrderRouter without collapsing separation of concerns.
2. **SA-10 — Python Performance + Testing Philosophy** (`sa10-python-perf-testing.md`).
   Provides 17 property-based invariants, per-module cosmic-ray mutation targets,
   CI pyramid tier definitions, and a Parquet vs SQLite storage split for the
   MarketDataPort.

Separately, the operator committed to a scientific-experiment framing for the Swing
MVP1 rollout (`docs/experiments/swing-mvp1/pre-registration-v1.md`). That framing
surfaces the need for a Spec 9 — **Strategy Development Protocol** — that codifies
pre-registration, statistical-framework enforcement, data-split walls, kill-trigger
evaluation, and the experiment lab notebook AS CODE, not as documentation.

This amendment adds Spec 8 + Spec 9, updates Spec 4 and Spec 7, confirms OD-7,
explicitly rejects free-threaded Python 3.13+ for MVP1, updates the sequence
diagram, and revises the GH issues mapping. Every claim is cited (SA-N §M) or
(pre-registration-v1 §M).

**Amendment confidence**: HIGH on Specs 8, 9, and Spec 7 updates. MEDIUM on the
revised timeline (~14-16 weeks) because Spec 9 has no prior-art precedent in the
research corpus.

---

## 1. Table of Contents

1. Purpose
2. Table of Contents
3. New Spec 8 — Risk Overlay (SA-9)
4. New Spec 9 — Strategy Development Protocol (pre-registration + discipline as code)
5. Spec 7 Update — Test Pyramid Manifest (17 invariants + kill-rate targets + CI tiers)
6. Spec 4 Update — MarketDataPort storage split (SQLite events + Parquet L2 diffs)
7. Confirmation of OD-7 (storage backend for orderbook diffs)
8. Explicit REJECTION of free-threaded Python 3.13+ (PEP 703) for MVP1
9. Updated Spec Count, Dependency DAG, and Timeline
10. Updated GH Issues Mapping
11. Cross-References
12. Confidence per Section

---

## 2. New Spec 8 — Risk Overlay (from SA-9)

**Confidence**: HIGH on rule set and thresholds; MEDIUM on the exact re-enable
timing constants (they are conservative engineering defaults per SA-9 §11 and
should be calibrated against actual shadow-mode data via the Spec 6 harness).

### 2.1 Why this is a NEW spec, not part of Spec 3

Spec 3 (OrderRouter) is **per-order**: it receives a `StrategySignal`, sizes it,
runs risk checks, and dispatches or rejects. All of its inputs are attached to a
single candidate order.

SA-9's rules are **cross-position and portfolio-scope**:

- **RO-01 (correlation lock)** reads the currently-open position set and refuses
  a new entry based on 7-day rolling BTC-correlation of PAIRS THAT ARE ALREADY
  OPEN. This cannot be evaluated from a single `StrategySignal` in isolation.
  (SA-9 §2.5, §10 defense matrix)
- **RO-08 (multi-pair ATR cascade)** escalates from ENTRY_SUPPRESS (single-pair
  ATR spike, already in SA-7) to PORTFOLIO_HALT (2+ pairs simultaneously
  spiking). The trigger is a PORTFOLIO-LEVEL aggregation. (SA-9 §8.6)
- **RO-06 (depeg monitor)** watches USDT/USD + USDC/USD via Kraken public
  ticker and gates the WHOLE PORTFOLIO. It is not tied to any candidate order.
  (SA-9 §4.5)
- **RO-04 (spread-spike halt)** and **RO-05 (stale-book guard)** operate on
  the market-data layer and gate all new market orders across all pairs
  simultaneously. (SA-9 §3.5)
- **RO-07 (FOMC blackout)** is a calendar-driven portfolio-wide gate. (SA-9 §8.6)

Collapsing these into Spec 3 would (a) inflate the OrderRouter into a portfolio
coordinator, (b) mix per-order and cross-position concerns in a way that violates
the hexagonal separation the base synthesis mandates, and (c) make the Spec 3
test surface unmaintainable.

**Decision**: Spec 8 is a standalone spec that ships a `RiskOverlay` port +
`PortfolioRiskOverlay` concrete implementation. The OrderRouter (Spec 3)
consults the RiskOverlay via a `pre_entry_check(candidate: Order, portfolio:
Portfolio) -> OverlayDecision` call and honors PORTFOLIO_HALT / ENTRY_SUPPRESS
verdicts. RO-02 and RO-03 (max-slippage guards) STAY in Spec 2's fill model
because they are per-order fill-time checks, not cross-position rules.

### 2.2 Spec 8 — Risk Overlay: purpose, scope, API

**Purpose**: Ship the 8 SA-9 portfolio-shock defenses as a first-class hexagonal
component that gates entries across pairs, holds portfolio state independently
of Spec 3, and can raise from ENTRY_SUPPRESS to PORTFOLIO_HALT verdicts.

**Scope IN** (SA-9 §10 defense matrix):

| # | Rule | Threshold | Trigger action | SA-9 reference |
| --- | ------ | ----------- | ---------------- | ---------------- |
| RO-01 | Correlation lock | No 2 BTC-correlated positions simultaneously (7-day rolling correlation > 0.80 to BTC) | Block entry in position sizer (returns SUPPRESS to OrderRouter) | SA-9 §2.5, §10 |
| RO-02 | Max slippage — entry | Simulated slippage > 0.50% of position notional at mid | Cancel order; WARN alert. **Owned by Spec 2 (fill model)**, not Spec 8 | SA-9 §3.5, §10 |
| RO-03 | Max slippage — exit | Simulated slippage > 2.00% of position notional at mid | WARN (do NOT block; operator may need to accept worse fill to exit). **Owned by Spec 2** | SA-9 §3.5, §9.4 |
| RO-04 | Spread-spike halt | Current spread > **10× 1H rolling median** for the pair | Halt new market-order entries; CRITICAL alert; re-enable after spread normalizes for 5 consecutive minutes | SA-9 §3.5 |
| RO-05 | Stale book guard | Last book update > **2000 ms** ago | Mark STALE; block OrderbookWalkFillModel fills (paper); halt new entries (live); stop-losses still permitted | SA-9 §3.5 |
| RO-06 | Depeg monitor | USDT/USD or USDC/USD deviation from $1.00 > **1.5% = WARN**; > **3.0% = HALT** | WARN at 1.5% → log + operator notify. HALT at 3% → halt all new entries + set PORTFOLIO_HALT_DEPEG flag + trigger equity restatement | SA-9 §4.5 |
| RO-07 | FOMC calendar blackout | 30 min before / 60 min after each FOMC meeting (8 per year, hardcoded) | Suppress new entries; tighten trailing stop to 1.5× ATR (from 3.0×) during blackout | SA-9 §8.6 |
| RO-08 | Multi-pair ATR cascade | ATR spike (>1.5× 20-period ATR MA) on **2+ pairs simultaneously** | PORTFOLIO_HALT; SYSTEMIC_VOLATILITY alert; resume only after ATR normalizes on all pairs for 3 consecutive candles | SA-9 §8.6, §2.5 |

**Concrete numeric thresholds** (all from SA-9 — cited above):

- Spread-spike multiplier: **10×** the 1-hour rolling median (RO-04)
- Max entry slippage: **0.50%** (RO-02)
- Max exit slippage: **2.00%** (RO-03)
- Stale-book age: **2000 ms** (RO-05)
- Depeg WARN threshold: **1.5%** from $1.00 (RO-06)
- Depeg HALT threshold: **3.0%** from $1.00 (RO-06)
- Correlation-lock threshold: **0.80** BTC-dominance 7-day rolling correlation (RO-01; matches pre-registration §5.5)
- Depeg polling cadence: every **60 seconds** via Kraken public ticker (SA-9 §4.4)

**Scope OUT**:

- Kill-trigger evaluation for the experiment (Spec 9 owns this — the overlay
  fires immediate defenses; the kill trigger evaluator counts occurrences and
  can declare the strategy DEAD per pre-registration-v1 §10).
- Order-time fill quality gates RO-02 and RO-03 (Spec 2 owns these — they need
  the `OrderbookWalkFillModel` to compute simulated slippage).
- Multi-exchange routing for MVP2 (SA-9 §5.4 P2).
- Adaptive order sizing during vacuum (SA-9 §3.5 MVP2).

**API surface** (Python-flavoured pseudocode):

```python
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol


class OverlayVerdict(Enum):
    ALLOW = "allow"  # no overlay concerns
    SUPPRESS = "suppress"  # single-rule suppression (e.g., correlation lock)
    PORTFOLIO_HALT = "portfolio_halt"  # multiple new entries halted


@dataclass(frozen=True)
class OverlayDecision:
    verdict: OverlayVerdict
    triggered_rules: tuple[str, ...]  # e.g., ("RO-01",) or ("RO-06", "RO-08")
    rationale: str  # human-readable
    reenable_at: datetime | None  # e.g., re-check RO-04 after 5 min of normal spread


class RiskOverlayPort(Protocol):
    def pre_entry_check(
        self,
        candidate: Order,
        portfolio: Portfolio,
        market_snapshot: MarketSnapshot,  # includes current book, spread, ATR context
    ) -> OverlayDecision: ...

    def evaluate_periodic(self, portfolio: Portfolio) -> list[OverlayEvent]:
        """Called every 60s by scheduler.
        Runs RO-06 (depeg poll), RO-08 (multi-pair ATR aggregation), etc.
        Emits domain events for firing rules."""

    def is_portfolio_halted(self) -> tuple[bool, tuple[str, ...]]:
        """Fast check consulted by OrderRouter before every submit()."""


class PortfolioRiskOverlay(RiskOverlayPort):
    """Concrete MVP1 impl of the 8 SA-9 rules."""

    correlation_threshold: Decimal = Decimal("0.80")  # RO-01, SA-9 §2.5
    spread_spike_multiplier: Decimal = Decimal("10.0")  # RO-04, SA-9 §3.5
    stale_book_ms: int = 2000  # RO-05, SA-9 §3.5
    depeg_warn: Decimal = Decimal("0.015")  # RO-06, SA-9 §4.5
    depeg_halt: Decimal = Decimal("0.030")  # RO-06, SA-9 §4.5
    fomc_blackout_before_min: int = 30  # RO-07, SA-9 §8.6
    fomc_blackout_after_min: int = 60  # RO-07, SA-9 §8.6
    multi_pair_atr_threshold: int = 2  # RO-08, SA-9 §8.6
    reenable_normal_min: int = 5  # RO-04, SA-9 §3.5
```

**Dependencies**:

- Spec 1 (Portfolio, Order, StrategyId — SA-9 rules operate on domain aggregates)
- Spec 4 (MarketDataPort for book state, ATR context; Kraken ticker for depeg poll)
- Spec 3 (OrderRouter consults `pre_entry_check` and `is_portfolio_halted`)

**Tests**:

- Unit: each of the 8 rules with fixture-driven scenarios (correlated positions
  in open state, engineered book with 10× spread, artificially stale timestamps,
  depeg fixtures at 1.5% / 3.0% / 5%, FOMC calendar boundaries).
- Property (Hypothesis): correlation-lock symmetry (adding a BTC position blocks
  ETH and SOL entries, and vice versa); depeg WARN never triggers HALT alone;
  ATR cascade never triggers on single-pair spike.
- **Historical replay chaos** (per SA-9 §2.1 events): fixture replays of
  March 2020 COVID crash, June 2022 3AC, November 2022 FTX, March 2023 SVB,
  August 2024 yen unwind → asserts that the overlay would have fired
  correctly on real event data.
- Integration: OrderRouter + RiskOverlay + FillModel end-to-end where overlay
  suppresses an entry that would otherwise have passed all Spec 3 gates.
- Mutation: kill rate ≥ **85%** on `PortfolioRiskOverlay` (SA-10 §11.3 —
  overlay logic is SEV-1: financial defense).

**PR sizing**: **L** (~1000 LoC + ~1800 LoC tests + 5 historical replay
fixtures). Roughly the same scale as Spec 2 or Spec 4.

**Composite-scenario coverage** (SA-9 §9): the four composite scenarios (A:
panic+vacuum+correlation; B: depeg+accounting; C: geoblock+active positions;
D: macro+correlation+hack) each map to a specific rule interaction in the
overlay. All four are required chaos-test scenarios in Spec 7's manifest.

---

## 3. New Spec 9 — Strategy Development Protocol (code, not process)

**Confidence**: HIGH on the enforcement mechanism design (pre-registration
schema, split walls, kill evaluator); MEDIUM on the exact DSR/bootstrap/CV
implementation details, which require a separate spec-level design (López de
Prado 2014 formulas; block bootstrap block-length calibration).

### 3.1 Why this is a spec, not a process document

The pre-registration document (`docs/experiments/swing-mvp1/pre-registration-v1.md`)
defines what SCIENCE requires. Without CODE ENFORCEMENT, every principle in it
becomes a promise the operator can break under pressure. The exact failure modes
the pre-registration exists to prevent are:

- **P-hacking**: silently tweaking parameters after seeing results
  (pre-registration-v1 §11, §12)
- **OOS data leakage**: peeking at the certification window during
  development (pre-registration-v1 §4)
- **Multiple-testing inflation**: adding "just one more" secondary metric
  without penalty (pre-registration-v1 §8.4)
- **Kill-trigger dishonesty**: rationalizing why a fired kill trigger
  "doesn't really count" (pre-registration-v1 §10)

A process document cannot prevent any of these. Code that PHYSICALLY REFUSES
to load OOS data outside certification mode, that CRYPTOGRAPHICALLY BINDS
metrics to a pre-registration hash, and that AUTOMATICALLY declares the
strategy DEAD when a kill trigger fires — that is the only enforcement the
operator's future self cannot argue with.

The pre-registration doc explicitly assigns this role to the "experimentalist"
(the engine) and the "pre-registration integrity checker" (pre-registration-v1
§13). Spec 9 is where those two roles become code.

### 3.2 Spec 9 — Strategy Development Protocol: purpose, scope, API

**Purpose**: Codify pre-registration, statistical framework enforcement,
data-split walls, kill trigger evaluation, and the experiment lab notebook AS
CODE. This spec exists so the operator's future self cannot p-hack, cannot
peek at OOS data, cannot silently deviate, and cannot rationalize past a
fired kill trigger. It is the scientific-integrity enforcement layer that
gates whether a strategy is allowed to hold live capital.

**Scope IN**:

1. **Pre-registration schema + parser + hasher**
   - Read `pre-registration-v1.md` (Markdown front-matter + typed sections)
     into a `PreRegistration` value object.
   - Compute SHA-256 of the canonical pre-registration payload (all sections
     §1-§13 except §15 change log and §16 signatures).
   - Emit `PreRegistrationSigned` event when the operator signs; the event
     carries the hash + git commit SHA (pre-registration-v1 §16).
   - Any subsequent code path that runs backtest / shadow / OOS certification
     REQUIRES a `PreRegistration` reference; without it the harness refuses
     to run.

2. **Data-split wall (physical refusal to read OOS)**
   - `DataSplitEnforcer` reads the frozen windows from the pre-registration:
     Train = 2020-01-01 → 2024-06-30, Validation = 2024-07-01 → 2025-08-31,
     OOS Test = 2025-09-01 → 2026-12-31 (pre-registration-v1 §4).
   - Exposes `read_bars(pair, start, end, mode: DataMode)` where
     `DataMode ∈ {TRAIN, VALIDATION, OOS_CERTIFICATION}`.
   - If `mode != OOS_CERTIFICATION` and the request overlaps the OOS window,
     the enforcer **raises `OOSDataLeakageError`**. Not a warning, not a log —
     an exception that fails the pytest / CLI invocation loudly.
   - `OOS_CERTIFICATION` mode is a one-shot: it is activated only by the
     `hermes certify --pre-registration <hash>` CLI command, which itself
     refuses to run before 2027-01-01 (pre-registration-v1 §4 discipline gate)
     AND requires the pre-registration hash + operator confirmation.

3. **Statistical framework enforcement**
   - `StatisticalReport` value object with fields required by the
     pre-registration (pre-registration-v1 §8, §9):
     - Deflated Sharpe Ratio (DSR) per López de Prado 2014
     - Bootstrap 95% CI (10,000 resamples, block bootstrap length 30 days)
     - Walk-forward CV metrics (expanding window, 6-month retrain, 3-month test)
     - Purged combinatorial CV (5-fold, 5-day purge, 3-day embargo)
     - Bonferroni penalty accumulator (increments each time a secondary metric
       is added)
   - `H1AcceptanceGate`: consumes a `StatisticalReport` and returns
     ACCEPTED / NOT_ACCEPTED per pre-registration-v1 §1 decision rule
     (DSR > 0.7 AND bootstrap 95% CI lower bound > 0 AND all secondary metrics
     pass).
   - **CV modes strictly reject OOS window**: any CV split whose test fold
     overlaps `[2025-09-01, 2026-12-31]` raises `OOSDataLeakageError`.

4. **Kill trigger evaluator**
   - `KillTriggerEvaluator` runs continuously (as part of the periodic overlay
     evaluation from Spec 8) and independently in nightly CI.
   - Reads the FROZEN kill criteria from the pre-registration
     (pre-registration-v1 §10): backtest kills, shadow kills, live kills.
   - When any kill trigger fires:
     - Emit `KillTriggerFired` domain event with rule ID + evidence.
     - Set the strategy status to DEAD (irreversible for this pre-registration).
     - Halt all new entries via the RiskOverlay (Spec 8) PORTFOLIO_HALT.
     - Require a post-mortem within 7 days (Spec 9 tracks the deadline;
       fires a WARN if missed).
     - Any attempt to re-enable requires a NEW pre-registration (v2) —
       enforced by the pre-registration hash binding.
   - The evaluator does NOT rationalize. There is no "override" path in code.

5. **Experiment lab notebook (append-only, event-sourced)**
   - Extends SA-8's event log discipline to experiment-level events
     (pre-registration-v1 §12).
   - `LabNotebookEntry` events: `PreRegistrationSigned`, `DataAccessed`
     (with mode + window), `MetricComputed`, `SecondaryMetricAdded` (triggers
     Bonferroni increment), `DeviationProposed`, `DeviationAccepted`
     (invalidates pre-registration), `KillTriggerFired`, `CertificationRun`,
     `StatusChanged`.
   - Every entry: UTC ISO 8601 timestamp, phase (train/val/OOS/shadow/live),
     action, observed metrics, decision, rationale — stored as event log
     rows with `event_seq` + `payload_sha256` (SA-8 architecture #1, #3).
   - Human-readable projection: `hermes experiment log` CLI dumps a Markdown
     view of the append-only log for the pre-registration's `experiment_id`.

6. **Deviation detector + integrity checker**
   - `DeviationDetector` compares the current in-memory strategy config
     (from `StrategyConfig`, Spec 5) against the frozen pre-registration
     spec (§3, §5, §6, §9, §10). Any drift raises
     `PreRegistrationDeviationError`.
   - Runs at:
     - Startup of any backtest / shadow / live run.
     - Every certification attempt.
     - Every nightly CI run against the current git HEAD.
   - Implements the "pre-registration integrity checker" role
     (pre-registration-v1 §13). When the operator says "let me just tweak
     the ADX filter," the tests break loudly.

**Scope OUT**:

- The strategy code itself (Spec 5 owns the `SwingStrategyAdapter`).
- The overlay defenses (Spec 8 owns those; Spec 9 CONSUMES their event
  stream to count kill-trigger firings per pre-registration-v1 §10 shadow
  and live sections).
- Notifications (Spec 9 emits domain events; a notification adapter is a
  cross-cutting concern already covered by the base synthesis).

**API surface** (Python-flavoured pseudocode):

```python
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import Protocol


class DataMode(Enum):
    TRAIN = "train"
    VALIDATION = "validation"
    OOS_CERTIFICATION = "oos_certification"  # one-shot; requires certify CLI


class ExperimentStatus(Enum):
    UNTRUSTED = "untrusted"
    BACKTEST_CERTIFIED = "backtest_certified"
    SHADOW_CERTIFIED = "shadow_certified"
    OOS_CERTIFIED = "oos_certified"
    LIVE_APPROVED = "live_approved"
    CERTIFIED = "certified"
    DEAD = "dead"  # terminal — kill trigger fired


@dataclass(frozen=True)
class PreRegistration:
    experiment_id: str  # e.g., "swing-mvp1-v1"
    version: int
    hash_sha256: str  # canonical hash of §1-§13
    signed_at: datetime | None
    git_commit_sha: str | None
    asset_universe: tuple[str, ...]  # §3
    train_window: tuple[datetime, datetime]  # §4
    validation_window: tuple[datetime, datetime]
    oos_window: tuple[datetime, datetime]
    certification_earliest: datetime  # §4 discipline gate
    strategy_config_frozen: dict  # §5 — exact params required
    fee_model_frozen: dict  # §6
    kill_criteria: dict  # §10
    primary_metric_thresholds: dict  # §9 primary
    secondary_metric_thresholds: dict  # §9 secondary
    bonferroni_penalty_count: int  # incremented on secondary additions


class DataSplitEnforcer:
    def __init__(self, pre_registration: PreRegistration, clock: ClockPort): ...

    def read_bars(
        self,
        pair: str,
        start: datetime,
        end: datetime,
        mode: DataMode,
    ) -> Iterable[Bar]:
        """Raises OOSDataLeakageError if mode != OOS_CERTIFICATION and range
        overlaps the OOS window. Also enforces the certification_earliest
        date gate for OOS_CERTIFICATION mode."""


class OOSDataLeakageError(Exception):
    """Physical refusal to load OOS data outside certification mode."""


class PreRegistrationDeviationError(Exception):
    """Current config drifts from frozen pre-registration."""


class StatisticalReport:
    dsr: Decimal
    bootstrap_ci_lower: Decimal
    bootstrap_ci_upper: Decimal
    walk_forward_metrics: dict
    purged_cv_metrics: dict
    trade_count: int
    bonferroni_adjusted_alpha: Decimal


class H1AcceptanceGate:
    def evaluate(
        self,
        report: StatisticalReport,
        pre_registration: PreRegistration,
    ) -> tuple[bool, dict]:
        """Returns (H1_accepted, evidence_dict).
        H1 accepted iff report.dsr > pre_registration.primary_metric_thresholds['dsr']
        AND report.bootstrap_ci_lower > 0
        AND all secondary metrics pass."""


class KillTriggerEvaluator:
    def evaluate_shadow(
        self,
        overlay_events: list[OverlayEvent],
        drift_bps: list[Decimal],
        shadow_sharpe_60d: Decimal,
    ) -> list[KillTriggerFired]: ...

    def evaluate_live(
        self,
        drawdown_pct: Decimal,
        time_to_recovery_days: int,
        rolling_30_trade_sharpe: Decimal,
        overlay_events: list[OverlayEvent],
        shadow_vs_live_bps_2w: Decimal,
    ) -> list[KillTriggerFired]: ...


@dataclass(frozen=True)
class KillTriggerFired:
    rule_id: str  # e.g., "live-max-drawdown-15pct"
    evidence: dict  # observed values
    fired_at: datetime
    pre_registration_hash: str  # binds firing to specific experiment


class DeviationDetector:
    def compare(
        self,
        current_strategy_config: StrategyConfig,  # Spec 5
        current_fee_model: FeeModelConfig,  # Spec 2
        pre_registration: PreRegistration,
    ) -> list[Deviation]:
        """Returns empty list if no drift; non-empty raises
        PreRegistrationDeviationError at the enforcement site."""


class LabNotebook:
    """Append-only, event-sourced per SA-8 discipline."""

    def append(self, entry: LabNotebookEntry) -> None: ...
    def dump_markdown(self, experiment_id: str) -> str: ...
```

**Dependencies**:

- Spec 1 (domain event log — LabNotebook extends `event_seq` + `payload_sha256`
  discipline).
- Spec 4 (MarketDataPort — `DataSplitEnforcer` wraps the historical data
  adapter).
- Spec 6 (Calibration harness — `StatisticalReport` reuses metric primitives).
- Spec 8 (RiskOverlay — `KillTriggerEvaluator` consumes overlay events).
- Spec 5 (StrategyConfig — `DeviationDetector` compares live config against
  frozen pre-registration).

**Tests**:

- Unit: hasher determinism across two invocations; every window boundary
  case in the split enforcer (bar exactly on start / end / spanning boundary);
  every kill trigger fires exactly when its pre-registration condition holds;
  DeviationDetector detects every field in `strategy_config_frozen`.
- Property (Hypothesis): for arbitrary date ranges, `DataSplitEnforcer`
  raises iff `mode != OOS_CERTIFICATION` and range overlaps OOS; H1 gate is
  monotonic in DSR (higher DSR never causes rejection when lower DSR was
  accepted).
- **Critical CI test — OOS leakage attempt**: a pytest that intentionally
  attempts to read the OOS window in TRAIN mode must assert
  `OOSDataLeakageError` is raised. If this test ever passes without raising,
  the entire test run must fail.
- **Kill-trigger determinism**: fixture with a synthetic drawdown of exactly
  15.0% (boundary) triggers the live-max-drawdown kill; 14.99% does not.
- **Pre-registration hash stability**: SHA-256 of the same pre-registration
  file computed on two machines / two OS's must match. Guarded by a snapshot
  test with syrupy.
- **Deviation detection**: fixture where the operator changes `adx_threshold`
  from 25 to 30 in `StrategyConfig` — must raise
  `PreRegistrationDeviationError` at startup.
- Mutation: kill rate ≥ **90%** on `DataSplitEnforcer`,
  `KillTriggerEvaluator`, and pre-registration hash logic (SA-10 §11.3
  precedent: highest-value modules → highest mutation targets).

**PR sizing**: **L** (~900 LoC + ~1600 LoC tests + a set of pre-registration
fixture files for the tests). Slightly smaller than Spec 8 because the
statistical primitives (DSR, bootstrap, purged CV) are library-backed
(scipy, statsmodels, mlfinlab) rather than bespoke.

**Cross-references to pre-registration-v1**:

- §1 Hypothesis → `H1AcceptanceGate`
- §3 Asset universe → `PreRegistration.asset_universe`; `DeviationDetector`
- §4 Data windows + discipline gate → `DataSplitEnforcer` +
  `certification_earliest`
- §5 Strategy spec → `strategy_config_frozen` in `PreRegistration` +
  `DeviationDetector`
- §6 Fee model → `fee_model_frozen`
- §7 Certification lifecycle → `ExperimentStatus` state machine
- §8 Statistical framework → `StatisticalReport` fields
- §9 Metrics + thresholds → `H1AcceptanceGate` evaluation
- §10 Kill criteria → `KillTriggerEvaluator`
- §11 Deviation protocol → `PreRegistrationDeviationError` raising sites
- §12 Lab notebook → `LabNotebook` append-only event log
- §13 Roles → the entire spec is the "experimentalist" and "integrity
  checker" as code

---

## 4. Spec 7 Update — Test Pyramid Manifest (SA-10)

**Confidence**: HIGH on all additions (SA-10 §8 catalogues invariants
explicitly; SA-10 §11.3 provides per-module kill-rate table; SA-10 §13
provides the three-tier CI model).

### 4.1 Additions to Spec 7 scope

Spec 7 as defined in the base SYNTHESIS §4 already declares markers, an
import-linter contract, and a CI matrix skeleton. This amendment REPLACES /
EXTENDS the following sub-sections with concrete SA-10 content:

**Add: 17 property-based invariants across 6 categories** (SA-10 §8.1).
Every invariant is a mandatory `@given` test annotated with the SEV rubric.
These are OWNED by the spec that ships the code under test, but the manifest
LISTS them as gate criteria (identical pattern to the base SYNTHESIS §8 P0
mapping).

**Category 1 — Position conservation** (SA-10 §8.1):

- INV-01 (SEV-1): `sum(lot.remaining_qty for lot in open_lots) == portfolio.position_qty` per asset
- INV-02 (SEV-1): total consumed qty ≤ total opened qty per asset after any sequence
- INV-03 (SEV-1): `lot.remaining_qty ≤ lot.original_qty`; `remaining_qty ≥ 0`

**Category 2 — Balance monotonicity** (SA-10 §8.1):

- INV-04 (SEV-1): cash balance never goes negative
- INV-05 (SEV-1): `new_balance = old_balance - fill_cost - commission` — algebraically exact with Decimal
- INV-06 (SEV-2, F1 analogue): fee ≥ 0; commission rate × notional = exact fee

**Category 3 — Event sequence monotonicity** (SA-10 §8.1):

- INV-07 (SEV-1): `event_seq` strictly monotonic (no duplicates, no gaps except crash recovery)
- INV-08 (SEV-2): `event.occurred_at` non-decreasing within a session (ClockPort discipline)
- INV-09 (SEV-1): `payload_sha256` of same event serialized twice is identical

**Category 4 — Portfolio value identity** (SA-10 §8.1):

- INV-10 (SEV-1): `portfolio_value == cash_balance + sum(position_qty * mark_price)` after every fill
- INV-11 (SEV-1): after all lots consumed → `position_qty == 0` and no open lots for that asset

**Category 5 — FIFO lot ordering** (SA-10 §8.1):

- INV-12 (SEV-1, F2 analogue — AEAT FIFO tax compliance): `select_lots_fifo` returns lots in `opened_at ASC, lot_id ASC`
- INV-13 (SEV-1): oldest lot always consumed first

**Category 6 — Fill correctness** (SA-10 §8.1):

- INV-14 (SEV-1): `fill.filled_qty ≤ order.qty` always
- INV-15 (SEV-1): `fill.fill_price > 0` when `fill.filled_qty > 0`
- INV-16 (SEV-2): BUY market order → `fill.fill_price ≥ best_ask`
- INV-17 (SEV-2): SELL market order → `fill.fill_price ≤ best_bid`

**Add: cosmic-ray mutation kill-rate targets per module** (SA-10 §11.3):

| Module | Kill-rate target | Justification (SA-10 §11.3) |
| -------- | ------------------ | ----------------------------- |
| `domain/lots/selection.py` | ≥ **90%** | FIFO ordering is AEAT compliance critical (SEV-1: F2 analogue) |
| `domain/lots/lot_consumption.py` | ≥ **90%** | Consumption arithmetic — direct PnL impact |
| `domain/fees/fee_schedule.py` | ≥ **85%** | Fee calculation — financial output (matches SA-5 harness target) |
| future `domain/fill_model.py` (Spec 2) | ≥ **85%** | Core fill accuracy |
| `adapters/ledger/sqlite_ledger.py` | ≥ **80%** | Transaction boundary correctness |
| `domain/events/event_bus.py` | ≥ **75%** | Failure isolation logic |
| `infrastructure/config/` | ≥ **70%** | Validation rules |
| `adapters/exchanges/` | ≥ **60%** | I/O adapters — harder to kill (network mocking) |
| **Spec 8 `PortfolioRiskOverlay`** | ≥ **85%** | SEV-1 financial defense (this amendment) |
| **Spec 9 `DataSplitEnforcer` + `KillTriggerEvaluator`** | ≥ **90%** | Scientific integrity defense (this amendment) |

**Add: CI pyramid tier definitions** (SA-10 §13):

**PR tier (target < 60 s total)** — every PR:

- `pytest -x -q --timeout=60 -m "not chaos and not contract and not slow"`
- Unit tests, property tests (`@settings(max_examples=100)`), snapshot tests
  on changed modules, import-linter contracts, type checking, linting.
- Coverage report: ≥ 90% line, ≥ 85% branch (matches base synthesis Spec 7).
- `pytest -n auto` (pytest-xdist) parallelization for unit tests only —
  chaos and integration tests do NOT parallelize (SQLite single-writer).

**Nightly tier** — cron:

- `pytest -m chaos` — all chaos injection tests (WS drop, clock skew, disk
  full, duplicate messages, partial fill sequences per SA-10 §9).
- `pytest -m replay` — golden-replay suite with small (<200 events) fixtures.
- `pytest -m contract` — Binance/Kraken public API contract tests.
- Property tests with `HYPOTHESIS_MAX_EXAMPLES=10000` (env var override,
  SA-10 §13.2).
- `cosmic-ray run --filter "domain/lots domain/fees"` — targeted mutation on
  highest-value modules.
- Target: nightly run completes in < 30 minutes (SA-10 §13.3).

**Weekly tier** — cron on self-hosted runner:

- Full calibration harness against recorded shadow-mode data (Spec 6).
- Full mutation run: `cosmic-ray run src/hermes/domain/` — all domain
  modules; 2-4 hours (SA-10 §13.4).
- atheris fuzz campaigns on WS parser and config loader with 24h wall-clock
  budget (SA-10 §12.1).
- Large golden-replay fixtures from git-lfs or S3 (SA-10 §10.2).

**Add: Hypothesis DB caching** (SA-10 §13.5):

- Store `.hypothesis/` in GitHub Actions cache; share across CI runs so
  nightly runs benefit from failures found in previous PR runs.

**Add: chaos test manifest additions** from SA-10 §9 + SA-9 composite
scenarios:

- Clock skew injection (SA-10 §9.2 — `freezegun` with random skew)
- WebSocket message reorder/drop/duplicate (SA-10 §9.1 — `ChaosWebSocketProxy`)
- Disk-full during event log write (SA-10 §9.3 — monkey-patch `sqlite3.execute`)
- Duplicate exchange responses (SA-10 §9.5 — idempotency test)
- **Historical shock replays** (from SA-9 §2.1): March 2020, June 2022,
  November 2022, March 2023 SVB, August 2024 yen unwind — each fixture
  replays the price/spread/depeg sequence and asserts Spec 8 overlay fires
  correctly.
- **Composite scenarios A-D** (SA-9 §9): assert overlay + fill model +
  circuit breakers interact correctly under compound shocks.

**Add: golden-replay fixture policy** (SA-10 §10):

- JSONL format, deterministic per SA-8 sort_keys discipline.
- Small (<200 events) → `tests/fixtures/replays/`, committed to git,
  run in PR CI.
- Large (>200 events, e.g., full 30-day shadow replay) → git-lfs or S3,
  weekly CI only.
- syrupy for snapshot management; `--snapshot-update` required to change
  fill logic (SA-10 §10.4).

### 4.2 Impact on Spec 7 PR sizing

Base SYNTHESIS estimated Spec 7 as **S** (~500 LoC total). With the
17-invariant catalogue documentation, per-module mutation config, and the
three-tier CI matrix, updated estimate is **M** (~700 LoC config + ~500 LoC
meta-tests + expanded docs). Still small relative to Spec 2 / Spec 4 / Spec 8.

---

## 5. Spec 4 Update — MarketDataPort storage split (SA-10)

**Confidence**: HIGH — SA-10 §6.1 and §6.3 provide explicit design; SA-10
confirms "SQLite for events, Parquet for L2 diffs" is the correct split for
hermes scale.

### 5.1 Storage split summary

Base SYNTHESIS Spec 4 §Scope IN listed "orderbook diff storage schema +
append-only writer (SQLite or Parquet — decide in spec design)". Base
SYNTHESIS OD-7 recommended Option A (SQLite) for MVP1.

**SA-10 CONFIRMS a hybrid split, not one or the other** (SA-10 §6):

- **SQLite (WAL mode) for the DOMAIN EVENT LOG** (SA-10 §6.1). Low volume
  (~10s of events per day at swing frequency), single-writer discipline
  (SA-8 architecture #3), transactional consistency required. SA-10 §6.1
  confirms WAL + batch commit (100 events or 500ms) is correct.
- **Parquet (one file per day per pair, Zstd compression) for L2
  ORDERBOOK DIFFS** (SA-10 §6.3). High volume (~10 msgs/sec per pair per
  Binance @depth@100ms), analytical query pattern (time-range reads for
  backtest replay), no transactional requirement.

**SA-10 §6.3 quantifies the Parquet benefit**: "Column-oriented reads:
a time-range query on 10M rows reads only the `timestamp` and `levels`
columns, not all columns. **10-100× faster than SQLite row scans for
analytical queries.**" This is the critical fact that flips OD-7 from
"SQLite for everything" to "SQLite for events + Parquet for L2 diffs".

### 5.2 Updated Spec 4 scope

**Replace** the ambiguous "SQLite or Parquet — decide in spec design" line
with:

**Storage backend — CONFIRMED SPLIT** (SA-10 §6.1 + §6.3):

- **`hermes.db` (SQLite WAL)** — domain event log, single-writer, batch
  commit every 100 events or 500 ms.
  - PRAGMAs from SA-10 §6.1: `journal_mode=WAL`, `synchronous=NORMAL`,
    `wal_autocheckpoint=1000`, `cache_size=-65536`, `temp_store=MEMORY`,
    `mmap_size=268435456`.
- **`diffs/<PAIR>/<YYYY-MM-DD>.parquet` (pyarrow + Zstd)** — L2 orderbook
  diff corpus, one file per day per pair, columnar schema:

  ```text
  ts_ns:     int64
  bids_px:   list[decimal128(18, 8)]
  bids_qty:  list[decimal128(18, 8)]
  asks_px:   list[decimal128(18, 8)]
  asks_qty:  list[decimal128(18, 8)]
  ```

  (SA-10 §6.3 write pattern)
- Rationale (from SA-10 §6.3):
  - Column-oriented reads → **10-100× faster** than SQLite row scans for
    time-range queries on the diff corpus. This is the backtest-replay
    hot path.
  - Zstd compresses repetitive L2 data 5-10× better than SQLite page
    storage.
  - Schema evolution: Parquet allows adding nullable columns without
    migrating existing files.
  - Interoperability: pandas, polars, duckdb all read Parquet natively
    → calibration harness (Spec 6) can share the diff corpus with backtest
    replay.

### 5.3 Updated Spec 4 dependencies

Add:

- `pyarrow >= 15.0` (already Python 3.14 compatible per SA-10 §6.3).
- No `duckdb` or `polars` dependency at MVP1 — pyarrow's native reader is
  sufficient. Add if calibration harness queries become complex.

### 5.4 Updated Spec 4 tests

Add tests from SA-10:

- **Parquet round-trip determinism**: write a batch of diffs, read them
  back, assert bit-identical Arrow tables (per SA-10 §6.3 write/read pattern).
- **Time-range query correctness**: pyarrow `dataset.scanner(filter=...)`
  returns exactly the diffs in the requested `[start_ns, end_ns]` range
  (property test with Hypothesis-generated diff sequences).
- **Compression ratio smoke**: on a fixture of 10k realistic diffs, Zstd
  Parquet file size is at most 20% of the equivalent SQLite BLOB storage
  (validates SA-10's 5-10× compression claim in the actual pattern).
- **Backtest replay speed benchmark**: on a 24h diff corpus (single pair),
  time-range read via pyarrow completes in < 100 ms; SQLite equivalent
  documented for comparison (validates SA-10's 10-100× claim).

### 5.5 Updated Spec 4 PR sizing

Base estimate was **L** (~1000 LoC + ~1800 LoC tests). Adding the Parquet
writer + reader + tests bumps it to **L+** (~1200 LoC + ~2100 LoC tests).
Still one PR, still deliverable in 2 weeks.

---

## 6. Confirmation of OD-7

**Status**: OD-7 (base SYNTHESIS §6) CONFIRMED by SA-10 with **HIGH
confidence**, with a REFINEMENT.

Base SYNTHESIS OD-7 asked "SQLite vs Parquet vs TimescaleDB for orderbook
diffs" and recommended Option A (SQLite) for MVP1.

**SA-10 refines this to a HYBRID split**:

- SQLite (Option A) for **domain events** (base SYNTHESIS was correct here)
  — SA-10 §6.1 confirms WAL + batch commits sustain > 5000 rows/sec, well
  above hermes's rate. Switch to Postgres only at > 5000 rows/sec sustained
  or multi-machine deployment (SA-10 §6.2). NEITHER applies at 15 trades/month.
- Parquet (Option B) for **L2 diff corpus** — SA-10 §6.3 gives the
  10-100× read speed argument for analytical time-range queries.

**Amendment update**: In the base SYNTHESIS OD-7, ADD:
> **CONFIRMED by SA-10 (2026-08-24)**. SA-10 §6.1 confirms SQLite WAL for
> domain events at hermes scale; SA-10 §6.3 refines the recommendation with
> Parquet for L2 diffs specifically (one file per day per pair, Zstd), citing
> 10-100× read speed for time-range queries. **Spec 4 ships both** —
> `hermes.db` for events, `diffs/<pair>/<date>.parquet` for L2 corpus. Not
> either/or. The refinement is additive: base recommendation for events is
> unchanged.

---

## 7. Explicit rejection of free-threaded Python 3.13+ (PEP 703)

**Confidence**: HIGH — SA-10 §3.2 provides the reasoning explicitly with
citations to PEP 703's own performance section.

### 7.1 Decision

**REJECTED for MVP1 and MVP2**. Hermes will NOT adopt the `python3.14t`
free-threaded build.

### 7.2 Rationale (SA-10 §3.2)

1. **Performance cost**: removing the GIL introduces a **5-10% single-threaded
   overhead** due to per-object reference count atomics (PEP 703 §Performance,
   cited in SA-10 §3.2). Hermes IS a single-threaded workload — one asyncio
   event loop, single writer to SQLite. We would pay the cost with zero benefit.

2. **Ecosystem gaps**: SA-10 §3.2 notes "extension ecosystem compatibility is
   partial: numpy, pandas, and most scientific libraries have published
   free-threaded wheels as of mid-2026, but not all." Every dependency that
   ships a C extension is a potential blocker. cosmic-ray (mutation testing),
   older versions of scipy, some of pyarrow's C++ integration — any one of
   these could break silently.

3. **Not justified at 10 msg/sec**: SA-10 §3.1 does the arithmetic explicitly.
   "For hermes at 10 msg/sec: the GIL is not a bottleneck. A single 64-byte
   orderbook diff parsed and dispatched takes < 1 microsecond of GIL-holding
   time. At 10/sec, that is 10 microseconds/second GIL pressure — negligible."
   The workload is I/O-bound, not CPU-bound.

4. **The single-loop asyncio model does not need parallelism**: SA-10 §2.1
   confirms the architectural fit — asyncio for WS ingestion + HTTP REST, sync
   fill path called from the single writer task. This is the correct model.
   Free-threaded Python solves problems hermes does not have.

### 7.3 When to reassess

SA-10 §3.2: "Reassess when the calibration harness needs true CPU parallelism
across many pairs." Concretely:

- If MVP3+ scales to 100+ pairs simultaneously with per-pair calibration
  running in parallel.
- If the calibration Monte Carlo runs need to sweep parameter grids across
  many CPU cores in a single process (numpy vectorization + `asyncio.to_thread`
  should be tried FIRST per SA-10 §3.4 decision tree).

Neither applies to MVP1 or MVP2. This decision is bindable.

### 7.4 Related rejections (from SA-10, for completeness)

- **Cython for MVP1**: rejected (SA-10 §3.4). Pure Python is 1000× below
  its ceiling at hermes's load.
- **Rust extensions (PyO3 / maturin) for MVP1**: rejected (SA-10 §3.4).
  Only pursue if Cython maintenance becomes a team liability.
- **Sub-interpreters (PEP 684 / PEP 734)**: rejected (SA-10 §3.3). No shared
  object model; complexity exceeds benefit at hermes scale.
- **msgpack / protobuf for event log**: rejected (SA-10 §5.1). Deterministic
  SHA-256 (SA-8 requirement) needs canonicalization discipline that
  json+sort_keys already provides.

**The one alternative left OPEN**: `orjson` as a drop-in replacement for
stdlib `json` (SA-10 §4.3, §5.1). One-line swap that gives ~5× speedup.
Adopt only when profiling shows > 1% CPU in serialization. Not part of
MVP1 unless benchmarks say so.

---

## 8. Updated Spec Count, Dependency DAG, and Timeline

### 8.1 Spec count: 7 → **9**

The paper-trading engine now consists of NINE focused specs:

- Spec 1 — Domain Aggregates (unchanged)
- Spec 2 — FillModelPort + SimulatedExchangeAdapter (unchanged; adds RO-02 / RO-03 per-order slippage guards)
- Spec 3 — OrderRouter (unchanged; adds consultation of Spec 8 `RiskOverlayPort.pre_entry_check`)
- Spec 4 — MarketDataPort + ClockPort (**UPDATED §5** — SQLite events + Parquet L2 diffs split)
- Spec 5 — StrategyContract + SwingStrategyAdapter (unchanged)
- Spec 6 — Calibration Harness (unchanged; `StatisticalReport` primitives reused by Spec 9)
- Spec 7 — Test Pyramid Manifest (**UPDATED §4** — 17 invariants + per-module kill rates + 3-tier CI)
- **Spec 8 — Risk Overlay (NEW §2)** — SA-9's 8 rules RO-01..RO-08 (RO-02/RO-03 delegated to Spec 2)
- **Spec 9 — Strategy Development Protocol (NEW §3)** — pre-registration + split walls + kill evaluator + lab notebook as code

### 8.2 Updated dependency DAG

```text
Spec 7 (Test Pyramid) ─────────────────────────────────────┐
                                                            ├─ gate for all
Spec 1 (Domain Aggregates) ────────────────────────────────┤
                                                            │
      ┌──── Spec 4 (MarketData + Clock, Parquet L2)         │
      │                                                     │
Spec 2 (FillModelPort) <─────────────────┐                  │
      │                                  │                  │
      │              Spec 3 (OrderRouter) <── Spec 1 + Spec 2 + Spec 8
      │                                  │                  │
      │              Spec 5 (Strategy) <── Spec 1 + Spec 4  │
      │                                  │                  │
      │              Spec 8 (Risk Overlay) <── Spec 1 + Spec 4   [NEW, lands parallel with 4/5/6]
      │                                  │                  │
      │              Spec 6 (Calibration) <── Spec 2 + Spec 4
      │                                  │                  │
      │              Spec 9 (Strategy Dev Protocol) <── Spec 1 + Spec 4 + Spec 5 + Spec 6 + Spec 8
      │                                                     │
                                                            │
Foundation lands first; Specs 8 and 9 join the 4/5/6 parallel batch,
with Spec 9 downstream of the batch because it consumes Spec 8's events
and Spec 6's statistical primitives.
```

**Key change from base SYNTHESIS**: Specs 8 and 9 are NEW. Spec 8 lands
in parallel with the existing 4/5/6 batch (it only depends on Spec 1 + Spec 4).
Spec 9 lands AFTER Specs 5, 6, and 8 because it consumes:

- Spec 5's `StrategyConfig` for `DeviationDetector`
- Spec 6's statistical primitives for `StatisticalReport`
- Spec 8's overlay event stream for `KillTriggerEvaluator`

### 8.3 Updated timeline

Base SYNTHESIS §5 estimated **~12 weeks** to first CERTIFIED shadow-mode.
Adding Specs 8 (L) and 9 (L) extends the timeline. Spec 8 lands in parallel
with the 4/5/6 batch and adds ~1 week to their combined runway (they are
not fully parallelizable — the same engineer is bottleneck). Spec 9 must
land AFTER Specs 5 + 6 + 8, so it adds ~2 weeks on the critical path.

**Updated estimate: ~14-16 weeks** to first CERTIFIED shadow-mode + operational
pre-registration protocol. Breakdown:

- Week 1: Spec 7 (S/M) + Spec 1 (M) — parallel
- Week 2-3: Spec 4 (L+ with Parquet split)
- Week 4-5: Spec 2 (L)
- Week 6-7: Spec 3 (L)
- Week 8: Spec 5 (M)
- **Week 9-10: Spec 8 (L) — the risk overlay** *(new)*
- Week 11-12: Spec 6 (L)
- **Week 13-14: Spec 9 (L) — pre-registration + integrity checker** *(new)*
- Week 15-16: baseline measurement + first CERTIFIED shadow-mode run +
  pre-registration signature + Week-1-2 data ingest (per pre-registration-v1
  §14 companion documents)

Range **14-16 weeks** reflects:

- Lower bound (14): tight execution, no rework, Spec 8 partially parallelized
  with Spec 3 (Spec 8 only needs Spec 1 + Spec 4 as hard deps).
- Upper bound (16): expected slippage on Spec 8's historical-replay chaos
  fixtures (5 events × ~half day each) + Spec 9's pre-registration hash
  determinism cross-platform issues + integration of statistical library
  choices (mlfinlab vs custom for purged CV).

### 8.4 Sequence diagram (updated)

```text
   Week 1        Week 2-3     Week 4-5   Week 6-7   Week 8   Week 9-10  Week 11-12   Week 13-14   Week 15-16
   [Spec 7 S]    [Spec 4 L+]  [Spec 2 L] [Spec 3 L] [Spec 5] [Spec 8 L] [Spec 6 L]   [Spec 9 L]   [baseline
   [Spec 1 M]                                        [M]                              (needs 8+6)  +certify
   in parallel                                                                                     +pre-reg
                                                                                                   signature]
   └── gate ──────────────────── canonical fill path ─────────────────── overlay ──────────────── science
   established                    with orderbook L2 (Parquet) writes                              enforcement
                                  + risk gates in place
```

---

## 9. Updated GH Issues Mapping

Base SYNTHESIS §7 already maps existing issues. This amendment adds:

**New issues to file** (adds Spec 8 + Spec 9 to the existing 7):

- feat(specs): Spec 1 — Domain Aggregates (unchanged)
- feat(specs): Spec 2 — FillModelPort + SimulatedExchangeAdapter (unchanged; add scope note "RO-02/RO-03 max-slippage guards live here per amendment §2.2")
- feat(specs): Spec 3 — OrderRouter (updated: "consults RiskOverlayPort from Spec 8 per amendment §2")
- feat(specs): Spec 4 — MarketDataPort + ClockPort (updated body: "SQLite events + Parquet L2 diffs per amendment §5")
- feat(specs): Spec 5 — StrategyContract + SwingStrategyAdapter (unchanged)
- feat(specs): Spec 6 — Calibration harness (unchanged; add scope note "statistical primitives shared with Spec 9")
- feat(specs): Spec 7 — Test pyramid manifest (updated body: "17 invariants + per-module kill rates + 3-tier CI per amendment §4")
- **feat(specs): Spec 8 — Risk Overlay (SA-9 RO-01..RO-08)** — NEW parent issue for cross-position portfolio-shock defenses. Body references SA-9 §10 defense matrix + pre-registration-v1 §5.5 correlation lock + §10 kill criteria.
- **feat(specs): Spec 9 — Strategy Development Protocol (pre-registration + discipline as code)** — NEW parent issue for pre-registration schema, `DataSplitEnforcer`, `H1AcceptanceGate`, `KillTriggerEvaluator`, `DeviationDetector`, `LabNotebook`. Body references pre-registration-v1 §1-§13 and this amendment §3.

All 9 filed as sub-issues of #25 (parent epic).

**Updates to existing base-synthesis mappings**:

| Issue | Previous action | Amended action |
| ------- | ----------------- | ----------------- |
| #25 | Parent for 7 specs | Parent for **9 specs** — update body to list Spec 8 + Spec 9 |
| **New: Spec 8 parent issue** | — | File under #25; children: RO-01..RO-08 sub-issues if broken down; single issue if kept whole |
| **New: Spec 9 parent issue** | — | File under #25; children: (a) pre-registration parser + hasher, (b) DataSplitEnforcer, (c) H1AcceptanceGate + StatisticalReport, (d) KillTriggerEvaluator, (e) DeviationDetector, (f) LabNotebook — each ~200-300 LoC child issue |

**Cross-links to add**:

- Spec 2 issue body: "RO-02 (max entry slippage 0.5%) and RO-03 (max exit
  slippage 2.0%) from SA-9 §10 are implemented in the fill model here, not
  in Spec 8. See amendment §2.2."
- Spec 3 issue body: "Consults `RiskOverlayPort` from Spec 8 before every
  order dispatch. See amendment §2 for the API contract."
- Spec 4 issue body: "Storage split: SQLite for domain events (existing
  pattern), Parquet for L2 orderbook diffs (one file per day per pair,
  Zstd). See amendment §5 for schema + benchmark tests."
- Spec 6 issue body: "Statistical primitives (bootstrap CI, walk-forward
  CV) are shared with Spec 9. Design as a shared library module under
  `hermes.statistics.` importable by both."
- Spec 7 issue body: "17 property invariants + per-module cosmic-ray kill
  rates + PR/nightly/weekly CI tiers per amendment §4."
- Spec 8 issue body: "Implements SA-9 RO-01 through RO-08 (except RO-02
  and RO-03, which live in Spec 2). Consumed by Spec 3 (via
  `pre_entry_check`) and Spec 9 (via kill-trigger event stream)."
- Spec 9 issue body: "Enforces pre-registration protocol at
  `docs/experiments/swing-mvp1/pre-registration-v1.md`. `DataSplitEnforcer`
  physically refuses to read OOS window outside certification mode.
  `KillTriggerEvaluator` consumes Spec 8's overlay events."

---

## 10. Cross-References

Every claim in this amendment cites its source.

**Spec 8 (Risk Overlay)** — SA-9:

- Rule set RO-01..RO-08 → SA-9 §10 defense matrix + §10.5 MVP1 minimum viable table
- Threshold values → SA-9 §3.5 (spread-spike, stale book, max slippage);
  SA-9 §4.5 (depeg WARN/HALT); SA-9 §2.5 (correlation lock); SA-9 §8.6
  (FOMC blackout, multi-pair ATR cascade)
- Composite scenarios A-D → SA-9 §9
- Historical events for chaos fixtures → SA-9 §2.1 (correlation), §3.1
  (vacuum), §4.1 (depeg), §5.3 (exchange freezes), §8.2 (yen unwind)
- Correlation-lock threshold 0.80 → SA-9 §2.5 + pre-registration-v1 §5.5

**Spec 9 (Strategy Development Protocol)** — pre-registration-v1:

- Pre-registration schema fields → pre-registration-v1 §3, §4, §5, §6,
  §9, §10
- Data-split walls + certification gate → pre-registration-v1 §4
  ("certification cannot begin before 2027-01-01" + "code physically
  refuses to read this window outside certification mode")
- Statistical framework (DSR, bootstrap, walk-forward, purged CV) →
  pre-registration-v1 §8
- H1 decision rule → pre-registration-v1 §1
- Kill criteria (backtest / shadow / live) → pre-registration-v1 §10
- Deviation protocol → pre-registration-v1 §11
- Lab notebook append-only + event-sourced → pre-registration-v1 §12
  (extends SA-8 event log discipline)
- Role model (theorist / experimentalist / integrity checker) →
  pre-registration-v1 §13
- Ulcer index threshold 10 → pre-registration-v1 §9 (bakes into
  `secondary_metric_thresholds`)
- Time-stop 90 days → pre-registration-v1 §5.2

**Spec 7 update** — SA-10:

- 17 property invariants (INV-01..INV-17) → SA-10 §8.1
- Per-module cosmic-ray kill rates → SA-10 §11.3
- CI pyramid three-tier model → SA-10 §13
- Chaos test patterns (WS proxy, clock skew, disk full, duplicate response)
  → SA-10 §9
- Golden-replay fixture policy → SA-10 §10
- Hypothesis DB caching → SA-10 §13.5

**Spec 4 update** — SA-10:

- SQLite WAL for domain events + PRAGMAs → SA-10 §6.1
- Parquet for L2 diffs (one file per day per pair, Zstd) → SA-10 §6.3
- 10-100× read speed for time-range queries → SA-10 §6.3 (explicit quote)
- SQLite → Postgres threshold table → SA-10 §6.2

**OD-7 confirmation** — SA-10 §6.1 + §6.3 (see §6 of this amendment).

**Free-threaded Python 3.13+ rejection** — SA-10:

- 5-10% single-threaded overhead → SA-10 §3.2 (citing PEP 703 §Performance)
- Ecosystem gaps → SA-10 §3.2
- Not justified at 10 msg/sec → SA-10 §3.1 (GIL arithmetic)
- Single-loop asyncio adequacy → SA-10 §2.1

---

## 11. Confidence per Section

| Section | Confidence | Basis |
| --------- | ----------- | ------- |
| §2 Spec 8 rule set + thresholds | HIGH | SA-9 §10 defense matrix explicit; thresholds documented with historical evidence in SA-9 §2.1-§8 |
| §2 Spec 8 API design (`RiskOverlayPort`) | HIGH | Standard hexagonal port pattern; consistent with base synthesis Spec 3 style |
| §2 RO-01 correlation-lock threshold 0.80 | HIGH | Independent confirmation from SA-9 §2.5 and pre-registration-v1 §5.5 |
| §2 Historical-replay chaos fixtures (composite scenarios) | MEDIUM | SA-9 §9 provides scenario narratives; exact event replay data must be sourced (Tardis / CoinGecko / manual reconstruction from citations) |
| §3 Spec 9 pre-registration schema + hasher | HIGH | Pre-registration-v1 provides explicit field structure; SHA-256 canonicalization is well-understood |
| §3 Spec 9 `DataSplitEnforcer` design | HIGH | Physical refusal via typed enum is a clean, testable design |
| §3 Spec 9 `KillTriggerEvaluator` | HIGH | Kill criteria fully enumerated in pre-registration-v1 §10 |
| §3 Spec 9 statistical framework implementation | MEDIUM | Library choice (mlfinlab vs custom DSR + purged CV) needs a spec-level design decision; formulas from López de Prado 2014 are well-known but implementation choices non-trivial |
| §3 Spec 9 `DeviationDetector` scope | HIGH | Fields to compare are exhaustively listed in pre-registration-v1 §3/§5/§6/§9/§10 |
| §3 Spec 9 `LabNotebook` design | HIGH | Direct extension of SA-8 event log discipline |
| §4 Spec 7 17 invariants | HIGH | SA-10 §8.1 lists all 17 with rubric annotations |
| §4 Spec 7 per-module kill-rate targets | HIGH | SA-10 §11.3 provides table with justification for each module |
| §4 Spec 7 CI three-tier definitions | HIGH | SA-10 §13 provides tier structure and timing constraints |
| §4 Spec 7 chaos test additions (SA-9 historical replays) | MEDIUM | Depends on availability of replay fixtures — see composite-scenario note above |
| §5 Spec 4 SQLite + Parquet split | HIGH | SA-10 §6.1 and §6.3 provide explicit design with quantified benefit |
| §5 Spec 4 Parquet schema | HIGH | SA-10 §6.3 provides literal schema in code |
| §5 Spec 4 backtest replay speed benchmark | MEDIUM | 10-100× claim comes from pyarrow docs cited in SA-10 §6.3; actual hermes measurement needed |
| §6 OD-7 confirmation | HIGH | Trivial confirmation with additive refinement |
| §7 Free-threaded Python rejection | HIGH | SA-10 §3.1 + §3.2 provide arithmetic and citations |
| §8 Updated spec count + DAG | HIGH | Direct additive change; DAG dependencies verified against Spec 8 + Spec 9 API surfaces |
| §8 Updated timeline (14-16 weeks) | MEDIUM | Additive estimate; upper bound reflects known slippage risks (chaos fixture sourcing, hash determinism, statistical library choice) |
| §9 GH issues mapping | HIGH | Deterministic mapping given the new specs |

**Overall amendment confidence**: HIGH on the rule sets, thresholds, API
designs, and citations. MEDIUM on timeline estimate and on the pending
implementation choices in Spec 9's statistical framework.

---

## 12. What this amendment does NOT change

To avoid ambiguity for reviewers:

- **Base SYNTHESIS §1 (Executive Summary)** — unchanged. The core hexagonal
  architecture, same-code-path invariant, and bucket-aware domain model
  stand as written.
- **Base SYNTHESIS §2 (Tensions T-1 to T-9)** — unchanged. None of the
  tensions is affected by Specs 8 or 9.
- **Base SYNTHESIS §3 (ADR structure)** — should be UPDATED when the ADR
  is written to add Spec 8 and Spec 9 to §4 (Decisions Locked) and to §6
  (Follow-up Specs). This is a follow-on task, not part of this amendment.
- **Base SYNTHESIS Specs 1, 2, 3, 5, 6** — scope unchanged. Spec 2 and Spec 3
  gain small integration hooks (RO-02/RO-03 in Spec 2's fill model;
  `RiskOverlayPort` consultation in Spec 3), but neither PR sizing nor test
  scope changes materially.
- **Base SYNTHESIS §8 P0 mapping** — unchanged. The 6 SA-4 P0 defenses still
  map to their originally assigned specs. Spec 8's SA-9 rules are ORTHOGONAL
  to SA-4's operational defenses — they defend against DIFFERENT failure
  classes.
- **Base SYNTHESIS OD-1 through OD-6, OD-8** — unchanged.

Only OD-7 (§6 of this amendment) receives a "CONFIRMED by SA-10" annotation.

---

*End of SYNTHESIS AMENDMENT (2026-08-25).*
