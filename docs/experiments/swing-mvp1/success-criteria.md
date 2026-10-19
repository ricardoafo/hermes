---
name: Success Criteria — Swing MVP1
experiment_id: swing-mvp1-v1
status: DRAFT — pending operator review
date_drafted: 2026-08-25
date_revised: 2026-08-25
companion_to: pre-registration-v1.md
sources: SYNTHESIS.md (base, 7 specs), SYNTHESIS-AMENDMENT.md (adds Specs 8+9, SA-10 targets), pre-registration-v1.md, adr-011-paper-trading-engine.md (D-01..D-22)
---

# Success Criteria + Progress Measurement — Swing MVP1

> This document is NOT the scientific protocol. The pre-registration (`pre-registration-v1.md`) owns
> the falsifiable hypothesis, thresholds, and kill criteria. This document owns engineering
> milestones, operational KPIs, weekly progress reporting, and ongoing monitoring cadence.
> It is explicitly allowed to evolve during execution without invalidating the experiment.
>
> Data windows (Train / Val / OOS), position sizing cap (5%), asset universe, and kill criteria
> are FROZEN in pre-registration-v1.md and are reproduced here for reference only. Any apparent
> conflict between this document and the pre-registration resolves in favour of the pre-registration.

---

## 1. Engineering Milestones — 14–16 Week Roadmap

### 1.1 Spec Inventory (9 specs)

| Spec | Name | Size | Owner doc |
| ------ | ------ | ------ | ----------- |
| Spec 1 | Domain Aggregates (bucket-aware) | M | SYNTHESIS §4 |
| Spec 2 | FillModelPort + SimulatedExchangeAdapter | L | SYNTHESIS §4 |
| Spec 3 | OrderRouter (bucket-aware, strategy-agnostic) | L | SYNTHESIS §4 |
| Spec 4 | MarketDataPort + ClockPort (SQLite events + Parquet L2) | L+ | SYNTHESIS §4 + AMENDMENT §5 |
| Spec 5 | StrategyContract + SwingStrategyAdapter | M | SYNTHESIS §4 |
| Spec 6 | Calibration Harness | L | SYNTHESIS §4 |
| Spec 7 | Test Pyramid Manifest | M | SYNTHESIS §4 + AMENDMENT §4 |
| Spec 8 | Risk Overlay (SA-9 RO-01..RO-08) | L | AMENDMENT §2, ADR-011 D-21 |
| Spec 9 | Strategy Development Protocol | L | AMENDMENT §3, ADR-011 D-22 |

Specs 8 and 9 are new entries from SYNTHESIS-AMENDMENT.md. Spec 9 (pre-registration enforcement as
code) is the guardrail that makes Spec 5's strategy results scientifically defensible. It must land
before shadow-mode is treated as evidence for any certification decision.

### 1.2 Dependency DAG

```text
Week 1:   [Spec 7 — Test Pyramid Manifest]  ────────────────────────────────┐
          [Spec 1 — Domain Aggregates]      ────────────────────────────────┤
                                                                             │ gate cleared
Week 2-3: [Spec 4 — MarketDataPort + ClockPort + Parquet L2 split]  ───────┤
                                                                             │
Week 4-5: [Spec 2 — FillModelPort + SimulatedExchangeAdapter]  ────────────┤
                                                                             │
Week 6-7: [Spec 3 — OrderRouter]  ──────────────────────────────────────────┤
          [Spec 8 — Risk Overlay] (parallel — deps: Spec 1 + Spec 4)  ──────┤
                                                                             │
Week 8:   [Spec 5 — StrategyContract + SwingStrategyAdapter]  ─────────────┤
                                                                             │
Week 9-10: [Spec 8 — Risk Overlay] (if not finished in Week 7)  ────────────┤
                                                                             │
Week 11-12: [Spec 6 — Calibration Harness]  ───────────────────────────────┤
                                                                             │
Week 13-14: [Spec 9 — Strategy Development Protocol]  ─────────────────────┤
            (deps: Spec 1 + Spec 4 + Spec 5 + Spec 6 + Spec 8)             │
                                                                             │
Week 15-16: baseline measurement + first CERTIFIED shadow run               │
            + pre-registration signature + Spec 9 lab-notebook init  ───────┘
```

Notes:

- Spec 8 (Risk Overlay) can begin as soon as Spec 1 and Spec 4 are merged. The AMENDMENT places it
  in the same batch as Specs 3/5/6. The AMENDMENT's own sequence diagram (§8.4) shows Spec 8 in
  Week 9-10, but that is a serial schedule; partial parallelism with Spec 3 is feasible.
- Spec 9 has hard dependencies on Spec 5 (`StrategyConfig` for `DeviationDetector`), Spec 6
  (statistical primitives for `StatisticalReport`), and Spec 8 (overlay event stream for
  `KillTriggerEvaluator`). It cannot start until those three are merged.
- G8 (SHADOW_CERTIFIED) must be reached by approximately 2026-10-01 to leave a 3-month buffer
  before the 2027-01-01 OOS certification gate mandated by pre-registration §4.

### 1.3 Milestone Gates

| Gate | End of Week | Criterion to pass | Unlocks |
| ------ | ------------- | ------------------ | --------- |
| **G1: Foundation** | Week 1 | Spec 7 + Spec 1 merged; PR CI green; import-linter baseline committed | All subsequent specs |
| **G2: Data layer** | Week 3 | Spec 4 merged; Binance WS ingest live against public feed; Parquet round-trip determinism test green; nightly chaos green | Spec 2 (needs orderbook snapshots) |
| **G3: Fill kernel** | Week 5 | Spec 2 merged; `OrderbookWalkFillModel` golden replay passing; stateful property tests green; no-look-ahead and single-fill-path tests pass | Spec 3 and Spec 6 |
| **G4: Risk gates** | Week 7 | Spec 3 merged; all composition-root port-swap pattern verified; sizing never exceeds 5% notional (per pre-registration §5.4) | Spec 5 (signals need OrderRouter) |
| **G5: Protocol** | Week 8 or 9 | Spec 9 entry-criteria met: Spec 8 also merged OR both Spec 3 and Spec 8 merged before Spec 9 starts. Spec 9 itself ships: `DataSplitEnforcer` raises `OOSDataLeakageError` for any non-certification access to 2025-09-01 → 2026-12-31; `PreRegistrationSigned` event has a stable SHA-256 across platforms | Spec 5 can be trusted as a source of strategy results |
| **G6: Strategy** | Week 9 | Spec 5 merged; no OOS data access in non-certification mode; `DeviationDetector` in Spec 9 confirms config matches frozen pre-registration | Spec 6 (strategy feeds fill pairs) |
| **G7: Calibration engine** | Week 12 | Spec 6 merged; all P0 metrics implemented; calibration report schema valid; 7-day testnet baseline measurement job passes; shadow mode activatable | Shadow-mode first run |
| **G8: SHADOW_CERTIFIED** | ~2026-10-01 | 90 consecutive shadow days with M-02 p50 ≤ 30 bps; no P0 kill trigger fired; `KillTriggerEvaluator` (Spec 9) reports clean; lab notebook entry signed by operator | OOS test eligibility (2027-01-01 at earliest per pre-registration §4) |

G5 (Spec 9 merged) must appear in the gate sequence before G6 (Spec 5 relied upon as evidence),
because Spec 9 is the enforcement layer that makes Spec 5's outputs trustworthy. Any backtest or
shadow result claimed before Spec 9 is merged is advisory only, not admissible as pre-certification
evidence.

