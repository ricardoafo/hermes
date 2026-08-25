# Test Tiers — Marker Taxonomy and CI Pyramid

**Type**: Guide (living document — PR any change)
**Owners**: Ricardo Afonso
**Applies to**: every test in this repo and every spec test-plan section.
**Description**: Six-tier marker taxonomy, CI pyramid tier assignments, coverage thresholds, and layer-isolation contracts — the single reference subsequent specs cite.

Canonical reference for the six-tier marker taxonomy used across all spec
test-plan sections. Established by `spec-7-test-infrastructure` (issue #220).

## Overview

| Marker        | Tier | CI schedule          | Max wall-clock |
|---------------|------|----------------------|----------------|
| `unit`        | 1    | Every PR             | < 1 s/test     |
| `property`    | 1    | Every PR             | < 5 s/test     |
| `integration` | 2    | Nightly (push→main)  | < 60 s/test    |
| `chaos`       | 2b   | Nightly (push→main)  | < 60 s/test    |
| `contract`    | 2b   | On-demand / nightly  | < 120 s/test   |
| `mutation`    | 3    | Weekly cron          | session-level  |
| `e2e`         | 3    | Weekly / manual      | < 300 s/test   |

Legacy markers `smoke` and `slow` are preserved for backward compatibility;
`smoke` maps to post-deploy validation (not part of the pyramid CI tiers).

## PR CI tier

Runs on **every pull request** via `.github/workflows/ci.yml`.
Must complete in < 60 s total.

Markers included: `unit`, `property`

```bash
uv run pytest -m "unit or property" --timeout=30
```

Rules:

- No network calls, no filesystem writes outside `tmp_path`.
- No sleep, no real-time waits.
- Hypothesis profiles use `settings(max_examples=50)` for PR runs;
  `max_examples=500` is reserved for nightly via `@settings(suppress_health_check=...)`.
- Every test at this tier must pass on Windows AND Linux (CI runners may differ).

## Nightly tier

Runs on **push to main** and via the nightly cron job.
Timeout budget: 10 minutes total.

Markers included: `integration`, `chaos`, `contract`

```bash
uv run pytest -m "integration or chaos or contract" --timeout=120
```

Rules:

- `integration` — may use SQLite `:memory:`, local filesystem, testnet
  sandbox APIs. No real money, no production endpoints.
- `chaos` — fault-injection: drop network, corrupt responses, force retries.
  Uses `respx` or `unittest.mock` to simulate failures deterministically.
- `contract` — real HTTP to third-party sandboxes (Telegram Bot API, Slack,
  Frankfurter). Requires secrets in the environment; skipped when absent.
  See `docs/decisions/2026-08-23-contract-test-tier.md` for the rationale.

## Weekly tier

Runs via **weekly cron** (`.github/workflows/mutation.yml` — follow-up PR).

Markers included: `mutation`, `e2e`

```bash
# Mutation testing — cosmic-ray (see [tool.cosmic-ray] in pyproject.toml)
uv run cosmic-ray init cosmic-ray.toml session.sqlite
uv run cosmic-ray exec session.sqlite
uv run cr-report session.sqlite

# End-to-end
uv run pytest -m "e2e" --timeout=300
```

Kill-rate target for domain layer: ≥ 85 % (per SA-10). The cosmic-ray
config targets `hermes.domain` exclusively — see `[tool.cosmic-ray]` in
`pyproject.toml` and the Decision D-2 rationale in
`openspec/changes/spec-7-test-infrastructure/change.md`.

## Coverage thresholds

Enforced by `scripts/dev/coverage-check.py` on every PR:

| Package                                  | Threshold |
|------------------------------------------|-----------|
| `src/hermes/domain`                      | 95 %      |
| `src/hermes/application`                 | 90 %      |
| `src/hermes/adapters/ledger`             | 90 %      |
| `src/hermes/adapters/exchanges`          | 85 %      |
| `src/hermes/adapters/*` (other)          | 85 %      |
| `src/hermes/infrastructure/sentry_*`     | 85 %      |
| `src/hermes/entrypoints`                 | 70 %      |

Overall fail-under gate: 90 % (set in `[tool.coverage.report]`).

## Layer-isolation contracts

`[tool.importlinter]` enforces five forbidden-import contracts that ensure
domain purity (verified by `spec-7-test-infrastructure`, no rewrite needed):

| Contract name                               | What it guards                                              |
|---------------------------------------------|-------------------------------------------------------------|
| `domain-is-pure`                            | Domain cannot import application/adapters/infra/entrypoints |
| `domain-no-pydantic`                        | Domain cannot import pydantic or pydantic_settings          |
| `domain-no-async-or-network`                | Domain cannot import asyncio/anyio/httpx/socket/...         |
| `application-imports-only-domain-and-ports` | Application cannot import adapters/infra/entrypoints        |
| `adapters-never-import-entrypoints`         | Adapters cannot import entrypoints                          |

Run locally: `uv run lint-imports`

## Running locally

```bash
# Fast PR tier only
uv run pytest -m "unit or property"

# Full nightly tier
uv run pytest -m "integration or chaos or contract"

# Everything except e2e and mutation
uv run pytest -m "not e2e and not mutation"

# Check coverage thresholds
uv run pytest --cov=src/hermes --cov-report=json:coverage.json
python scripts/dev/coverage-check.py --report coverage.json
```

## References

- `docs/research/paper-trading/sa10-python-perf-testing.md` — SA-10 CI pyramid source
- `docs/decisions/2026-08-23-contract-test-tier.md` — contract tier rationale
- `docs/guides/ai-authored-tests.md` — AI-authored test conventions (authoritative)
- `openspec/changes/spec-7-test-infrastructure/change.md` — decisions + verification record
- `pyproject.toml` §`[tool.pytest.ini_options]` — marker definitions
- `pyproject.toml` §`[tool.cosmic-ray]` — mutation config
