- **spec-7 test infrastructure** (`pyproject.toml`, `docs/guides/test-tiers.md`,
  `openspec/changes/spec-7-test-infrastructure/`):
  Formalises the six-tier marker taxonomy (`unit`, `property`, `integration`,
  `chaos`, `mutation`, `contract`) in `[tool.pytest.ini_options]`. Adds
  `[tool.cosmic-ray]` targeting `hermes.domain` with a ≥ 85 % kill-rate
  baseline (per SA-10). Verifies all five `[tool.importlinter]` layer-isolation
  contracts and per-package coverage thresholds in `scripts/dev/coverage-check.py`
  (domain ≥ 95 %, application ≥ 90 %, adapters ≥ 85–90 %) — no rewrites needed.
  Introduces `docs/guides/test-tiers.md` as the single-page reference every
  subsequent spec cites for its test-plan section. CI wire-up for mutation
  (weekly cron) is a follow-up PR.