---

### 1.4 Per-Spec Definition of Done

#### Spec 7 — Test Pyramid Manifest (G1)

**What ships**: `pyproject.toml` pytest marker config, CI job matrix YAML, import-linter contracts,
mutation testing config. No engine runtime code.

**Entry criteria**: None. Spec 7 is a foundation; it lands first or in parallel with Spec 1.

| DoD criterion | Verification |
| --------------- | ------------- |
| pytest markers defined: `unit`, `property`, `chaos`, `replay`, `contract`, `slow`, `mutation` | `pytest --collect-only -q` shows all markers with no warnings |
| PR CI target (`pytest -m "not chaos and not contract and not slow"`) runs < 60s on empty suite | Measured in GitHub Actions |
| Nightly target: chaos + replay + contract suite defined | CI YAML merged and green on first run |
| Weekly target: full mutation + calibration + fuzz campaigns defined with cron trigger | CI YAML merged and green |
| `import-linter` contracts committed: (a) no `datetime.now()` in domain, (b) no `if shadow_mode/dry_run` in fill logic, (c) no cross-strategy imports | `lint-imports` passes on current codebase |
| Coverage gate: ≥ 90% line, ≥ 85% branch, exits non-zero below threshold | `pytest --cov` CI step wired |
| `cosmic-ray` configured in `pyproject.toml` with per-module kill-rate targets per SA-10 §11.3 | `cosmic-ray init` runs without error |
| 17 property invariants (INV-01..INV-17, SA-10 §8.1) listed as gate criteria in `docs/testing/manifest.md` | Doc committed; invariants cross-referenced to owning specs |
| Hypothesis DB caching configured in GitHub Actions cache key | Cache hit visible in Actions run |

**Test-tier requirements**: meta-tests only — assert markers registered, CI matrix contains
required jobs, import-linter contracts execute.
**PR size estimate**: M (~700 LoC config + ~500 LoC meta-tests + docs).

---

#### Spec 1 — Domain Aggregates (G1)

**What ships**: `Order`, `Fill`, `Position`, `Balance`, `Portfolio` + domain events with `event_seq`

- `payload_sha256`. Every aggregate carries `strategy_id: StrategyId` from day 1.

**Entry criteria**: None. Foundation; all other specs depend on it.

| DoD criterion | Verification |
| --------------- | ------------- |
| All value objects are `frozen=True, slots=True` dataclasses | `mypy --strict` passes |
| Every aggregate carries `strategy_id: StrategyId` | Grep confirms; import-linter enforces no leakage |
| Domain events have `event_seq` (strictly monotonic) + `payload_sha256` (deterministic SHA-256 of sort_keys=True JSON) | Property test: `@given` confirms determinism across two invocations (INV-07, INV-09) |
| `__event_schema_version__: ClassVar[int]` on every `DomainEvent` | CI type-check passes |
| No `datetime.now()` calls in domain — all via `ClockPort` | `rg 'datetime\.now' src/hermes/domain/'` returns zero results; import-linter contract enforces structurally |
| INV-01 through INV-17 (SA-10 §8.1) each have a dedicated `@given` test annotated with SEV rubric | All 17 tests present; nightly run at 10,000 examples green |
| FIFO lot selection (INV-12, INV-13): `select_lots_fifo` returns lots in `opened_at ASC, lot_id ASC` | Snapshot test + property test; SEV-1: F2 analogue (AEAT FIFO compliance) |

**Test-tier requirements**:

- Unit: constructor validation, immutability, `strategy_id` propagation Order→Fill→Position
- Property (Hypothesis, PR tier 100 examples / nightly 10,000): PnL invariants INV-10, INV-11;
  balance monotonicity INV-04, INV-05; position conservation INV-01..INV-03
- Contract: schema-versioned event serialisation deterministic across two invocations
- Mutation (weekly cosmic-ray): ≥ 90% on `domain/lots/selection.py` and
  `domain/lots/lot_consumption.py`; ≥ 85% on `domain/fees/fee_schedule.py`

**PR size estimate**: M (~600 LoC domain + ~800 LoC tests).

---

#### Spec 4 — MarketDataPort + ClockPort (G2)

**What ships**: `MarketDataPort` + `ClockPort` protocols; `SimulatedClock` + `WallClockClock`
concrete impls; `Consolidator` domain service; `LiveWebSocketMarketDataAdapter` (Binance);
**dual storage backend**: `hermes.db` (SQLite WAL) for domain events + `diffs/<PAIR>/<YYYY-MM-DD>.parquet`
(pyarrow, Zstd) for L2 orderbook diff corpus (SA-10 §6.1 + §6.3; AMENDMENT §5).

**Entry criteria**: Spec 1 merged (domain events).

| DoD criterion | Verification |
| --------------- | ------------- |
| `MarketDataPort` and `ClockPort` are `Protocol` (not ABC) | mypy structural subtype check |
| Binance WS ingest connects, receives diffs, publishes `MarketDataReceived` events | Nightly contract test against Binance public feed |
| Consolidator produces bars with strictly monotonic `snapshot_time` | Hypothesis property test with clock skew injection |
| SQLite (`hermes.db`) uses WAL mode + SA-10 §6.1 PRAGMAs: `journal_mode=WAL`, `synchronous=NORMAL`, `wal_autocheckpoint=1000`, `cache_size=-65536`, `temp_store=MEMORY`, `mmap_size=268435456` | PRAGMA assertions in test fixture |
| Parquet writer: one file per day per pair, Zstd compression, columnar schema `ts_ns / bids_px / bids_qty / asks_px / asks_qty` | Parquet round-trip determinism test: write → read → bit-identical Arrow table |
| Time-range query via `pyarrow dataset.scanner(filter=...)` returns exactly the diffs in `[start_ns, end_ns]` | Hypothesis property test with generated diff sequences |
| Compression smoke: 10k realistic diffs in Parquet ≤ 20% size of equivalent SQLite BLOB | Smoke test committed |
| Backtest replay speed benchmark: 24h single-pair diff corpus read in < 100ms | Benchmark test; SQLite baseline documented for comparison |
| Kraken CRC32 checksum validation on known book snapshot | Unit test with fixture (deferred to MVP2 production use, but code present) |
| No blocking calls in async context — all DB writes via batch commit pattern (100 events or 500ms, SA-10 §6.1) | Code review checklist + chaos test: 500ms delay spike does not stall fill path |
| Clock drift test: mock `WallClockClock` returning drift > 5s → adapter halts | Chaos test passes (`pytest -m chaos`) |
| WebSocket 1006 gap → REST reconciliation before resume (SA-4 P0-1) | Chaos test passes |
| Binance buffer+snapshot+updateId reconciliation bootstrap protocol | Integration test with replay of recorded bootstrap sequence |

**Test-tier requirements**:

- Unit: consolidator correctness, CRC32 validation, Parquet schema
- Property: bar high ≥ every trade price during window; bar volume = sum of trade volumes; split
  enforcer boundary correctness (all dates)
- Integration: replay recorded WS capture → same event sequence; consolidator → deterministic bars
- Chaos (nightly): WS drop/reorder/duplicate, clock drift, disk-full during Parquet write
- Mutation (weekly): ≥ 85% on adapter core paths; ≥ 80% on `adapters/ledger/sqlite_ledger.py`

