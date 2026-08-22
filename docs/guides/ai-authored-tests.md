# AI-Authored Tests — Discipline & Enforcement

**Type**: Guide (living document — PR any change)
**Owners**: Code Owner
**Applies to**: every test file in this repo, whether authored by a human or an AI agent (`rafo-claude-bot`).
**Description**: What makes a unit test meaningful, how AI typically fails at writing tests, the protocol AI must follow in this repo, and the tooling that enforces it.

This guide exists because AI-authored tests have a specific failure mode: they *look* like tests, they satisfy line-coverage gates, and they pass — but they don't catch bugs. This guide is the discipline that prevents that from happening in hermes.

## The core problem

Coverage-driven testing tells you which lines executed. It tells you nothing about whether those executions asserted anything meaningful. An AI (or a hurried human) can hit 100% line coverage with tests that:

- Call the function.
- Assert `is not None` on the result.
- Never exercise a boundary, a failure, or an invariant.

That test suite is worse than none: it looks like a safety net while catching nothing.

## What makes a unit test *meaningful*

A meaningful test satisfies **all** of the following:

1. **Tests behavior, not implementation.** The test asserts what the code should *do*, not *how* it does it. A refactor that preserves behavior must not break the test. A behavior change (bug or intentional) must break exactly one test.
2. **Would fail if the code had a real bug.** Not "if we deleted the whole function", but "if we broke the invariant". Mutation testing is the objective measure of this.
3. **Names describe what CANNOT happen.** `test_fifo_never_consumes_from_future_lot` is meaningful. `test_fifo_works` is not. The name is a spec fragment.
4. **Asserts against the output, not internal state.** If the test needs to peek at private state to know if it passed, the code has a design gap or the test is coupled to implementation.
5. **One test = one behavior claim.** Multi-assertion tests that check "everything is right after this call" mask *which* thing broke.
6. **Inputs represent real scenarios**, not just what the code will accept. Include boundary values, empty collections, single-element collections, degenerate cases, and the failure modes documented in the type contract.

## Failure modes specific to AI-authored tests

