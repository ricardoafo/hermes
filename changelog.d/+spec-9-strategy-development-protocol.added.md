- **Spec 9 — Strategy Development Protocol** (`src/hermes/application/strategy_protocol/`):
  five scientific guardrail modules that make every subsequent spec's outputs admissible
  as pre-certification evidence for the Swing MVP1 experiment (`swing-mvp1-v1`).
  (1) `PreRegistration` parser — typed accessor layer over the SIGNED
  `docs/experiments/swing-mvp1/pre-registration-v1.md` frontmatter and binding
  constants (data windows, universe, sizing config, §9 H1 thresholds, §10 kill
  triggers); validates `experiment_id` at init time to catch file tampering.
  (2) `DataSplitEnforcer` — transparent `MarketDataPort` wrapper that raises
  `OOSDataLeakageError` when code attempts to read a bar or trade with timestamp
  inside the OOS window (`2025-09-01 → 2026-12-31`) unless `certification_context()`
  is active; implements the code-enforced read refusal mandated by pre-reg §4.
  (3) `H1AcceptanceGate` — evaluates `CertificationResult` against all §9 thresholds
  (DSR, bootstrap CI, max drawdown, ulcer index, recovery days, win rate) and returns
  `AcceptanceDecision(accepted, failed_criteria)` with a full audit trail of failed
  criteria.
  (4) `KillTriggerEvaluator` — evaluates `ExperimentState` against §10 kill criteria
  (drawdown >= 15%, SA-9 HALT, 90-day recovery breach, rolling Sharpe < 0.3) and
  returns `KillDecision(triggered, reason)`.
  (5) `LabNotebook` append-only writer — enforces the anti-p-hacking append-only
  invariant for `docs/experiments/swing-mvp1/lab-notebook.md`; computes per-entry
  SHA256 comment for integrity verification; raises `LabNotebookAppendError` on
  duplicate-header attempts.
  Domain errors `OOSDataLeakageError` and `LabNotebookAppendError` added to
  `src/hermes/domain/errors.py`. Full test suite: 5 modules, property tests via
  Hypothesis for parser window isolation and gate threshold matrices.
  Closes #226.