**PR size estimate**: L+ (~1200 LoC + ~2100 LoC tests). Largest foundational spec after Spec 2.

---

#### Spec 2 — FillModelPort + SimulatedExchangeAdapter (G3)

**What ships**: `FillModelPort` protocol; `OrderbookWalkFillModel` (with OHLCV-synthetic fallback
when L2 absent); `SimulatedExchangeAdapter` (implements `ExchangePort`); fill-pair recording for
the calibration harness; RO-02 (max entry slippage 0.50%) and RO-03 (max exit slippage 2.00%)
per-order fill-time guards (SA-9 §3.5; owned here, not in Spec 8).

**Entry criteria**: Spec 1 merged. Spec 4 merged (for orderbook snapshots in shadow tests).

| DoD criterion | Verification |
| --------------- | ------------- |
| `FillModelPort.process_fill(ctx: MarketDataContext) -> FillResult` implemented | mypy protocol check |
| `OrderbookWalkFillModel` uses book walk with `Decimal` precision; VWAP fill ≥ best price (INV-16, INV-17) | Unit tests on known orderbook + order pairs |
| Maker/taker classification correct for crossing limits (SA-2 AP-3) | Unit test per case |
| Tick + lot quantization and MIN_NOTIONAL validation (SA-3 P0.1, P0.6) | Unit test: orders below min notional rejected with explicit `OrderReject` event |
| Entry slippage 5 bps (OHLCV mode) and exit slippage 15 bps per pre-registration §6.2 | Parameter tests |
| RO-02 guard: cancel order if simulated slippage > 0.50% of notional at mid | Unit test with engineered 0.5% slippage scenario |
| RO-03 guard: WARN (do not block) if exit slippage > 2.00% of notional at mid | Unit test; non-blocking confirmed |
| `SimulatedExchangeAdapter` routes to `FillModelPort` — zero real-exchange calls | Import-linter: no exchange HTTP calls from adapter |
| No-look-ahead fill test (SA-2 AP-1): market orders NEVER fill at current bar's close price | Test: fabricated bar sequence; fill timestamp always > bar close timestamp |
| Single-fill-path test (SA-2 AP-2): grep/import-linter confirms no `if.*shadow` or `if.*dry_run` in `fill.py` | Import-linter contract; `rg 'dry_run\|shadow' src/hermes/domain/fill` returns zero |
| Fill-pair (`SimulatedFill`, `RealFill`) recording emits events for calibration harness | Integration test with fake `ExchangePort` |
| Hypothesis stateful `FillStateMachine` covering INV-04, INV-05, INV-14, INV-15, INV-16, INV-17 | Property test present; nightly max_examples=10,000 |
| Golden-replay fixture: 1 small BTCUSDT replay (< 200 events) committed to `tests/fixtures/replays/` | syrupy snapshot test passes |

**Test-tier requirements**:

- Unit: orderbook walk on known inputs, maker/taker cases, quantization, MIN_NOTIONAL, RO-02/03
- Property (Hypothesis): fill VWAP ≥ best price; commission proportional to notional;
  `filled_qty ≤ order.qty`; determinism under seed (SA-8)
- Integration: end-to-end against canned orderbook → identical event log across two runs
- Replay (nightly): BTCUSDT golden replay via syrupy snapshot
- Mutation (weekly): ≥ 85% on `domain/fill_model.py`

**PR size estimate**: L (~1200 LoC + ~2000 LoC tests + calibration harness smoke tests).

---

#### Spec 3 — OrderRouter (G4)

**What ships**: `OrderRouter` domain service; `RiskEngine` with per-strategy `capital_budget`;
`SizingCalculator` (ATR-baseline capped by fractional Kelly or 5% hard cap per pre-registration
§5.4 — binding); drawdown circuit-breaker; orphan position invariant; rate-limit weight tracker;
**consults `RiskOverlayPort.pre_entry_check` and `is_portfolio_halted` from Spec 8** before every
order dispatch. RO-02 and RO-03 slippage guards live in Spec 2, not here.

**Entry criteria**: Spec 1 merged. Spec 2 merged (ExchangePort). Spec 8 merged or interface-stable
(OrderRouter consults `RiskOverlayPort` by interface; Spec 8 can deliver the implementation
concurrently if the interface is agreed).

| DoD criterion | Verification |
| --------------- | ------------- |
| `OrderRouter.submit(signal)` returns `Accepted \| RejectedByRisk \| RejectedByBudget \| Halted` | Unit test per outcome |
| Sizing: `risk_per_trade = 0.01 × equity` ($50); `qty = risk_per_trade / (entry_px - stop_px)`; hard cap 5% notional ($250) per pre-registration §5.4 | Property test: generated orders NEVER exceed 5% notional of experiment capital |
| `capital_budget` per strategy enforced — sum of open order notionals ≤ budget | Property test: budget invariant maintained across Hypothesis-generated order sequences |
| Drawdown circuit-breaker fires at -3% daily / -8% weekly / -20% peak (SA-7) | Unit tests at each threshold |
| Orphan position invariant: every `Position` in `Portfolio` traces to `OrderId` + `StrategyId` | Integration test; `OrphanPositionError` raised if not |
| Rate-limit weight tracker: blocks at 80% of Binance 1200 req/min ceiling; exponential backoff on 429 | Unit + chaos test: simulated 429 → backoff; at 80% ceiling → queued not dropped |
| Flash-crash guard: stop-market rejected if stop price is > N × ATR from current price | Unit test with SA-4 T-7 scenario |
| 3-gate shadow-mode invariant (`HERMES_ENV + HERMES_SHADOW + HERMES_ALLOW_MAINNET`) enforced at composition root — no `if shadow:` in write paths | Code review; import-linter contract; composition-root test |
| `pre_entry_check(candidate, portfolio, market_snapshot)` called before every dispatch; `PORTFOLIO_HALT` verdict halts all new entries | Integration test: Spec 8 mock returns `PORTFOLIO_HALT` → `Halted` result from `OrderRouter` |

**Test-tier requirements**:

- Unit: sizing composition edge cases (ATR=0, Kelly f*=None, equity below min notional); risk
  decision matrix; each circuit-breaker threshold
- Property: sizing never exceeds ATR-cap, Kelly-cap, equity-cap; capital_budget invariant
- Integration: signal → sizing → risk overlay → exchange happy-path + all rejection paths
- Chaos (nightly): flash-crash injection → circuit-breaker fires; WebSocket disconnect →
  REST reconciliation before resume
- Mutation (weekly): ≥ 75% on `OrderRouter`; ≥ 85% on `RiskEngine` sizing paths

**PR size estimate**: L (~800 LoC + ~1500 LoC tests + chaos scenarios).

---

#### Spec 8 — Risk Overlay (parallel with Spec 3 / G4 batch)

**What ships**: `RiskOverlayPort` protocol; `PortfolioRiskOverlay` concrete implementation of SA-9's
8 portfolio-scope shock defenses (RO-01..RO-08, except RO-02 and RO-03 which live in Spec 2).
This spec is a standalone hexagonal component — it holds portfolio-level state, fires `OverlayEvent`
domain events, and exposes `pre_entry_check` + `is_portfolio_halted` + `evaluate_periodic`.
The `KillTriggerEvaluator` in Spec 9 consumes these events to count rule firings against the
pre-registration §10 kill criteria.

