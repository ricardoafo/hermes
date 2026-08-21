# Spec: phase-0-python-toolchain

**SDD change**: `phase-0-python-toolchain`
**Date**: 2026-08-21
**Status**: Proposed

Mirror of engram topic key `sdd/phase-0-python-toolchain/spec` (see ADR-009 §6).

Format: `R-N` = requirement. `A-N.M` = acceptance criterion. Each `A-N.M` is a shell-verifiable check.

## R-1: Python runtime pinned to 3.14 (latest stable)

- **A-1.1**: `pyproject.toml` contains `requires-python = ">=3.14,<3.15"`
- **A-1.2**: `.python-version` file exists and contents = `3.14`
- **A-1.3**: `python --version` inside `.venv` reports `Python 3.14.x`

## R-2: Package management is uv with committed lockfile

- **A-2.1**: `uv.lock` is tracked in git (NOT in `.gitignore`)
- **A-2.2**: `uv sync --frozen` succeeds on Linux CI runner
- **A-2.3**: `pyproject.toml` uses `[build-system] requires = ["hatchling"]` + `build-backend = "hatchling.build"`
- **A-2.4**: `[tool.hatch.build.targets.wheel]` declares `packages = ["src/hermes"]`
- **A-2.5**: `uv build --wheel` produces a valid wheel at `dist/hermes-*.whl`

## R-3: Source layout matches ADR-003 §01

- **A-3.1**: `src/hermes/__init__.py` exists
- **A-3.2**: Sub-packages exist as directories with `__init__.py`: `domain`, `application`, `ports`, `adapters`, `adapters/exchanges`, `adapters/ledger`, `adapters/notifier`, `adapters/clock`, `infrastructure`, `entrypoints`
- **A-3.3**: `python -c "import hermes"` inside `.venv` succeeds
- **A-3.4**: No `main.py` at repo root

## R-4: Test layout supports ADR-007 tiers

- **A-4.1**: `tests/{unit,integration,e2e}/__init__.py` exist
- **A-4.2**: `tests/conftest.py` exists and registers the `sev1_guard` plugin
- **A-4.3**: `tests/plugins/sev1_guard.py` exists; unit-tested via synthetic `# SEV-1:` skipped test
- **A-4.4**: `pyproject.toml` `[tool.pytest.ini_options]` declares `testpaths = ["tests"]`, `markers = ["smoke", "integration", "e2e", "slow"]`
- **A-4.5**: `uv run pytest tests/` collects 0 items, exits 0

## R-5: Linting + formatting = ruff, zero warnings

- **A-5.1**: `[tool.ruff]` config present in `pyproject.toml`
- **A-5.2**: `uv run ruff check .` exits 0 with 0 findings
- **A-5.3**: `uv run ruff format --check .` exits 0 with 0 findings

## R-6: Type checking = mypy --strict, zero errors

- **A-6.1**: `[tool.mypy]` config with `strict = true` in `pyproject.toml`
- **A-6.2**: `uv run mypy --strict src/hermes` exits 0

## R-7: Import contracts enforce hexagonal boundaries

- **A-7.1**: `[tool.importlinter]` declares 3 `forbidden` contracts: `domain-is-pure`, `application-imports-only-domain-and-ports`, `adapters-never-import-entrypoints`
- **A-7.2**: `uv run import-linter` exits 0, reports "3 contracts, all passing"

## R-8: Security scanning = semgrep only (bandit dropped)

- **A-8.1**: `bandit` is NOT in `pyproject.toml` dev deps
- **A-8.2**: `bandit` is NOT in `.pre-commit-config.yaml`
- **A-8.3**: CI `semgrep` job uses `returntocorp/semgrep-action@v1` with configs `p/security-audit` + `p/python` + `--error`
- **A-8.4**: Semgrep run on empty `src/` scaffold exits 0

## R-9: Supply chain audits pass; Dependabot already live; safety dropped

