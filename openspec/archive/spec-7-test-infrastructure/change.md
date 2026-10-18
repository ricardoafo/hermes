# Change: spec-7-test-infrastructure

**SDD change**: `spec-7-test-infrastructure`
**Date**: 2026-08-25
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#220)

Mirror of engram topic key `sdd/spec-7-test-infrastructure/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Audits the existing test infrastructure, fills the gaps needed for subsequent
specs to cite a single canonical reference, and formalises the six-tier marker
taxonomy (`unit`, `property`, `integration`, `chaos`, `mutation`, `contract`).
Mutation testing is configured via `[tool.cosmic-ray]` targeting
`src/hermes/domain/**/*.py` with a ≥ 85 % kill-rate baseline (per SA-10). The
five `[tool.importlinter]` layer-isolation contracts are verified in place.
Coverage thresholds (`domain ≥ 95 %`, `adapter ≥ 85–90 %`, `application ≥ 90 %`)
are verified in `scripts/dev/coverage-check.py`. The new
`docs/guides/test-tiers.md` reference is the single page every subsequent spec
cites for its test-plan section. No new test dependencies are added.

## Scope

### In

- `openspec/archive/spec-7-test-infrastructure/change.md` + `issue.md`
- `pyproject.toml` `[tool.pytest.ini_options]` — expand `markers` block
  with `unit`, `property`, `integration`, `chaos`, `mutation` taxonomy
  (alongside existing `smoke`, `integration`, `e2e`, `slow`, `contract`).
- `pyproject.toml` `[tool.cosmic-ray]` — mutation testing configuration
  targeting `hermes.domain`, baseline kill-rate ≥ 85 %.
- **Verified (no rewrite)**: `[tool.importlinter]` — all five contracts in
  place covering the three SA-10 forbidden imports; cited in D-3 below.
- **Verified (no rewrite)**: `scripts/dev/coverage-check.py` enforces
  `domain ≥ 95 %`, `application ≥ 90 %`, all adapter sub-packages ≥ 85–90 %;
  cited in D-4 below.
- `docs/guides/test-tiers.md` — new one-page marker taxonomy + CI pyramid
  tier reference for subsequent specs.
- `changelog.d/wave-spec-7-test-infrastructure.md`

### Out

- Actually running mutation tests (CI wire-up is a follow-up PR).
- Chaos engineering framework (out of MVP1).
- Rewriting `docs/guides/ai-authored-tests.md` (stays authoritative).
- Adding new test dependencies.
- Modifying `.github/workflows/ci.yml` or `nightly-quality.yml` — CI wiring
  is a follow-up; this spec documents intent only.

## Decisions

### D-1: Six-tier marker taxonomy, not three

- **Decision**: Adopt six markers: `unit`, `property`, `integration`, `chaos`,
  `mutation`, `contract` — plus the three legacy markers already present
  (`smoke`, `e2e`, `slow`) for backward compatibility.
- **Alternatives**: (a) keep the three legacy markers unchanged — insufficient
  for the CI pyramid described in SA-10; (b) collapse `property` into `unit` —
  loses the ability to filter Hypothesis tests separately in the nightly run.
- **Rationale**: SA-10 §CI pyramid tiers requires distinct labels per tier so
  `addopts` can pin PR CI to fast tests without excluding contract tests
  explicitly. Six labels map cleanly onto three CI tiers.
- **Cost of reversal**: LOW — remove markers from `pyproject.toml` and update
  `docs/guides/test-tiers.md`.

### D-2: cosmic-ray targets domain only, not all src

- **Decision**: `[tool.cosmic-ray]` `module-path = "hermes.domain"` with
  `python-paths = ["src"]`; excludes application and adapters layers.
- **Alternatives**: (a) target all of `src/hermes` — mutation runs become
  hours-long and the kill rate target is meaningless across layers with
  different testability profiles; (b) target per-module granularity — over-
  engineered for MVP1.
- **Rationale**: Domain is the highest-value mutation target (pure logic, no
  I/O, fastest to test). Adapter and application layers are covered by
  integration tests where mutation is harder to interpret.
- **Cost of reversal**: LOW — change `module-path` in `pyproject.toml`.

### D-3: importlinter contracts verified, not rewritten

- **Decision**: All five existing `[tool.importlinter]` contracts are in place
  and cover the three SA-10 forbidden imports. No new contracts needed.
- **Contracts present**:
  - `domain-is-pure` — domain cannot import application/ports/adapters/infrastructure/entrypoints
  - `application-imports-only-domain-and-ports` — application cannot import adapters/infrastructure/entrypoints
  - `adapters-never-import-entrypoints` — adapters cannot import entrypoints
  - `domain-no-pydantic` — domain cannot import pydantic or pydantic_settings
  - `domain-no-async-or-network` — domain cannot import asyncio/anyio/httpx/socket/requests/aiohttp/urllib
- **Cost of reversal**: N/A (verification, not a new decision).

### D-4: coverage-check.py thresholds verified, not raised

- **Decision**: Existing `scripts/dev/coverage-check.py` thresholds satisfy
  the SA-10 baseline. No changes needed.
- **Thresholds present**: `src/hermes/domain: 95`, `src/hermes/application: 90`,
  all `src/hermes/adapters/*: 85`, `src/hermes/entrypoints: 70`.
- **Cost of reversal**: N/A (verification).

## Requirements

### R-1: Marker taxonomy in pyproject.toml

- **A-1.1**: `python -c "import tomllib; t=tomllib.load(open('pyproject.toml','rb'));
  markers=[m.split(':')[0] for m in t['tool']['pytest']['ini_options']['markers']];
  assert 'unit' in markers and 'property' in markers and 'mutation' in markers
  and 'chaos' in markers, markers"` exits 0.
- **A-1.2**: `uv run pytest --co -q 2>&1 | grep -q 'no tests ran\|selected'`
  exits 0 (markers parse without error).

### R-2: cosmic-ray config present

- **A-2.1**: `python -c "import tomllib; t=tomllib.load(open('pyproject.toml','rb'));
  assert 'cosmic-ray' in t['tool']"` exits 0.
- **A-2.2**: `python -c "import tomllib; t=tomllib.load(open('pyproject.toml','rb'));
  assert t['tool']['cosmic-ray']['module-path']=='hermes.domain'"` exits 0.

### R-3: test-tiers.md exists and is complete

- **A-3.1**: `test -f docs/guides/test-tiers.md` exits 0.
- **A-3.2**: File contains `## PR CI tier`, `## Nightly tier`, and
  `## Weekly tier` sections.

### R-4: importlinter contracts in place (verified)

- **A-4.1**: `uv run lint-imports 2>&1 | grep -q 'All contracts'` exits 0 on
  a clean codebase.

### R-5: coverage thresholds in place (verified)

- **A-5.1**: `python scripts/dev/coverage-check.py --bootstrap --report
  coverage.json` exits 0 when coverage.json exists with correct format.

## Design

### pyproject.toml — markers expansion

Expand the `markers` list in `[tool.pytest.ini_options]`. Existing `smoke`,
`integration`, `e2e`, `slow`, `contract` markers stay; add `unit`, `property`,
`chaos`, `mutation` with tier annotations.

### pyproject.toml — cosmic-ray section (new)

```toml
[tool.cosmic-ray]
module-path = "hermes.domain"
python-paths = ["src"]
timeout = 30.0
excluded-modules = []

[tool.cosmic-ray.execution-engine]
name = "local"

[tool.cosmic-ray.distributor]
name = "local"
```

### docs/guides/test-tiers.md (new)

Structure:

1. Overview table (marker → tier → CI schedule → wall-clock cap)
2. Three H2 sections: PR CI tier / Nightly tier / Weekly tier
3. Coverage thresholds table
4. Layer-isolation contracts table
5. Running locally cheat-sheet
6. References

## Test plan

1. Every acceptance criterion (A-1.x through A-5.x) passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. `uv run pytest --co -q` — markers parse without error.
4. `HERMES_SELF_TEST=1 bash scripts/dev/render-pr-body.sh spec-7-test-infrastructure`
   — body renders with `Closes #220`.

## Risks

- **Risk 1 — existing `integration` marker conflict**: The original `integration`
  marker description references SQLite / testnet; the new taxonomy maps
  `integration` to tier-2. The descriptions are compatible; no rename needed.
  Mitigation: description text in `pyproject.toml` updated to be tier-aware.
- **Risk 2 — cosmic-ray not run in this PR**: The `[tool.cosmic-ray]` config is
  added but not exercised. The weekly CI wire-up is a follow-up. Risk: the
  config may have a runtime error discovered only in CI. Mitigation: TOML
  validated by `python -c "import tomllib; tomllib.load(...)"` in A-2.1.

## Dependencies on other changes

- **SA-10** (`docs/research/paper-trading/sa10-python-perf-testing.md`): the
  CI pyramid tiers this change formalises. Not modified.
- **#220** (tracking issue): this PR closes it.
- **Subsequent specs** (spec-8 onward): MUST cite `docs/guides/test-tiers.md`
  in their test-plan section instead of hand-rolling tier descriptions.
