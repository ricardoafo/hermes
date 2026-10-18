- **Spec 6 — Calibration Harness** (`src/hermes/domain/calibration/`,
  `src/hermes/application/calibration/`): validates `SimulatedExchangeAdapter`
  fill accuracy and drives the 6-state certification lifecycle for `swing-mvp1-v1`.
  (1) Seven pure-function metric calculators (`compute_m01`..`compute_m07`) in
  `src/hermes/domain/calibration/metrics.py`:
  M-01 total fill count;
  M-02 fill-price delta bps (avg + p50/p95/p99 — the PRIMARY calibration metric
  that validates `OrderbookWalkFillModel` accuracy using Decimal arithmetic per
  code-conventions.md §6);
  M-03 latency simulation (mocked to 0 for MVP1 backtest — placeholder for shadow mode);
  M-04 rejection rate;
  M-05 orphan position count (SA-4 P0 guard — every open position must trace to a fill);
  M-06 regime-tagged returns (bull×quiet, bull×vol, bear×quiet, bear×vol buckets);
  M-07 queue-position quality (placeholder returning `None` for MVP1 per OD-2).
  (2) `CalibrationHarness` orchestrator (`src/hermes/application/calibration/harness.py`)
  that accepts pre-collected fills, rejections, mid-prices, regime labels, and open-position
  IDs, calls the domain calculators, and emits a frozen `CalibrationReport` dataclass
  with all 7 metric values + window metadata.
  (3) `CertificationLifecycle` pure state machine
  (`src/hermes/application/calibration/lifecycle.py`) implementing the 6+1 state
  transition table from pre-registration-v1.md §7:
  `UNTRUSTED → BACKTEST_CERTIFIED → SHADOW_CERTIFIED → OOS_CERTIFIED → LIVE_APPROVED
  → CERTIFIED`; `DEAD` is the absorbing error state triggered by SA-4 P0 violations or
  100% rejection rate; `force_dead()` is the external kill-trigger integration point.
  (4) `FixtureBaselineJob` stub (`src/hermes/application/calibration/baseline.py`)
  implementing the `BaselineJob` Protocol for testnet-vs-testnet baseline comparison;
  replays pre-captured fills without live HTTP calls (actual testnet integration is
  Spec 6 follow-up).
  Full testing pyramid: 20 unit tests for metric calculators with hand-computed bps
  fixtures, 12 property tests covering the full state machine transition matrix via
  Hypothesis, 10 integration tests exercising end-to-end harness over a synthetic
  5-fill data window and FixtureBaselineJob.
  Closes #240.