- **A-9.1**: `uv run pip-audit --skip-editable` exits 0 (no vulnerabilities). `--strict` dropped because it turns the local-editable skip into an error; `--skip-editable` alone is correct.
- **A-9.2**: `safety` is NOT in dev deps. Dropped 2026-08-22 — v3.7.0 needs interactive auth or `--key`, and `safety scan` crashes on our stack. pip-audit + Dependabot alerts + semgrep cover the ground. See [`docs/decisions/2026-08-22-drop-safety.md`](../../../docs/decisions/2026-08-22-drop-safety.md).
- **A-9.3**: `.github/dependabot.yml` exists and is unchanged by Phase 0 (already live pre-change)
- **A-9.4**: `known_advisories.yaml` exists at repo root with header comment and `advisories: []`
- **A-9.5**: NO `renovate.json` file (Dependabot supersedes ADR-010 §2's stated Renovate decision)

## R-10: Complexity + dead-code guards active

- **A-10.1**: `uv run radon cc --min B src/` exits 0 (no functions ≥ complexity B)
- **A-10.2**: `uv run vulture src/ --min-confidence 80` exits 0
- **A-10.3**: `[tool.vulture]` in `pyproject.toml` excludes `src/hermes/ports/` (Protocol stubs)

## R-11: Coverage with per-package thresholds + bootstrap mode

- **A-11.1**: `scripts/dev/coverage-check.py` exists, reads `coverage.json`, applies per-package thresholds:

  | Package                          | Threshold |
  | -------------------------------- | --------- |
  | `src/hermes/domain`              | 95%       |
  | `src/hermes/application`         | 90%       |
  | `src/hermes/adapters/ledger`     | 90%       |
  | `src/hermes/adapters/exchanges`  | 80%       |
  | `src/hermes/adapters/notifier`   | 85%       |
  | `src/hermes/entrypoints`         | 70%       |

- **A-11.2**: Script accepts `--bootstrap` flag; packages with 0 executable lines pass instead of fail
- **A-11.3**: `uv run pytest tests/unit/ --cov=src/hermes --cov-branch --cov-report=json` produces `coverage.json`
- **A-11.4**: `uv run python scripts/dev/coverage-check.py --bootstrap` exits 0 on the Phase 0 scaffold

## R-12: Pre-commit Python hooks via `uv run`

- **A-12.1**: `.pre-commit-config.yaml` adds hooks for ruff-check, ruff-format, mypy, import-linter — all `language: system` with `entry: uv run <tool>`
- **A-12.2**: `pre-commit run --all-files` exits 0 on the Phase 0 scaffold
- **A-12.3**: No `bandit` hook

## R-13: CI jobs `quality`, `unit`, `guards`, `semgrep` have functional bodies (self-hosted)

- **A-13.1**: `quality` runs: uv sync → ruff check → ruff format --check → mypy --strict → import-linter → pip-audit → safety check → radon cc --min B → vulture
- **A-13.2**: `unit` runs: uv sync → pytest with `--cov=src/hermes --cov-branch --cov-report=json` → `coverage-check.py --bootstrap`
- **A-13.3**: `guards` runs test-deletion guard (git-diff-based; deleted `tests/**/*.py` require `SEV-1:` header in PR body)
- **A-13.4**: `semgrep` runs via `returntocorp/semgrep-action@v1` with the two rulesets
- **A-13.5**: All four jobs green on the scaffolding PR; `ci-success` aggregator green

## R-14: CD `build` + `smoke` job bodies functional

- **A-14.1**: `build` runs `uv sync --frozen && uv build --wheel`, uploads `dist/*.whl` artifact
- **A-14.2**: `smoke` runs `uv sync --frozen && uv run pytest -m smoke --tb=short` (0 smoke tests in Phase 0 = trivially passes)

## R-15: `[tool.mutmut]` config exists, no CI workflow file

- **A-15.1**: `[tool.mutmut]` in `pyproject.toml` with `paths_to_mutate = ["src/hermes/domain/"]`
- **A-15.2**: NO `.github/workflows/mutmut.yml` file (deferred to Phase 1)

## Cross-cutting Definition of Done

Phase 0 ships when a single PR opens with all above changes and:

- All 15 CI jobs green (including the 4 new-body jobs), `ci-success` aggregator green
- CD `build` + `smoke` green on a manual dry-run dispatch from `main`
- Human approval + squash-merge via the revalidate-then-merge queue
- Post-merge, `main` remains at `ci-success` green