**Entry criteria**: Spec 1 merged (domain aggregates); Spec 4 merged (MarketDataPort for book state
and ATR context; Kraken public ticker for depeg poll).

| DoD criterion | Verification |
| --------------- | ------------- |
| RO-01 (correlation lock): blocks new entry when 7-day rolling BTC-correlation of any open pair > 0.80 (pre-registration §5.5) | Unit test with synthetic correlation series; Hypothesis: adding a BTC position blocks ETH and SOL symmetrically |
| RO-04 (spread-spike halt): blocks new market-order entries when spread > 10× 1H rolling median for the pair | Unit test with injected spread series; re-enable after 5 consecutive minutes of normal spread |
| RO-05 (stale-book guard): marks book STALE when last update > 2000ms; blocks fills; stop-losses still permitted | Unit test with injected stale timestamp; stop-loss confirmation test |
| RO-06 (depeg monitor): WARN at USDT/USD or USDC/USD deviation > 1.5%; HALT at > 3.0%; poll cadence 60s | Unit test with injected price fixtures at 1.5% / 3.0% / 5.0%; WARN never escalates to HALT alone; Kraken ticker polling confirmed |
| RO-07 (FOMC blackout): suppresses new entries 30 min before / 60 min after each FOMC meeting; tightens trailing stop to 1.5× ATR during blackout | Unit test: boundary checks at ±1 minute from blackout edge; hardcoded 2026 FOMC calendar tested |
| RO-08 (multi-pair ATR cascade): fires PORTFOLIO_HALT when ATR spike (> 1.5× 20-period ATR MA) on 2+ pairs simultaneously; resumes after ATR normalizes on all pairs for 3 consecutive candles | Unit test: 1 pair spiking → no halt; 2 pairs spiking → PORTFOLIO_HALT |
| `evaluate_periodic(portfolio)` called every 60s by scheduler; emits `OverlayEvent` domain events with `event_seq` + `payload_sha256` | Integration test: 60s tick → events emitted; events stored in `hermes.db` event log |
| Historical replay chaos: March 2020, June 2022 (3AC), November 2022 (FTX), March 2023 (SVB), August 2024 (yen unwind) — overlay fires correctly on real event data | 5 chaos replay fixtures committed; `pytest -m chaos` green |
| Composite scenarios A-D (SA-9 §9): panic+vacuum+correlation; depeg+accounting; geoblock+active; macro+correlation+hack | 4 integration chaos tests; all pass |
| 30-day rolling rule-fire counter per rule — feeds Spec 9 `KillTriggerEvaluator` (pre-registration §10 shadow kill criterion: ≥ 3 fires of any one RO rule in 30 days) | Counter persisted in `hermes.db`; unit test: 3 fires → kill criterion met |

**Test-tier requirements**:

- Unit: each of the 8 rules with fixture-driven scenarios at threshold boundaries
- Property (Hypothesis): correlation-lock symmetry; depeg WARN never triggers HALT alone;
  ATR cascade never triggers on single-pair spike
- Chaos (nightly): 5 historical shock replays + 4 composite scenarios
- Integration: OrderRouter + RiskOverlay + FillModel end-to-end where overlay suppresses
  an entry that otherwise passes all Spec 3 gates
- Mutation (weekly): ≥ 85% on `PortfolioRiskOverlay` (SEV-1 financial defense per SA-10 §11.3)

**PR size estimate**: L (~1000 LoC + ~1800 LoC tests + 5 historical replay fixtures).

---

#### Spec 9 — Strategy Development Protocol (G5)

**What ships**: `PreRegistration` value object + parser + SHA-256 hasher; `DataSplitEnforcer`
(physical refusal to read OOS data); `StatisticalReport` value object; `H1AcceptanceGate`;
`KillTriggerEvaluator` (consumes Spec 8 overlay events + Spec 6 calibration results);
`DeviationDetector`; `LabNotebook` (append-only, event-sourced); `hermes certify` CLI gate
(refuses before 2027-01-01 per pre-registration §4); `hermes experiment log` CLI.

This spec is the code enforcement layer that the pre-registration describes but cannot enforce
by itself. Without Spec 9, the pre-registration is a promise; with Spec 9, it is a hard contract
that the operator's future self cannot silently violate.

**Entry criteria**: Spec 1, Spec 4, Spec 5, Spec 6, and Spec 8 all merged. Spec 9 consumes all
of them. Do not merge Spec 9 before its four upstream specs are complete.

| DoD criterion | Verification |
| --------------- | ------------- |
| `PreRegistration` SHA-256 hasher is deterministic across two machines / two OS platforms | Snapshot test with syrupy; same hash on Linux + Windows CI |
| `DataSplitEnforcer.read_bars(pair, start, end, mode)` raises `OOSDataLeakageError` for any `mode != OOS_CERTIFICATION` request overlapping 2025-09-01 → 2026-12-31 | Critical CI test: intentional OOS read attempt must assert `OOSDataLeakageError`; if test passes without raising, entire run fails |
| `OOS_CERTIFICATION` mode: `hermes certify --pre-registration <hash>` CLI refuses before 2027-01-01 using `ClockPort` (not `datetime.now()`) | Unit test: `SimulatedClock` set to 2026-12-31 → refused; set to 2027-01-01 → permitted |
| Train window enforced: 2020-09-01 → 2024-06-30 (per pre-registration §4 — SOL uniform start date) | Boundary property test: any request with start < 2020-09-01 in TRAIN mode → rejected |
| `H1AcceptanceGate.evaluate(report, pre_registration)` accepts iff DSR > 0.7 AND bootstrap 95% CI lower bound > 0 AND all secondary metrics pass (pre-registration §1) | Unit test: DSR = 0.71, CI lower = 0.01 → accepted; DSR = 0.69 → not accepted; CI lower = −0.01 → not accepted |
| `KillTriggerEvaluator` fires backtest kills (DSR on Train+Val < 0.7), shadow kills (M-02 > 30 bps p50 sustained 7 days; RO rule ≥ 3 fires / 30 days; shadow Sharpe < 0.3 for 60 days), and live kills (drawdown ≥ 15%; time-to-recovery > 90d from ≥ 8%; rolling 30-trade Sharpe < 0.3; any SA-9 P0 violation; live fills > 50 bps from shadow for 2 consecutive weeks) per pre-registration §10 | Unit test per kill criterion at exact boundary; determinism test: 14.99% drawdown → no kill; 15.00% → kill |
| `KillTriggerFired` event: strategy status set to DEAD (irreversible); `LabNotebook` entry appended; `RiskOverlay` (Spec 8) transitions to `PORTFOLIO_HALT`; no override path in code | Integration test: kill trigger → DEAD state; no code path leads out of DEAD without new pre-registration hash |
| `DeviationDetector` raises `PreRegistrationDeviationError` on startup if `StrategyConfig` drifts from frozen pre-registration §5 | Unit test: change `adx_threshold` from 25 to 30 → error raised; unchanged config → no error |
| `LabNotebook` is append-only: existing entries cannot be modified; new entries require correct `event_seq` + `payload_sha256` | Integration test: attempt to modify existing entry → exception; sequential appends monotone |
| `hermes experiment log` CLI renders a human-readable Markdown view of the lab notebook for a given `experiment_id` | Integration test: known fixture log → known Markdown output via snapshot |

**Test-tier requirements**:

