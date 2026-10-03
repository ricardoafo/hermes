# ADR-007: Testing strategy — pytest + property tests + snapshot tests + targeted mutation testing, with SEV-1 protection at every layer

**Date**: 2026-08-20
**Status**: Accepted
**Accepted**: 2026-08-21
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: —
**Superseded in part by**: [ADR-017](adr-017-test-tiers-as-enforced.md) (tier cadences, mutation tool and scope, coverage table, test data)

## Context

Hermes moves real money on real exchanges. Every code path that decides
whether to trade, how much to trade, what the tax basis is, or what the
user sees on their phone touches value directly. The cost of a false
positive (a test passes when the code is wrong) is not "a bug ticket" — it
is a mispriced trade, a wrong tax number, or a missed anomaly notification.

Simultaneously, this is a solo-operator project. Testing infrastructure that
would be justified at a 10-engineer trading firm (property-based tests over
every function, mutation testing on every commit, integration environments
that mirror production) becomes bureaucracy when a single person maintains
it. The question is not "what is best-practice testing?" but "which testing
practices earn their maintenance cost at N=1 while preserving the SEV-1
protection the money demands?"

The severity rubric (`~/.claude/rules/severity-rubric.md`) categorizes bugs
by blast radius: SEV-1 is a tenant-cross data leak, tax miscalculation,
auth bypass, or an LOPDGDD violation. Tests covering SEV-1 paths must
exist, must run in CI, must never be quarantined without triggering a
release block. Sub-SEV-1 tests can be more selectively covered.

## Decision

Hermes uses a **four-tool test stack**, applied non-uniformly by test
category. Coverage is not a target; **SEV-1 protection** is the target.

### Tool stack

| Layer                | Tool                              | Applies to                                                        |
| -------------------- | --------------------------------- | ----------------------------------------------------------------- |
| Runner + assertions  | `pytest`                          | Everything                                                        |
| Property-based tests | `hypothesis`                      | Domain invariants (FIFO, money arithmetic, weight math, decimals) |
| Snapshot tests       | `syrupy`                          | CLI output, Telegram templates, report generation                 |
| Mutation testing     | `mutmut`                          | Domain path only, sampled; never blanket coverage                 |
| Fixtures / factories | Plain functions, `pytest.fixture` | Everywhere; avoid `factory_boy` complexity                        |
| Coverage measurement | `coverage.py`                     | Reported per-package with per-package thresholds                  |
| Concurrent test runs | `pytest-xdist`                    | CI only; local runs single-process for debugability               |
| HTTP mocking / VCR   | `respx` (`httpx` adapter)         | Adapter contract tests; matched pair with real testnet suite      |

### Test categorization

Tests are divided into three tiers, each with different runtime
expectations and CI treatment.

#### Tier 1 — Unit (`tests/unit/`)

Pure domain and application-layer tests. No I/O, no exchange, no ledger,
no config file reads. All external dependencies mocked or supplied as
test doubles.

- **SLA**: whole tier runs in **under 10 seconds** on the developer's
  machine. If it takes longer, something is wrong (real I/O has crept
  in).
- **Property-based tests** live here: every domain invariant that can
  be expressed as "for all inputs of shape X, output has property Y"
  gets a `hypothesis` test.
  Examples: FIFO invariant (sum of consumed lots == sold quantity;
  realized gain == price × consumed - basis × consumed); Money
  arithmetic invariants (associativity, no negative-quantity leakage,
  no unit mixing); portfolio-weight normalization (Σweights == 1
  after normalization).
- **Coverage target**: 90%+ line, 100% on money math, 100% on FIFO
  calculator.

#### Tier 2 — Integration (`tests/integration/`)

Adapter-level tests that exercise real external systems in
non-production configurations.

- **Ledger integration** — SQLite in-memory (`:memory:` DB). Runs on
  every push; ~1 second overhead per test class.
- **Exchange contract tests** — Binance testnet + Kraken testnet
  (matching adapter). Runs on CI's self-hosted runner (laptop, not
  VPS) because testnet API keys sit on the laptop only. Nightly
  cadence + on-PR when adapter code changes; not blocking on every
  push (rate limits + latency).
- **Notification templates** — full `send()` path against a
  Telegram bot in a test chat. Runs on-PR when template code
  changes.
- **SLA**: whole tier under 5 minutes on CI. Contract tests may
  timeout individually and be retried once.

#### Tier 3 — End-to-end (`tests/e2e/`)

Full-loop tests that boot the engine, run a synthetic market cycle
against testnet, and verify the ledger + notifications reflect the
expected trades.

- Runs **nightly** on the self-hosted runner. Not blocking on
  every PR; blocking on release-candidate cuts.
- One test per major user flow: `dry_run_rebalance_cycle`,
  `swing_entry_and_stop_hit`, `reconciliation_after_missed_cycle`,
  `weekly_report_generation`.
- **SLA**: whole tier under 15 minutes.

### SEV-1 test annotation

Every test covering a SEV-1 rubric row is annotated in a comment above
the test block, following the format from the rubric:

```python
# SEV-1: Security axis S2 — tenant-isolation IDOR
def test_rebalancer_query_includes_tenant_scope() -> None: ...
```

