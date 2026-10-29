# Proposal: phase-0-python-toolchain

**SDD change**: `phase-0-python-toolchain`
**Date**: 2026-08-21
**Status**: Proposed
**Deciders**: Maintainer

Mirror of engram topic key `sdd/phase-0-python-toolchain/proposal` (see ADR-009 §6 dual-source rule).

## Executive Summary

Phase 0 lays the Python toolchain foundation for hermes: `pyproject.toml` with hatchling + `src/` layout, `uv.lock` committed, three-tier `tests/` skeleton, seven-package `src/hermes/` skeleton, full lint/type/security tool config, four CI job bodies (quality, unit, guards, semgrep) and two CD job bodies (build, smoke), pre-commit Python hooks, and enforcement plumbing (import-linter contracts, `sev1_guard.py`, per-package coverage script). Architectural picks: **Python 3.13** (revert the accidental 3.14 venv), **hatchling** as build backend, **mypy --strict** as the sole type checker, **semgrep** as the sole security scanner (bandit dropped), all CI + CD jobs on the self-hosted Docker/Linux runner, and a **custom coverage-check script** to enforce ADR-007's per-package thresholds. Main risk: empty `src/hermes/` packages fail zero-warning coverage gates on day one — mitigated by a documented **bootstrap-thresholds mode** that ratchets up as code lands.

## Scope

### In

- `pyproject.toml` with build backend, direct deps (compatible-release specifiers), dev deps, all tool sections (ruff, mypy, pytest, coverage, importlinter, mutmut, hatch, vulture)
- `src/hermes/{domain,application,ports,adapters,infrastructure,entrypoints}/__init__.py` (7 packages including `adapters/{exchanges,ledger,notifier,clock}` sub-packages)
- `tests/{unit,integration,e2e}/__init__.py` + `tests/conftest.py` + `tests/plugins/sev1_guard.py` + one self-test
- `.importlinter` contracts (three forbidden contracts, embedded in `pyproject.toml`)
- `.python-version` pinning `3.14`
- Remove `uv.lock` from `.gitignore`; commit initial lock
- `.pre-commit-config.yaml` additions — ruff (check + format), mypy, import-linter via `uv run` / `language: system`
- Fill CI job bodies: `quality`, `unit`, `guards`, `semgrep` in `.github/workflows/ci.yml` (all on self-hosted)
- Fill CD job bodies: `build`, `smoke` in `.github/workflows/deploy.yml`
- `scripts/dev/coverage-check.py` — per-package threshold enforcement over `coverage json`, with `--bootstrap` flag
- Empty `known_advisories.yaml` scaffold (Dependabot already live at `.github/dependabot.yml` — no `renovate.json`)

### Out (deferred)

- Actual domain code (Money, FIFO, Portfolio) → Phase 1
- Exchange adapter concrete implementation → deferred until ADR-004 resolves (Phase 1 or 2)
- Textual TUI + rich CLI entrypoint bodies → Phase 2+
- `hermes-mcp.service` and LLM supervisor → Phase 3+
- Weekly `mutmut` GH Actions workflow file → Phase 1 (needs mutation targets to exist)
- systemd unit files + VPS bootstrap fill → Phase 4 (deploy prep)
- gh-runner Dockerfile with semgrep pre-baked → Phase 4 (Phase 0 uses `returntocorp/semgrep-action`)

## Decisions

Each decision cites the ADR / invariant / constraint it derives from. Cost of reversal = LOW unless flagged.

### D-1: Python version pin — **3.14** (latest stable)

- **Decision**: `requires-python = ">=3.14,<3.15"` in `pyproject.toml`; `.python-version` = `3.14`; keep existing `.venv` (already 3.14).
- **Alternatives**: (a) revert to 3.13.x (ADR-003 §07 stale wording); (b) unpin upper bound.
- **Rationale**: 3.14 is the current stable Python release. No compatibility blocker (empty scaffold, all deps support 3.14). Staying current buys ~12 months more of security-fix window vs pinning to 3.13. Upper bound pin prevents drift on next `uv resolve`. **Supersedes** the exploration's "revert to 3.13" recommendation; that was based on stale ADR-003 wording.
- **ADR follow-up**: ADR-003 README §64 ("Python 3.11+") and §07 ("Python 3.11 via uv") need refresh to "Python 3.14". Handled outside this change (decisions-journal entry or ADR amendment — see § Dependencies).
- **Cost of reversal**: LOW (bump `requires-python`, re-lock).

### D-2: `uv.lock` committed

- **Decision**: Remove `uv.lock` line from `.gitignore`; commit the lockfile; CI + VPS use `uv sync --frozen`.
- **Alternatives**: keep gitignored + regenerate on install.
- **Rationale**: ADR-010 §1 explicit: "`uv.lock` is the resolved, hash-pinned lockfile. **Committed** to git." Hash-pinning is the supply-chain defense — it does not exist without the file.
- **Cost of reversal**: LOW.