- Unit: hasher determinism; every window boundary case; each kill trigger at exact boundary;
  `DeviationDetector` on every field in `strategy_config_frozen`
- Property (Hypothesis): `DataSplitEnforcer` raises iff `mode != OOS_CERTIFICATION` and request
  overlaps OOS; H1 gate monotonic in DSR; `LabNotebook` event sequences always monotonic
- Critical CI tests (run on every PR, fail-fast): OOS leakage attempt; kill-trigger determinism
  at boundary; pre-registration hash stability across platforms
- Mutation (weekly): ≥ 90% on `DataSplitEnforcer`, `KillTriggerEvaluator`, and
  pre-registration hash logic (highest mutation target in the project per SA-10 §11.3 precedent)

**PR size estimate**: L (~900 LoC + ~1600 LoC tests + pre-registration fixture files).

---

#### Spec 5 — StrategyContract + SwingStrategyAdapter (G6)

**What ships**: `StrategyPort` protocol; `StrategyConfig`; `StrategyStatus` state machine;
`SwingStrategyAdapter` implementing Donchian(20) 4H + Chandelier(22, 3.0) + ADX(14) ≥ 25 +
EMA(200) daily + OBV + ATR-spike filters (SA-7, pre-registration §5.1/§5.2).

**Entry criteria**: Spec 1 merged (domain). Spec 4 merged (bars from `MarketDataPort`).
Spec 9 merged — `DataSplitEnforcer` must be in place before any strategy result is treated as
evidence. (See G5 note above.)

| DoD criterion | Verification |
| --------------- | ------------- |
| `SwingStrategyAdapter.on_bar(bar, portfolio) -> list[StrategySignal]` is pure: no I/O | mypy; no I/O in adapter; import-linter |
| Entry logic: Donchian(20) close > highest_high(20) on 4H AND EMA(200) filter on 1D AND ADX(14) ≥ 25 on 4H — all three TRUE simultaneously on bar close | Backtested against known historical signals on Train window only |
| Exit logic: Chandelier trailing stop (highest_high(22) − 3×ATR(22)) AND time-stop 90 days AND regime break (close < EMA(200) daily) | Property test with synthetic bar sequences |
| On-exchange stop-market posted immediately after entry fill; trailing stop moved daily | Integration test with `SimulatedExchangeAdapter` |
| System 1 fail-safe: skip entry after prior winning breakout on the same pair (SA-7) | Unit test with two consecutive breakout scenarios |
| `f_star_estimate` field on `StrategySignal`: `None` for MVP1 (Kelly deferred per pre-registration §5.4 + SYNTHESIS T-1; activates after ≥ 30 OOS trades) | Unit test: `f_star_estimate = None` → `SizingCalculator` uses 5% hard cap |
| DST / UTC discipline: strategy uses `ClockPort` only; no local time | Unit test with `SimulatedClock` at DST boundary; no local-time calls |
| Lookahead guard: `signal_timestamp > bar_close_timestamp` enforced (SA-5 M-13) | Unit test: fabricated lookahead scenario raises `LookaheadViolation` |
| Backtested results on Train window stored in `LabNotebook` (Spec 9) with trial count for DSR denominator | Integration test: backtest run → `MetricComputed` entry in `LabNotebook` |

**Test-tier requirements**:

- Unit: indicator correctness (Donchian, ATR, ADX, Chandelier cross-checked against TA-Lib
  reference values); regime filter logic; System 1 fail-safe; DST boundary
- Property: Chandelier only moves up (monotone); no signal when ADX < 25; entry only when
  close > EMA(200)
- Integration: replay canned bar sequence → deterministic signal sequence; Spec 9
  `DeviationDetector` confirms config matches pre-registration at startup
- Mutation (weekly): ≥ 85% on `SwingStrategyAdapter` signal logic

**PR size estimate**: M (~700 LoC + ~1200 LoC tests + backtest fixtures).

---

#### Spec 6 — Calibration Harness (G7)

**What ships**: `CalibrationService` domain service (pure, no I/O); metric modules for all P0
metrics (M-02, M-04, M-09, M-10, M-13, M-15); `CalibrationReport` value object with JSON schema
(SA-5 §9); `CERTIFIED / UNTRUSTED` state machine; sim↔real fill-pair matcher (5s window);
7-day testnet baseline measurement job; `hermes replay` CLI; `RecordedExchangePort` +
`ExternalCallRecorded`. Statistical primitives (`bootstrap CI`, `walk-forward CV`, `purged CV`)
are designed as `hermes.statistics.*` library importable by Spec 9's `StatisticalReport`.

**Entry criteria**: Spec 1, Spec 2, Spec 4 all merged.

| DoD criterion | Verification |
| --------------- | ------------- |
| M-02 (fill-price divergence p50/p95/p99 bps) computed with KS + Wasserstein; testnet baseline subtracted (SYNTHESIS T-5) | Unit test with known bps series; property test: percentiles monotone |
| M-04 (bias direction) threshold: 0.35–0.65 OK, outside BLOCK | Unit test with biased/unbiased synthetic distributions |
| M-09 (PnL correlation Pearson r) with bootstrap 95% CI (10,000 resamples) | Unit test: known correlation series matches expected r |
| M-10 (Sharpe ratio relative divergence ≤ 20% OK, > 40% BLOCK) | Unit test with synthetic Sharpe pairs |
| M-13 (lookahead integrity) fires on first violation → immediate UNTRUSTED | Unit test: one lookahead event → `ShadowModeStatus.UNTRUSTED` |
| M-15 (fee model accuracy median ≤ 5%) | Unit test with known fee mismatch |
| `UNTRUSTED → CERTIFIED` requires human approval (`hermes shadow-mode certify --confirm`); auto-restoration blocked | Integration test: auto-restore path does not exist |
| 7-day sim-off baseline measurement runs before M-02 thresholds enforced (SYNTHESIS OD-5) | Hardcoded in harness; asserted in integration test |
| Regime conditioning (low/normal/high vol by ATR bucket) applied to M-02/M-04 | Unit test per bucket |
| Calibration JSON report matches SA-5 §9.1 schema | Schema validation test |
| `ShadowModeGate.check_deploy_allowed()` exits non-zero in CI when status is UNTRUSTED | Integration test: CI step fails with UNTRUSTED status |
| `hermes.statistics.*` primitives (bootstrap CI, walk-forward CV, purged CV) importable by Spec 9 | Import test + interface contract |

**Test-tier requirements**:

- Unit: KS test wrapper, Wasserstein, Pearson r bootstrap CI, lookahead detector,
  bias binomial test (SA-5 §11.1)
- Property: percentile monotonicity; bias ratio symmetry; threshold boundary correctness
- Integration: known-biased sim → BLOCK after 1,000 fills; known-matching → CERTIFIED after 200
- Replay: regression golden files (SA-5 §11.5) with pre-computed expected reports
- Mutation (weekly): ≥ 85% on `calibration_service.py`; critical mutations: `>` → `>=` in BLOCK
  thresholds; `BLOCK` → `WARN`

**PR size estimate**: L (~900 LoC + ~1500 LoC tests + baseline measurement job + replay CLI).

---

## 2. Coverage and Mutation Kill-Rate Targets

### 2.1 Line/Branch Coverage by Module

Per SA-10 §13.1. Enforced by `pytest --cov` in PR CI tier; failure blocks merge.