Rules:

- A quarantined test that covers a SEV-1 path **blocks release** until
  re-validated. `pytest.skip` on a `# SEV-1:` test triggers a red CI
  status even if the rest of the suite passes. Enforced by a small
  pytest plugin (`sev1_guard.py`) that runs at session end.
- Every SEV-1 fix PR must include a test that exercises the specific
  code path fixed and carries the `# SEV-1:` annotation. Absence is a
  reviewer-required block.
- The list of SEV-1 paths is not exhaustive; the annotation exists to
  make the ones we know about explicit and durable.

### Mutation testing — targeted, not blanket

Mutation testing (mutating the code under test and asserting the test
suite fails) is expensive: mutation runs are minutes to hours per
target. Blanket mutation coverage is not affordable at solo scale.

The scope is **narrow and deliberate**:

- **FIFO calculator** — every mutation must be caught. This is the
  tax-critical hotspot.
- **Money arithmetic value objects** — every mutation caught.
- **Rebalance decision function** (the pure function that says
  "trade to weight" given portfolio + target + band).
- **Sizing policy** (the function that turns volatility forecast into
  position size).

Mutation testing runs **weekly** (scheduled `mutmut` workflow) rather
than per-PR. Escaped mutants get logged as findings and prioritized;
they do not gate individual PRs.

### Coverage — targets per package, not overall

Overall coverage as a number is misleading (a 95% overall coverage that
misses the FIFO path is worse than 80% overall that includes it). Per-package
thresholds are more meaningful:

| Package                          | Line coverage target | Rationale                                                                     |
| -------------------------------- | -------------------- | ----------------------------------------------------------------------------- |
| `src/hermes/domain/`             | ≥ 95%                | Pure functions; no reason to skip coverage. Money math + FIFO must be 100%    |
| `src/hermes/application/`        | ≥ 90%                | Use cases; may skip trivial DTO passthroughs                                  |
| `src/hermes/adapters/ledger/`    | ≥ 90%                | Every SQL query exercised at least once                                       |
| `src/hermes/adapters/exchanges/` | ≥ 80%                | Error-path coverage relies on contract tests; unit tests hit the happy paths  |
| `src/hermes/adapters/notify/`    | ≥ 85%                | Templates + delivery paths                                                    |
| `src/hermes/cli/`                | ≥ 70%                | Snapshot tests cover most; edge-error branches sometimes only via integration |
| `src/hermes/tui/`                | ≥ 60%                | Textual widgets rely more on snapshot + `teatest` than line coverage          |

CI publishes per-package numbers and fails the build if any drops below
its threshold.

### Test data — deterministic, in-tree

- **No random test data.** `hypothesis` tests use their own strategy
  generation, seeded deterministically per run. All other tests use
  fixed sample data checked into `tests/data/`.
- **No network in unit tests.** Ever. If a unit test needs an HTTP
  response, it uses a `respx` mock or a canned fixture in
  `tests/data/http/`.
- **`freezegun` for time.** Every test that depends on the current
  time freezes it explicitly to `2026-08-20T12:00:00Z` or another
  fixed instant. Test flakiness from clock drift is not tolerated.
- **Test isolation** — every test that touches SQLite gets a fresh
  `:memory:` DB. No shared-state fixtures. Cross-test pollution is
  a bug.

## Why

### Why pytest, not `unittest`

Pytest's fixture model, parametrize decorator, plugin ecosystem
(`pytest-xdist`, `respx`, `syrupy`, `hypothesis` all integrate as
plugins), and assertion introspection ("expected 42, got 41" with
diff view) are meaningfully better than `unittest`'s. This is not
a close call; pytest is the modern Python default.

### Why hypothesis for property-based tests

Money arithmetic and FIFO computation have a lot of invariants that
example-based tests exercise poorly:

- "The sum of realized gains from any partial SELL equals the sum of
  (sell_price - buy_basis) × qty_consumed across consumed lots" —
  hypothesis generates thousands of buy/sell sequences and checks the
  invariant on each.
- "For any two Money instances a and b of the same currency,
  `(a + b) - b == a`" — hypothesis finds the decimal-precision edge
  cases that example tests would need to be told about.
- "For any portfolio and target weights and band, the rebalance
  function either produces zero trades OR produces trades that move
  the portfolio inside the band" — hypothesis-derived counterexamples
  are useful bug reports even when they are false positives (they
  reveal an unstated assumption).

Hypothesis is the tool of choice for these; there is no serious
alternative in Python.

### Why syrupy for snapshot tests

CLI output, Telegram templates, and report generation are content that
matters to the operator. Any change to formatting, wording, table
padding, or Rich rendering should be a conscious change reviewable in a
PR. Snapshot tests turn "text output" into a reviewable artifact:

- First run creates the snapshot in `tests/snapshots/`.
- Every subsequent run compares actual output to the snapshot.
- A change requires the reviewer to run `pytest --snapshot-update` and
  visually verify the diff before committing.

This is the difference between "did I mean to change this table
column?" being a question that gets asked in review vs. one that gets
missed.

### Why targeted mutation testing, not blanket