Cataloged from observed patterns (mine and others'). If a test you wrote or reviewed fits one of these, it is not doing its job.

### 1. Assertion inflation

Many trivial `assert result is not None`, `assert len(x) > 0`, `assert x == x` — none of which would fail if the function did the wrong thing.

**Symptom**: high assertion count, high line coverage, zero mutation kill on the tested branch.

**Fix**: replace with one specific behavior assertion. `assert order.total == Decimal("42.13")` beats ten `is not None` checks.

### 2. Testing the mock

The test mocks the collaborator, then asserts the mock was called with certain args, then reports pass. The real code is never exercised.

**Symptom**: test passes when the tested function's body is deleted (only mock setup remains).

**Fix**: mock only external boundaries (network, filesystem, time). Integration-test the seams. If a mock is the ONLY assertion, the test is testing itself.

### 3. Coverage-driven writing

"Add tests until pytest-cov shows ≥ 90%." The tests are drawn to code paths, not to invariants. Uncovered lines get a single execution and no assertion.

**Symptom**: coverage green, mutation kill rate mediocre, PRs still ship bugs.

**Fix**: start from the *spec* (what invariants the code must satisfy), write tests for those, THEN check coverage as a sanity floor.

### 4. Snapshot abuse

Snapshot testing that locks in current output for functions that have no canonical output. The first snapshot captures whatever the code produces (bugs included). Every subsequent PR that changes the output "just updates the snapshot" — the test asserts nothing beyond "the output hasn't changed unexpectedly".

**Symptom**: snapshot files updated without any human review of the diff.

**Fix**: snapshots only for outputs that are genuinely canonical (JSON schema, generated code, config-file rendering). Everything else uses behavior assertions.

### 5. Happy-path only

Tests exercise the common case. Never the empty input, the malformed input, the boundary, the concurrent access, the failure recovery.

**Symptom**: production bugs are all "unexpected input" or "timing issue".

**Fix**: for pure-logic modules, add property-based tests (hypothesis) — the framework will find edge cases you didn't think of. For stateful code, list failure modes explicitly and test each.

### 6. Over-mocking

Mocking so much of the surface that the test only exercises the glue code. The domain logic is never actually run.

**Symptom**: mutation testing kills few mutants in the domain logic; refactoring the domain doesn't break tests.

**Fix**: separate pure logic from I/O. Test pure logic without any mocks. Mock only at the I/O boundary.

### 7. Frozen-time obsession

Every test freezes time to a specific timestamp. The code's behavior around real time (DST, month boundaries, leap seconds, timezone conversions) is never tested.

**Symptom**: production bug on Jan 1 or during DST transition.

**Fix**: frozen time for reproducibility, but ALSO parametrize tests over meaningful time boundaries (year-end, month-end, DST transitions in Europe/Madrid).

## The hermes protocol for AI-authored tests

When you (an AI agent) author a test in this repo, you MUST:

1. **Write a docstring naming the invariant.** One sentence. `"""FIFO consumption never draws from a lot dated after the sale."""` — not "test fifo function".

2. **Start from the spec, not the code.** Look at the ADR, the interface contract, or the failing production incident. Enumerate the invariants the code must satisfy. Write one test per invariant. THEN look at coverage.

3. **For pure-logic modules: add a property-based test.** Hypothesis will find edge cases. One `@given` decorator is worth ten hand-crafted examples. Use `@example` to pin specific regressions.

4. **For every mock, name the integration test at the seam.** Add a comment: `# integration: tests/integration/exchange_adapter/test_binance_ws.py::test_reconnect_survives_1006`. If you can't name one, the mock is untethered — either add the integration test or don't mock.

5. **Assert on outputs, not internal state.** If the assertion requires accessing a private attribute, either widen the public surface (if the state is genuinely relevant) or restructure the test to observe behavior instead.

6. **Assert exactly the values that would change if the invariant broke.** Not `is not None`. Not `> 0`. The specific value or the specific relationship.

7. **One behavior claim per test.** If you find yourself using `and` in the docstring, split the test.

8. **Include the failure modes.** Every function that can raise MUST have a test that verifies the specific exception on the specific bad input. Every branch in the code MUST have a test that exercises it with a real input pattern, not just a coverage-hit.

9. **When updating a test to accommodate a code change: STOP.** If the code change is a behavior change, the test SHOULD have failed — that's the point. Updating the test to make it pass is silencing the alarm. Ask: "is the new behavior correct, or did I just break the invariant the test was protecting?"

For tests beyond Tier 1 (unit): the same nine rules apply, plus the tier-specific
considerations in the next section. *(See [Test pyramid: tiers 2 and 3](#test-pyramid-tiers-2-and-3) for the integration test tier definition and markers.)*

## Test pyramid: tiers 2 and 3

Unit tests (this guide's primary focus) sit at Tier 1 of the hermes pyramid. Two more
tiers exist. Each has a different marker, directory, dependency profile, and CI cadence.

| Tier | Scope | Dependencies | Marker | Directory | When to write |
| ---- | ----- | ------------ | ------ | --------- | ------------- |
| 1 — unit | Pure functions, value objects, single-class invariants | None (no I/O, no time, no network) | *(none — the default)* | `tests/unit/` | Every branch, every invariant, every failure mode. See §"The hermes protocol". |
| 2 — integration | Adapter-to-collaborator seams | Fakes for external services; SQLite `:memory:` for stateful ports; `respx` for HTTP | `@pytest.mark.integration` | `tests/integration/` | When code crosses a port boundary — the seam is the contract, so test it end-to-end from the port's perspective. |
| 3 — e2e | Full stack against real testnet | Live testnet (Binance testnet, Telegram bot in test mode) | `@pytest.mark.e2e` | `tests/e2e/` | One happy-path per user story. Not exhaustive — smoke coverage that the deployed system responds. |

**Marker cross-reference**: all four pytest markers live in `pyproject.toml`
`[tool.pytest.ini_options].markers` (`smoke`, `integration`, `e2e`, `slow`). The `slow`
marker overlays any tier — a unit test that takes >1s wall-clock, or a slow e2e that
should skip on developer laptops, both mark `slow`. Do not invent new markers without
updating `pyproject.toml`.

**Fake vs mock vs testnet policy**: use fakes (in-memory implementations of the port
protocol) over mocks for adapter tests — fakes validate real behaviour, mocks only
validate call signatures. Mock only at the I/O boundary where a fake is impractical.
Testnet means a real external service (Binance testnet, Telegram test bot) — tests
must be idempotent and use dedicated test-account funds and credentials only.

**Density guidance for hermes specifically** — this is a real-money system where a bug
means real losses. Tier 2 density is higher than in a typical CRUD app:

- **Phase 0/1 (now)**: 100% Tier 1. No adapters yet, no integration tests needed.
- **Phase 2 target**: ≥ 90% Tier 1, ≤ 10% Tier 2, 0% Tier 3 in the PR gate. Tier 3
  runs nightly/manual dispatch only — never blocks a PR merge.
- Every adapter gets a full integration suite covering: (a) happy path, (b) transient
  network failure with retry, (c) permanent failure with correct error propagation,
  (d) rate-limit response, (e) data-shape mismatch (test what the exchange actually
  returns, not what the docs claim it returns).

**Tier 2/3 status (as of Phase 0)**: `tests/integration/` and `tests/e2e/` directories
exist as empty scaffolds. Real tests land in Phase 2 when adapter code lands. This
section is forward-looking so the discipline is documented before the code exists —
avoids the "we will add tests later" trap.

## AI development lifecycle × test tier

This matrix maps every phase of `docs/guides/ai-development-lifecycle.md` to which
test tier is expected to move. It answers: "when should I be writing which tier of test?"

| Lifecycle phase | Tier 1 (unit) | Tier 2 (integration) | Tier 3 (e2e) |
| --------------- | ------------- | -------------------- | ------------ |
| SDD explore | — | — | — |
| SDD propose | — | — | — |
| SDD spec | Enumerate invariants (inputs for unit tests) | Enumerate adapter contracts | Enumerate flow guarantees |
| SDD design | Design test structure (fixtures, hypothesis strategies) | Design fakes vs real stubs | Design testnet fixture and secrets access |
| SDD tasks | One task per invariant | One task per adapter behaviour | One task per user story |
| SDD apply | **WRITE** (mandatory for every code task) | **WRITE if the task touches a port** | Skip — Phase 3 concern |
| SDD verify | **RUN** — must pass locally before commit | **RUN if written** | Skip |
| SDD archive | — (tests already committed) | — | — |
| PR open | Must pass in CI (blocks merge) | Must pass in CI if written | — |
| Merge to main | Weekly cosmic-ray run (`mutation.yml`, Sundays 03:00 UTC) | On-merge run in `unit` job | Nightly / manual dispatch (`e2e.yml`) |
| Deploy | Smoke (`@pytest.mark.smoke`) fires post-deploy | — | Manual e2e against prod-mirror testnet |
| Post-incident | Add regression unit test (every incident produces a test) | Add integration test if the incident crossed a port | Add e2e if the incident was cross-service |

**Code Owner review** — every PR touching production code is reviewed against this
matrix. Missing Tier 1 for a new function → REQUEST CHANGES. Missing Tier 2 for a new
adapter method → REQUEST CHANGES. Missing Tier 3 for a new user story → note for
follow-up (does not block merge, does block phase-close).

## Enforcement in this repo (Phase 0+)

Rules the CI pipeline enforces after `phase-1-prep-test-discipline` PR A (GH #116):

| Rule | Tool | Gate | Status |
| ---- | ---- | ---- | ------ |
| Every test has a docstring | ruff `D103` (tests-only via `per-file-ignores`) | pre-commit + CI `quality` job | live |
| Coverage floor (global) | coverage.py `fail_under = 90`, branch coverage | CI `unit` job — pytest exits non-zero | live |
| Coverage floor (per-package) | `scripts/dev/coverage-check.py` (no `--bootstrap`) | CI `unit` job — per-package thresholds | live |
| No sole-vague test assertion | `scripts/dev/no-vague-test-assertions.py` (AST walker) | pre-commit hook — blocks commit | live |
| Mutation kill floor | cosmic-ray (chosen — see `docs/decisions/2026-08-22-mutation-testing-cosmic-ray.md`) | ≥ 80% kill rate — advisory 2026-08-22 → 2026-09-05, blocking after | live (advisory) |
| Property-based tests exist | manual review + `grep hypothesis` | reviewer checklist | manual |
| No snapshot without reviewer comment on the diff | pytest-snapshot with review annotation | manual for now, tooling later | manual |
| Test files never deleted without justification | `guards` job (test-deletion guard) | CI `guards` job | live |

The **mutation kill rate** is the load-bearing gate. Line coverage is table stakes — it
tells you the test EXECUTED the line. Mutation testing tells you the test would DETECT
if that line's logic were subtly wrong. Until the mutation gate is live (GH #118), the
combination of D103 (docstring names the invariant), no-vague-assertions (the assertion
tests it), and per-package coverage (every path exercised) is the interim substitute.

## Tooling summary

- **pytest** — test runner. Fixtures are code — review them like production code.
- **hypothesis** — property-based testing. Use for pure functions with data inputs (FIFO, tax calculations, price aggregation).
- **cosmic-ray** — mutation testing. Runs weekly (`.github/workflows/mutation.yml`); kill rate is the real correctness gate. See `docs/decisions/2026-08-22-mutation-testing-cosmic-ray.md` for the tool choice + spike evidence.
- **coverage.py** with `--branch` — coverage floor as sanity check, not a goal.
- **pytest-cov** — reporting in CI.
- **pytest-xdist** — parallel execution (mutation runs are compute-heavy).
- **freezegun** or `pytest-freezer` — frozen time when needed. **Also** parametrize over real time boundaries.
- **ruff** with test-focused rules (`D`, `PT`) — test hygiene.

## The one question to ask before submitting

Before opening the PR, look at each test you wrote and ask:

> **"If I broke the invariant this test claims to protect, would this test fail?"**

If the answer is "not necessarily" — the test isn't doing its job. Rewrite it or delete it. A test that doesn't detect the bug it's supposed to detect is a liability, not an asset.

## Reading list (worth 30 min before writing tests)

- Kent Beck, *Test-Driven Development: By Example* — the canonical short book
- David Farley, *Modern Software Engineering*, chapter on testing
- GeePaw Hill's blog posts on "TDD as a design activity"
- Julian Berman on property-based testing (talks and hypothesis docs)
- Any recent post from Stripe, Cloudflare, or Google engineering blogs on mutation testing in practice

## Related

- `AGENTS.md` — pointer here from the "Writing code" section
- `docs/decisions/2026-08-21-revalidate-then-merge.md` — merge queue design (decisions module in `tools/merge/` is an example of the discipline applied)
- Phase 0 issues: mutation testing setup, hypothesis integration, coverage gates