| Module path | Line coverage | Branch coverage | Tier enforced |
| ------------- | -------------- | ----------------- | --------------- |
| `domain/lots/selection.py` | ≥ 95% | ≥ 90% | PR |
| `domain/lots/lot_consumption.py` | ≥ 95% | ≥ 90% | PR |
| `domain/fees/fee_schedule.py` | ≥ 95% | ≥ 90% | PR |
| `domain/fill_model.py` (Spec 2) | ≥ 95% | ≥ 90% | PR |
| `domain/risk_overlay.py` (Spec 8) | ≥ 95% | ≥ 90% | PR |
| `domain/strategy_dev_protocol.py` (Spec 9) | ≥ 95% | ≥ 90% | PR |
| `domain/` (all other) | ≥ 90% | ≥ 85% | PR |
| `adapters/ledger/sqlite_ledger.py` | ≥ 85% | ≥ 80% | PR |
| `adapters/exchanges/` | ≥ 75% | ≥ 70% | PR |
| `infrastructure/` | ≥ 70% | ≥ 65% | PR |
| Overall project minimum | ≥ 90% | ≥ 85% | PR |

### 2.2 Mutation Kill-Rate Targets by Module

Per SA-10 §11.3. Measured by `cosmic-ray` on weekly CI runs. Target is per-module; a run
below target for any SEV-1 module two consecutive weeks is a milestone blocker.

| Module path | Kill-rate target | Rationale (SA-10 §11.3) |
| ------------- | ----------------- | -------------------------- |
| `domain/lots/selection.py` | ≥ 90% | FIFO ordering = AEAT compliance (SEV-1: F2 analogue) |
| `domain/lots/lot_consumption.py` | ≥ 90% | Consumption arithmetic — direct PnL impact |
| `domain/fill_model.py` (Spec 2, future) | ≥ 85% | Core fill accuracy |
| `domain/fees/fee_schedule.py` | ≥ 85% | Fee calculation — financial output |
| `calibration_service.py` (Spec 6, future) | ≥ 85% | Calibration gate accuracy |
| `domain/risk_overlay.py` (Spec 8, future) | ≥ 85% | SEV-1 financial defense (AMENDMENT §2) |
| `data_split_enforcer.py` (Spec 9, future) | ≥ 90% | Scientific integrity — highest value module |
| `kill_trigger_evaluator.py` (Spec 9, future) | ≥ 90% | Scientific integrity — highest value module |
| `pre_registration_hasher.py` (Spec 9, future) | ≥ 90% | Hash stability = experiment validity |
| `adapters/ledger/sqlite_ledger.py` | ≥ 80% | Transaction boundary correctness |
| `domain/events/event_bus.py` | ≥ 75% | Failure isolation logic |
| `infrastructure/config/` | ≥ 70% | Validation rules |
| `adapters/exchanges/` | ≥ 60% | I/O adapters (network mocking harder to kill) |

### 2.3 CI Pyramid Tier Schedule

Per SA-10 §13.

| Tier | Trigger | Target duration | What runs |
| ------ | --------- | ---------------- | ----------- |
| **PR** | Every push to open PR | < 60s | Unit tests, property tests (100 examples), mypy, ruff, import-linter, coverage gate, syrupy snapshots on changed modules; `pytest -n auto` for unit tests only |
| **Nightly** | Midnight UTC | < 30 min | Chaos suite (clock skew, WS drop, disk-full, duplicate response, WS reorder), golden-replay small fixtures (<200 events), contract tests (Binance public API), property tests (10,000 examples), mutation on changed modules (paths trigger), Hypothesis DB cache warm-up |
| **Weekly** | Sunday 02:00 UTC | < 4 hours | Full mutation run on all domain modules, calibration harness vs recorded shadow data, large replay fixtures (git-lfs or S3), atheris fuzz campaigns on WS parser + config loader (24h wall-clock via self-hosted runner) |

PR timing enforced with `pytest-timeout` per-test and `pytest -x` (fail-fast). Exceeding 60s
for the PR tier is a Yellow flag on the next weekly report. `pytest -n auto` parallelizes unit
tests; chaos and integration tests do NOT parallelize (SQLite single-writer).

---

## 3. Certification Lifecycle Mapping

### 3.1 States and Engineering Spec Gates

The following maps pre-registration §7 certification states to the engineering specs that provide
the entry criteria. A state cannot be entered until every listed spec is merged and its gate passes.

| Certification state | Entry criteria | Spec gate(s) required |
| -------------------- | ---------------- | ---------------------- |
| `UNTRUSTED` | Default initial state | — (automatic at system start) |
| `BACKTEST_CERTIFIED` | Backtest metrics on Train+Val pass: DSR > 0.7, bootstrap CI lower > 0, max drawdown < 15%, all secondary metrics pass (pre-registration §9 + §10 backtest kills) | Spec 9 (`H1AcceptanceGate` + `DataSplitEnforcer` enforcing Train window only); Spec 5 (strategy logic); Spec 6 (statistical primitives via `hermes.statistics.*`); Spec 1 (domain events + `LabNotebook` trial count) |
| `SHADOW_CERTIFIED` | 90 consecutive shadow days: M-02 p50 ≤ 30 bps; no P0 kill trigger fired; `KillTriggerEvaluator` reports clean; human approval via `hermes shadow-mode certify --confirm` | Spec 2 (fill-pair recording), Spec 3 (OrderRouter + risk gates), Spec 4 (live market data), Spec 6 (CalibrationService + `CERTIFIED` state machine), Spec 8 (RO-N rule-fire counters), Spec 9 (`KillTriggerEvaluator` consuming Spec 6 + Spec 8 results) |
| `OOS_CERTIFIED` | H1 accepted on OOS window: one-shot run on or after 2027-01-01 using `hermes certify --pre-registration <hash>`; DSR > 0.7 + CI lower > 0 + secondary metrics pass | Spec 9 (`DataSplitEnforcer` in `OOS_CERTIFICATION` mode; `hermes certify` CLI clock check against 2027-01-01; `H1AcceptanceGate`) |
| `LIVE_APPROVED` | `OOS_CERTIFIED` + operator sign-off (explicit CLI confirmation) | Spec 9 (`PreRegistrationSigned` event with git commit SHA recorded in `LabNotebook`) |
| `CERTIFIED` | 90-day live capital run with no kill trigger fired; ongoing monitoring per pre-registration §10 | Spec 8 (real-time overlay monitoring), Spec 9 (`KillTriggerEvaluator` continuous; `LabNotebook` status entries), Spec 6 (daily calibration report; P0 metrics within thresholds) |
| `DEAD` | Any kill trigger fired (terminal for this pre-registration) | Spec 9 (`KillTriggerFired` event → `DEAD` state; no override path; post-mortem deadline tracked in `LabNotebook`) |

### 3.2 Frozen Data Windows (Binding — from pre-registration §4)

These are reproduced here for cross-reference only. They are FROZEN in the pre-registration and
any apparent conflict resolves in favour of the pre-registration.

| Window | Start | End | Access rule |
| -------- | ------- | ----- | ------------- |
| Train | **2020-09-01** | 2024-06-30 | Full access during backtest development |
| Validation | 2024-07-01 | 2025-08-31 | Touch sparingly; every touch logged in `LabNotebook` |
| OOS Test | 2025-09-01 | 2026-12-31 | Physical refusal by `DataSplitEnforcer`; one-shot on/after 2027-01-01 only |