### D-3: Build backend — **hatchling**

- **Decision**: `[build-system] requires = ["hatchling"]; build-backend = "hatchling.build"`; `[tool.hatch.build.targets.wheel] packages = ["src/hermes"]`.
- **Alternatives**: `flit-core`, `setuptools`, `poetry-core`.
- **Rationale**: hatchling is the modern default for `src/` layout, is what `uv init` produces, handles editable installs cleanly.
- **Cost of reversal**: LOW.

### D-4: Type checker — **mypy --strict**, no pyright

- **Decision**: `mypy --strict src/` in CI. Config in `[tool.mypy]`. No pyright.
- **Alternatives**: pyright, both.
- **Rationale**: ADR-003 §09 CI table lists `mypy --strict` explicitly. Two type checkers duplicate warnings and multiply suppression surface.
- **Cost of reversal**: MEDIUM (`# type: ignore` migration cost).

### D-5: Per-package coverage enforcement — **custom script**

- **Decision**: `scripts/dev/coverage-check.py` parses `coverage json` output and asserts each package meets its threshold from a table matching ADR-007. Invoked by CI `unit` after `pytest --cov`.
- **Alternatives**: N invocations of `coverage report --include=... --fail-under=X`; `coverage-conditional-plugin`; single global threshold.
- **Rationale**: `coverage.py`'s `fail_under` is scalar. A ~40-line script gives one clean fail message per violating package.
- **Cost of reversal**: LOW.

### D-6: Bootstrap coverage thresholds for empty packages

- **Decision**: `coverage-check.py` supports `--bootstrap` flag that treats packages with 0 executable lines as PASS. Phase 0 CI runs with the flag; drop it once packages have real code.
- **Alternatives**: (a) set all thresholds to 0 and ratchet later (invisible drift); (b) skip coverage entirely in Phase 0 (loses CI wiring test).
- **Rationale**: We need the coverage pipeline exercised on day one. Empty `__init__.py` files legitimately have no branches.
- **Cost of reversal**: LOW.

### D-7: Three import-linter `forbidden` contracts

- **Decision**: In `[tool.importlinter]`, three contracts:
  1. **`domain-is-pure`**: `hermes.domain` may not import from `application|ports|adapters|infrastructure|entrypoints`.
  2. **`application-imports-only-domain-and-ports`**: `hermes.application` may not import from `adapters|infrastructure|entrypoints`.
  3. **`adapters-never-import-entrypoints`**: `hermes.adapters` may not import from `entrypoints`.
- **Alternatives**: single `layers` contract.
- **Rationale**: ADR-003 §01 places `ports/` as sibling of `domain/`. Three explicit `forbidden` rules produce clearer error messages than one `layers` block.
- **Cost of reversal**: LOW.

### D-8: Pre-commit Python hooks — **`language: system` via `uv run`**

- **Decision**: ruff, mypy, import-linter hooks all use `language: system` + `entry: uv run <tool>`.
- **Alternatives**: pre-commit-managed venvs with pinned rev per tool.
- **Rationale**: Single source of truth = `pyproject.toml` + `uv.lock`. Pre-commit-managed venvs duplicate the version list and drift. Dev-loop speed: `uv run` reuses the warm project venv. Windows/Linux portability: `uv run <tool>` — no shell scripts, no shebangs.
- **Cost of reversal**: MEDIUM (full `pre-commit install` re-run on every dev machine).

### D-9: CI + CD execution — **self-hosted runner (Docker/Linux)**

- **Decision**: ALL CI jobs (quality, unit, guards, semgrep) AND CD jobs (build, smoke) run on the self-hosted Docker/Linux runner. Existing `runs-on: ${{ vars.RUNNER_LABEL && fromJSON(vars.RUNNER_LABEL) || 'ubuntu-latest' }}` pattern already in place.
- **Alternatives**: split (unit/quality on GH-hosted for faster minutes, integration on self-hosted).
- **Rationale**: Self-hosted is Docker on Linux (not Windows) — no dev/CI mismatch. Zero GH-hosted minutes cost. One execution environment = simpler debugging.
- **Cost of reversal**: LOW (change `runs-on`).

### D-10: Security scanning — **semgrep only, bandit dropped**

- **Decision**: `returntocorp/semgrep-action@v1` with `p/security-audit` + `p/python` rulesets. No bandit in `pyproject.toml`, no bandit pre-commit hook.
- **Alternatives**: bandit + semgrep (belt-and-braces); bandit only.
- **Rationale**: For a Python-only repo, semgrep's `p/python` ruleset supersedes bandit's coverage. Semgrep is actively maintained, rule-based, cross-language-ready. Two scanners = duplicated findings + double config drift.
- **Cost of reversal**: LOW.

### D-11: Mutation testing — **config in `pyproject.toml`, workflow deferred to Phase 1**