Blanket mutation testing produces reports full of mutations in
uninteresting places (config parsing, DTO field access, printout
formatting) that dominate the findings and hide the important
escapes. Targeting the four functions where every mutation matters —
FIFO, money, rebalance decision, sizing — is a more useful use of
compute.

The rest of the codebase relies on line coverage + property tests +
integration tests to catch regressions. Mutation testing is an
audit tool on the hotspots, not a coverage substitute.

### Why per-package coverage targets

Line coverage as a single number invites gaming. 100% coverage of
DTO getters + printf calls is trivial and worth zero; 100% coverage
of FIFO is essential and hard. Per-package thresholds put the
attention where it belongs.

### Why strict TDD on domain, standard TDD elsewhere

The domain layer is pure functions with algebraic properties. Writing
the property first (or the example first) forces the invariant to be
articulated before the implementation, which is where domain bugs
originate. Strict TDD ("no domain code without a failing test first")
is affordable there.

Application layer + adapters have more incidental complexity
(orchestration, protocol translation) where test-after is often
clearer than test-first. Standard TDD ("test before merge, not
necessarily before first line") applies there.

The `sdd-init` workflow flags whether the project has strict TDD
mode active; hermes has it active for domain.

### Why not Docker for testing

The project's operational stance is Docker-free (see
[ADR-003 section 09](./adr-003-solution-architecture/09-deployment.md)).
Docker-only test environments would be dishonest — production is
systemd, not containers. Testing on the same substrate production
runs on is the higher-fidelity choice. Contract tests hit real
testnet APIs; e2e tests boot the real app under `pytest` control.

## Consequences

### Positive

- **SEV-1 protection is auditable.** Every SEV-1 test carries an
  annotation; the sev1_guard plugin enforces that they run and pass.
- **Property tests catch the FIFO edge cases that example tests
  would need to be told about.** The tax-critical calculator is
  hardened by hypothesis rather than by hand-picked corner cases.
- **CLI + Telegram formatting is protected against silent changes.**
  A refactor that swaps `f"{x:.2f}"` for `f"{x:0.2f}"` shows up as
  a snapshot diff, not as a wrong number on someone's phone.
- **Per-package coverage targets reflect actual risk.** No time
  wasted padding coverage on trivial code; no risk of skipping
  coverage on critical code because the overall number is fine.

### Negative

- **First-time hypothesis strategies are non-trivial.** Writing a
  strategy that generates plausible portfolios or plausible price
  sequences takes an afternoon per new domain concept.
- **Mutation testing is a scheduled activity.** Weekly cadence
  means escaped mutants live for up to a week before detection.
  Acceptable for hermes' change velocity; would be tighter for
  a busier codebase.
- **Contract tests against testnet consume rate limits.** Nightly
  cadence + on-adapter-change is a compromise between "test
  every push" (rate-limit-hostile) and "test never" (regressions
  slip through).
- **Snapshot tests are noisy in review.** A rewording of one
  Telegram template regenerates the snapshot file for that
  template. The reviewer must actually inspect the diff, not
  approve reflexively.

### Neutral

- **Test data lives in `tests/data/`** — committed, small, human-
  readable. No test-data database.
- **Fixtures use plain `pytest.fixture`.** `factory_boy` and
  friends are not needed at this scale; a Python function that
  returns a Portfolio is easier to read than a factory DSL.
- **Test parallelism is CI-only** via `pytest-xdist`. Local runs
  are single-process to preserve `pdb` usability.

## What would change this decision

- **Change velocity increases** (multiple engineers, daily merges).
  Mutation testing may need per-PR gates; contract tests may need
  faster feedback loops. Both are addressable within this framework.
- **Regulatory audit requires 100% domain coverage assertion.**
  Fully expressible as a package-threshold change.
- **Property-based approach turns out to be insufficient for a
  specific class of bug** (e.g. a numerical-stability issue that
  hypothesis's shrinking doesn't reach). Case-specific test
  approach (statistical convergence tests, e.g. `pytest-benchmark`
  for numerical drift). Additive, not replacing.

## Cross-references

- [ADR-001](./adr-001-llm-as-supervisor.md) — LLM does not touch
  test-writing for production paths; property tests are
  human-authored
- [ADR-002](./adr-002-tax-posture.md) — FIFO calculator is the
  test-critical hotspot named by tax posture
- [ADR-003 section 03](./adr-003-solution-architecture/03-persistence-schema.md)
  — schema tests use `:memory:` fixtures
- [ADR-003 section 04](./adr-003-solution-architecture/04-exchange-adapters.md)
  — exchange contract-test approach in more detail
- ADR-006 — Versioning strategy (backward-compatibility assertion
  test enumerates prior minor's schema + config)
- ADR-008 — Observability strategy (structured-log tests share
  fixtures) *(pending)*

## Pending

- **`sev1_guard.py` pytest plugin** — trivial; part of Phase 0
- **`tests/data/` seed data** — created alongside first domain code
- **First hypothesis strategies** — Portfolio, Money, Price, TradeSequence
  — written alongside the corresponding value objects
- **Weekly mutation-testing GH Actions workflow** — Phase 1 activity