Note on Train start date: the pre-registration §4 sets 2020-09-01 (SOL uniform start to avoid
per-pair regime bias). ADR-011 consistency-check section erroneously states 2020-01-01; the
pre-registration is the binding document. `DataSplitEnforcer` must enforce 2020-09-01, not
2020-01-01. This discrepancy should be corrected in ADR-011.

---

## 4. Weekly Progress Reporting Cadence

### 4.1 What Gets Reported

Every Monday, one status entry is appended to `docs/experiments/swing-mvp1/lab-notebook.md`
(or to the Spec 9 `LabNotebook` event log once it is deployed; the two records are equivalent
until Spec 9 merges):

```text
## Week {N} status — {YYYY-MM-DD}

### Spec progress (Green / Yellow / Red per spec)
- Spec 7: GREEN — merged Week 1
- Spec 1: GREEN — merged Week 1
- Spec 4: YELLOW — Parquet round-trip determinism test intermittent
- Spec 2: RED — blocked on Spec 4 chaos-test green (nightly fails)
- Spec 3: NOT STARTED
- Spec 8: NOT STARTED
- Spec 5: NOT STARTED
- Spec 6: NOT STARTED
- Spec 9: NOT STARTED (blocked: needs Spec 5 + 6 + 8)

### Gate progress
- G1: PASSED (Week 1)
- G2: YELLOW (Spec 4 not yet green)
- G3-G8: NOT STARTED

### CI health
- PR tier: GREEN (avg 44s)
- Nightly: YELLOW (Parquet determinism flaky)
- Weekly: GREEN (mutation 91% on domain/lots)

### Blockers
- [BLOCKER] Spec 4 Parquet determinism intermittent on Windows CI runner
  Owner: implementer. Deadline: before G2 passes.

### Leading indicators toward G8 (2026-10-01)
- Train+Val backtest trial count: TBD (backtest not started; Spec 5 not merged)
- Shadow fill pair count: N/A (shadow not started)
- Weeks remaining to G8 target: {N}

### Next week plan
- Spec 4: fix Parquet determinism; re-request nightly green
```

Status definitions:

- **GREEN**: on track, no blockers, CI clean
- **YELLOW**: behind ≤ 1 week, or CI flaky, or minor issue requiring attention
- **RED**: behind > 1 week, or blocked on external dependency, or CI systematically broken

### 4.2 Escalation Triggers

Escalation means a synchronous operator-implementer conversation before the next weekly report.

| Trigger | Action |
| --------- | -------- |
| Any spec RED for 2 consecutive weeks | Synchronous review; milestone date renegotiated or scope cut |
| PR CI > 60s consistently (3+ PRs in a week) | Immediate investigation; not deferred to weekly |
| Nightly CI fails > 3 consecutive nights | Synchronous review; flaky test quarantined or fixed |
| Weekly mutation falls below target for any SEV-1 module 2 consecutive weeks | Investigation before next weekly run |
| Any proposed change to pre-registration §3/§4/§5/§9/§10 | Immediate operator notification; Spec 9 `DeviationDetector` raises; pre-registration invalidated if change proceeds |
| Any lookahead violation (M-13) in shadow mode | Immediate halt; synchronous post-mortem within 24h |
| Shadow drift breach (M-02 p50 > 30 bps sustained 7 rolling days) | Immediate halt per pre-registration §10; not deferred to weekly |
| G8 (SHADOW_CERTIFIED) looks at risk of missing 2026-10-01 target | Immediate scope/resource discussion |

---

## 5. Operational KPIs During Shadow Phase (~Weeks 15+)

Shadow begins after G7 (Spec 6 merged and first shadow run activated) and runs for a minimum
90 days. All thresholds per SA-5 §2. Measurements recorded daily in calibration JSON report.

### 5.1 Sim-vs-Live Drift Telemetry

| Metric | Green | Yellow | Red (BLOCK / HALT) | Cadence |
| -------- | ------- | -------- | ------------------- | --------- |
| M-02: Fill-price divergence p50 (bps) | ≤ 15 bps | 15–30 bps | > 30 bps sustained 7 rolling days → shadow kill (pre-registration §10) | Per 1,000 fills |
| M-02: Fill-price divergence p95 (bps) | ≤ 50 bps | 50–100 bps | > 100 bps | Per 1,000 fills |
| M-04: Bias direction | 0.40–0.60 | 0.35–0.40 or 0.60–0.65 | < 0.35 or > 0.65 | Per 1,000 fills |
| M-09: PnL correlation (Pearson r) | r ≥ 0.95 | 0.90–0.95 | r < 0.90 | Per 30 fills |
| M-10: Sharpe ratio relative divergence | ≤ 20% | 20–40% | > 40% | Daily |
| M-13: Lookahead integrity | 0 violations | — | Any violation → immediate UNTRUSTED | Continuous |
| M-15: Fee model accuracy (median) | ≤ 5% relative | 5–10% | > 10% | Per 100 fills |
| M-06: Trade count divergence | ≤ 5% | 5–10% | > 10% | Per 100 fills |

Calibration refresh cadence per SA-5 §3.4:

- Continuous: M-13 (lookahead) on every fill pair
- Per 100 fills: M-06, M-15
- Per 1,000 fills: M-02, M-04 (KS + Wasserstein)
- Daily midnight UTC: M-10, M-11, M-12, M-14
- After minimum 30 fills: M-09

### 5.2 Operational Metrics During Shadow

| Metric | Target | Alert threshold |
| -------- | -------- | ---------------- |
| Fill rate (submitted → confirmed) | ≥ 99% | < 97% → WARN; < 95% → CRITICAL |
| Latency: msg-in → position-updated (p50) | < 500ms | > 2s p50 → WARN |
| Latency: msg-in → position-updated (p99) | < 2s | > 5s p99 → CRITICAL |
| Error rate (unhandled exceptions) | < 0.1% of events | > 1% → HALT shadow engine |
| Heartbeat uptime | ≥ 99.9% (< 9 min/week downtime) | Any gap > 5 min during open position → SA-9 P0 rule violation |
| Spec 8 RO rule fires per 30-day rolling window | < 3 for any single rule | ≥ 3 fires of any one rule in 30 days → shadow kill per pre-registration §10; `KillTriggerEvaluator` declares DEAD |

### 5.3 Dashboard Requirements (Minimum Viable Before Shadow Operational)

**Per Spec 8 overlay rule (one chart per rule):**

- RO-01: 30-day rolling correlation BTC/ETH/SOL — live line with 0.80 threshold overlay
- RO-02/03: entry and exit slippage estimate (bps) — bar chart per order
- RO-04: bid-ask spread / 1H median spread ratio — time series with 10× threshold line
- RO-05: last book update age (ms) — time series with 2,000ms threshold line
- RO-06: USDT/USDC price deviation from $1.00 — time series with 1.5%/3% threshold lines
- RO-07: FOMC blackout window active status (binary) — calendar view
- RO-08: simultaneous ATR spike count — bar chart (trigger at ≥ 2 pairs)

**Per calibration metric (one chart per metric):**

- M-02: fill-price divergence p50/p95 (bps) — rolling time series with 30 bps threshold
- M-04: bias direction ratio — rolling time series with 0.35/0.65 threshold lines
- M-09: PnL correlation (Pearson r) — rolling 30-trade window
- M-10: Sharpe ratio sim vs real — dual-line time series