- **Decision**: `[tool.mutmut]` config lands in `pyproject.toml` now, targets `src/hermes/domain/`. No `.github/workflows/mutmut.yml` file until Phase 1 (when domain code exists).
- **Alternatives**: create workflow now with trivial pass.
- **Rationale**: ADR-007 says "weekly, not blocking on every PR". Empty domain = trivially passing workflow = noise.
- **Cost of reversal**: LOW.

### D-12: Exchange library placeholder — **port only, no adapter**

- **Decision**: `src/hermes/ports/exchange.py` defines `ExchangePort` Protocol. `src/hermes/adapters/exchanges/__init__.py` is empty. No `binance-connector` or `ccxt` dep in `pyproject.toml`.
- **Alternatives**: pick one speculatively.
- **Rationale**: ADR-004 undecided. Unused deps trigger `vulture` / audit noise. Port-only keeps Phase 1 type-check target without library commitment.
- **Cost of reversal**: LOW.

### D-13: `known_advisories.yaml` scaffold only — **Renovate dropped, Dependabot already live**

- **Decision**: Create empty `known_advisories.yaml` with header comment + `advisories: []`. Do NOT create `renovate.json`.
- **Alternatives**: (a) create `renovate.json` per ADR-010 §2 stated decision; (b) defer supply-chain hygiene entirely.
- **Rationale**: `.github/dependabot.yml` is already live and well-configured (weekly Monday 06:00, 7-day cooldown, grouped by prod/dev, reviewers set). ADR-010 §2 says "Renovate over Dependabot" — that decision was reversed in practice and ADR-010 is stale. Creating `renovate.json` now would duplicate + conflict with active Dependabot.
- **ADR follow-up**: ADR-010 §2 + Update-cadence table + Consequences need refresh to reflect Dependabot as the chosen tool. Handled outside this change.
- **Cost of reversal**: LOW.

## Risks

- **3.14 wheel availability for some transitive deps**: most first-party libraries publish 3.14 wheels by now, but a long tail (older C-extension libs) may lack them. Mitigation: `uv lock` will fail loudly on the first missing wheel; downgrade the affected dep or pin its version. If more than 2-3 deps hit this, revisit the 3.14 pin.
- **PyCharm regression on interpreter**: PyCharm may cache the old venv metadata. Fix: File → Invalidate Caches → point interpreter at existing `.venv/Scripts/python.exe`. Document in `docs/guides/local-setup.md`.
- **`radon cc --min B` may flag any function with an `if`**: threshold `B` = complexity 6-10. Empty `__init__.py` passes. First real domain function may need refactoring — acceptable per zero-warning ethos.
- **`vulture` false positives on port Protocols**: Protocol methods with `...` bodies read as unused. Mitigation: `min_confidence = 80` + `exclude = ["src/hermes/ports/"]`.
- **Self-hosted laptop offline during CD `smoke`**: `smoke` job queues, doesn't block. Deploy proceeds via `git pull` cron (ADR-003 §09 Option A).

## Test Plan (high level)

Phase 0 is complete when:

1. `uv sync --frozen` completes on Windows dev machine + Linux CI runner.
2. `uv run pytest tests/` collects 0 tests, exits 0.
3. `uv run mypy --strict src/hermes` reports 0 errors.
4. `uv run ruff check . && uv run ruff format --check .` reports 0 issues.
5. `uv run import-linter` reports 3 contracts, all passing.
6. `uv run python scripts/dev/coverage-check.py --bootstrap` exits 0, all packages marked "empty — bootstrap PASS".
7. `pre-commit run --all-files` passes cleanly.
8. All CI required jobs (quality, unit, guards, semgrep) green on the scaffolding PR.
9. `sev1_guard.py` unit-tested via synthetic skipped `# SEV-1:` test.
10. `.github/workflows/deploy.yml` `build` + `smoke` jobs green on manual dry-run dispatch from main.

## Dependencies on other changes

- **ADR-004 (exchange)** stays undecided — accommodated (D-12).
- **ADR-009 amended 2026-08-21** — this change spec exists in `openspec/changes/` because of that amendment; without it, artifacts would live in engram only.
- **ADR-003 wording drift** — resolved by [`docs/decisions/2026-08-21-python-3-14-pin.md`](../../../docs/decisions/2026-08-21-python-3-14-pin.md), which supersedes the specific "3.11" text in ADR-003 README §64 and §07. ADR body unchanged (immutability).
- **ADR-010 tool substitution** — resolved by [`docs/decisions/2026-08-21-dependabot-over-renovate.md`](../../../docs/decisions/2026-08-21-dependabot-over-renovate.md), which supersedes ADR-010's §Renovate choice. ADR body unchanged (immutability).
- **GH repo settings**: no additional action (Dependabot needs no app install; the ruleset requiring `ci-success` aggregator is already in place).

## Next Recommended

`spec.md` (see sibling file).