**Alert channels:**

- Telegram: all BLOCK and CRITICAL events, shadow HALT events
- Telegram: daily calibration digest (all metric statuses, GREEN/YELLOW/RED)
- CI gate: `ShadowModeGate.check_deploy_allowed()` reads `shadow_mode.status` from `hermes.db`
  before any live deployment step; UNTRUSTED status blocks deploy

---

## 6. Certification Progress — Leading Indicators for 2027-01-01

The OOS test cannot begin before 2027-01-01. G8 (SHADOW_CERTIFIED) must be reached by
approximately 2026-10-01 to preserve the 3-month buffer.

| Indicator | Target | When measured |
| ----------- | -------- | -------------- |
| G8 milestone reached | By 2026-10-01 | Track weekly against schedule |
| Train+Val backtest trial count | Log every run; DSR denominator grows | Every backtest run; recorded in `LabNotebook` (Spec 9) |
| Walk-forward Sharpe convergence on Val | Variance across 6-month expanding windows narrowing each quarter | Monthly during backtest development (Weeks 4–12) |
| Shadow drift M-02 p50 trend | ≤ 15 bps after first 200 fill pairs | Weekly during shadow phase |
| Spec 8 RO rule-fire rate | No rule fires ≥ 3 times in any 30-day window | Weekly shadow report |
| `KillTriggerEvaluator` shadow status | No shadow kill fired | Continuous; weekly report |

**On-track definition**: All six indicators Green by 2026-12-01 → OOS test proceeds as scheduled.
Any indicator Red by 2026-12-01 → synchronous operator decision on delay vs accept risk.
Delaying does not invalidate the pre-registration — it only shifts the date.

### 6.1 Backtest Metrics That Signal Pre-Certification Readiness

Monitored on Train+Val only (OOS window is never touched before 2027-01-01 per pre-registration §4):

| Metric | Target on Train+Val | Hard block? |
| -------- | --------------------- | ------------- |
| DSR on Val window | > 0.7 | Yes — pre-registration §10 backtest kill |
| Bootstrap 95% CI lower bound on Val Sharpe | > 0 | Yes |
| Max drawdown on Val | < 15% | Yes — pre-registration §10 backtest kill |
| Win rate on Val | ≥ 30% | No — monitored; < 30% triggers review |
| Profit factor on Val | > 1.3 | No — monitored |
| Ulcer index on Val | < 10 | No — secondary metric |

If any hard-block metric fails on Val, the spec run/parameter set is declared UNTRUSTED,
recorded in `LabNotebook` with trial count, and the trial count is added to the DSR denominator.
A new configuration requires a new `LabNotebook` entry and resets the trial-count denominator
for that experiment run (not the total; all trials across attempts count toward DSR adjustment).

---

## 7. Post-Certification Monitoring (Once CERTIFIED)

CERTIFIED state is entered after 90 days of live capital with no kill trigger fired
(pre-registration §7). Ongoing monitoring cadence applies indefinitely.

### 7.1 Ongoing Drift Monitoring

| Activity | Cadence | Action on failure |
| ---------- | --------- | ------------------ |
| Calibration harness re-run (all P0 metrics) | Daily midnight UTC digest | WARN if Yellow; HALT + operator notification if Red |
| M-02 p50 rolling check (last 1,000 live fills) | Per 1,000 fills | BLOCK if > 30 bps |
| M-09 PnL correlation rolling check (last 30 live trades) | Per 30 trades | BLOCK if r < 0.90 |
| M-13 lookahead integrity | Continuous | Immediate HALT on any violation |
| Full calibration report published | Weekly | Operator review |

### 7.2 Kill Trigger Monitoring Frequency

All kill triggers (pre-registration §10 live phase) monitored continuously by `KillTriggerEvaluator`
(Spec 9). The following additionally apply as scheduled checks:

| Kill trigger | Auto-monitoring | Scheduled check |
| ------------- | ---------------- | ----------------- |
| Max drawdown 15% peak-to-trough on experiment capital | Real-time on every position update | Daily digest |
| Time-to-recovery > 90d from any drawdown ≥ 8% | Real-time; timer starts at 8% breach | Daily digest |
| 30 rolling trades OOS Sharpe < 0.3 | Per 30 trades | Weekly operator review |
| Any SA-9 P0 rule violation (RO rules enforced on every order via Spec 8) | Real-time | Daily digest |
| Live fills diverging > 50 bps from shadow for 2 consecutive weeks | M-02 p50 nightly | Weekly operator review |

### 7.3 Re-Certification Triggers

Re-certification means returning to SHADOW_CERTIFIED and accumulating another 90-day clean shadow
window before re-entering CERTIFIED.

| Trigger | Re-certification required |
| --------- | -------------------------- |
| Annual review (every 12 months of CERTIFIED operation) | Yes — 90-day shadow; confirm drift thresholds still pass |
| Kill trigger near-miss (metric reaches Yellow sustained > 14 days) | Operator decision; document in `LabNotebook` |
| Any change to `FillModelPort` implementation (even bug fix) | Yes — sim accuracy must be re-validated |
| Exchange fee tier change | Yes — M-15 will fail; fix fee config + 90-day re-shadow |
| Underlying asset behaviour shift (ETF redemption mechanism change, new derivatives structure) | Operator discretion; reasoning documented in `LabNotebook` |
| Any kill trigger firing in the live phase | Strategy declared DEAD per pre-registration; post-mortem within 7 days; new pre-registration (v2) required |

A kill trigger firing is NOT re-certification — it terminates the experiment permanently for
this pre-registration. Post-mortem and a new pre-registration are the only path forward.

---

## 8. Appendix — Reference Mapping

| This document | Primary source |
| --------------- | --------------- |
| Spec 1–7 definitions + original 12-week timeline | SYNTHESIS.md §4–§5 |
| Spec 8 (Risk Overlay) definition + RO-01..RO-08 thresholds | SYNTHESIS-AMENDMENT.md §2; ADR-011 D-21 |
| Spec 9 (Strategy Development Protocol) definition | SYNTHESIS-AMENDMENT.md §3; ADR-011 D-22 |
| 14-16 week revised timeline | SYNTHESIS-AMENDMENT.md §8.3 |
| Mutation kill-rate targets per module | SA-10 §11.3 (via AMENDMENT §4) |
| CI pyramid three-tier model | SA-10 §13 (via AMENDMENT §4) |
| 17 property invariants INV-01..INV-17 | SA-10 §8.1 (via AMENDMENT §4) |
| Spec 4 storage split (SQLite events + Parquet L2) | SA-10 §6.1 + §6.3 (via AMENDMENT §5) |
| Drift telemetry thresholds (M-02, M-04, M-09, M-10, M-13, M-15) | SA-5 §2 acceptance thresholds table |
| Calibration refresh cadence | SA-5 §3.4 |
| Position sizing 5% cap | pre-registration-v1.md §5.4 (binding; supersedes SA-7 10% mention) |
| Data windows (Train/Val/OOS) | pre-registration-v1.md §4 (frozen) |
| Kill criteria (shadow, live) | pre-registration-v1.md §10 |
| Certification lifecycle states | pre-registration-v1.md §7 |
| Correlation lock threshold 0.80 | pre-registration-v1.md §5.5 + SA-9 §2.5 |

---

*Draft — not signed. Operator review required before treating any milestone date as committed.*
