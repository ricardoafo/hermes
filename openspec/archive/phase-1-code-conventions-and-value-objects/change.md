# Change: phase-1-code-conventions-and-value-objects

**SDD change**: `phase-1-code-conventions-and-value-objects`
**Date**: 2026-08-23
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#106 primary, also closes #98)

Mirror of engram topic key `sdd/phase-1-code-conventions-and-value-objects/change` (see [ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention) dual-source rule).

## Summary

Phase 1 Wave 1 foundation. Ships `docs/guides/code-conventions.md` (14 sections covering design patterns, error handling, immutability, factories, async, performance, polyglot posture, testing, time handling, event schema versioning, feature flags, engine pause/circuit breaker, schema migrations, public/internal API + LLM safety, report generation, CLI UX, config reload) alongside 6 domain value objects (Money, Price, Weight, Pair, Quantity, LotId) that demonstrate every convention. Establishes patterns for ALL of Phase 1-3, not just Phase 1 — chosen scope after cross-phase audit to avoid retrofit debt.

## Scope

### In

- **`docs/guides/code-conventions.md`** — 14 sections, evidence-backed (cites ADRs + external research; see `phase-1-code-conventions-research-2026-08-23` engram entry)
- **`src/hermes/domain/errors.py`** — `HermesError` root + `DomainError` / `InfraError` subtrees
- **`src/hermes/infrastructure/time.py`** — UTC-internally / Europe/Madrid-at-edges helpers (now_utc, to_madrid, from_madrid)
- **`src/hermes/domain/events/base.py`** — event base class with `__event_schema_version__` field for future upcasting
- **`src/hermes/domain/value_objects/`** — 6 value objects (Money, Price, Weight, Pair, Quantity, LotId) as frozen dataclasses with `from_*` factories and `Result`-based validation
- **`tests/strategies/`** — hypothesis strategy library (money.py, price.py, weight.py, pair.py, quantity.py, lot_id.py)
- **`tests/unit/domain/value_objects/`** — property tests per ADR-007
- **`tests/unit/domain/test_errors.py`** — hierarchy shape test
- **`tests/unit/infrastructure/test_time.py`** — UTC/Madrid roundtrip
- **`pyproject.toml`** — add `returns>=0.24,<1` (Result monad, domain layer) + `anyio>=4,<5` (structured concurrency, adapters)
- **`known_licenses.yaml`** — check `returns` + `anyio` license, no exception expected (both MIT/BSD)

### Out

- **Non-value-object domain code** (Portfolio, Position, Lot) — those are #24 or later
- **Domain events (concrete)** — TradeExecuted, RebalanceTriggered etc. → #99. This change ships the BASE class + versioning contract only.
- **Adapters** — Slack/Telegram/Binance implementations → #101, #102, and Phase 1 later waves
- **Config loading** — #103 (separate Wave 1 issue)
- **Polars / Numba / PyO3 additions** — the doc DOCUMENTS the polyglot posture + triggers; actual polyglot code lands when a profiled hot spot appears

## Decisions

### D-1: Hybrid error handling (Result in domain, exceptions in adapters)

- **Decision**: Domain functions return `Result[T, DomainError]` via `dry-python/returns`. Adapters raise typed exceptions from a sealed `HermesError` hierarchy. Application layer converts adapter exceptions → domain `Result`.
- **Alternatives**: (a) pure Result everywhere (verbose noise in adapters); (b) pure exceptions everywhere (silent-swallow risk on domain paths for real money); (c) NamedTuple `(value, error)` returns (no mypy enforcement, roll-your-own).
- **Rationale**: Domain errors are EXPECTED OUTCOMES (currency mismatch, invalid lot). Type signatures make them visible to callers — critical for a real-money system. I/O errors ARE exceptional; typed exception hierarchies follow the convention of established exchange client libraries. Splitting by layer takes the best of both. Evidence: `dry-python/returns` used in production banking; Sobolev's "exceptions considered antipattern" is the reference argument. See engram `phase-1-code-conventions-research-2026-08-23` § Topic 1.
- **Cost of reversal**: MEDIUM — swapping to pure Result would touch every adapter; swapping to pure exceptions would touch every domain function's callers. Making this call upfront saves both.

### D-2: Frozen dataclass everywhere in domain (no pydantic)

- **Decision**: All domain value objects and event dataclasses use `@dataclass(frozen=True, slots=True)`. Pydantic is ALLOWED at I/O boundary ONLY (config loading via `pydantic-settings`, JSON serialization at adapter edge).
- **Alternatives**: pydantic `BaseModel` uniformly; NamedTuple; attrs.
- **Rationale**: Hexagonal domain layer must NOT depend on I/O framework. `frozen=True` gives immutability; `slots=True` reduces memory + prevents attribute drift. stdlib means zero external dep for the layer that must be most stable.
- **Cost of reversal**: LOW — dataclass → pydantic BaseModel is mechanical if ever needed.

### D-3: AnyIO on asyncio (structured concurrency at I/O boundary)

- **Decision**: Adapters that do I/O use `anyio` (structured concurrency built on asyncio). Domain is sync. Bridge via `anyio.streams.memory.MemoryObjectStream` (bounded queue) or `run_sync_in_worker_thread`.
- **Alternatives**: raw asyncio (no TaskGroup native cancellation until 3.11); Trio (unfamiliar ecosystem, harder to hire).
- **Rationale**: AnyIO gives Trio-style structured concurrency ON asyncio, with `TaskGroup` (exception propagation + cancellation are automatic). Powers Prefect. `python-binance` async ≈ 80-90% faster than threaded. Concrete benefit: exception in one WS task doesn't kill the loop.
- **Cost of reversal**: LOW — AnyIO wraps asyncio; downgrading to raw asyncio is a mechanical rewrite of `TaskGroup` → `gather`.

### D-4: Time handling — UTC internally, Europe/Madrid at edges

- **Decision**: Every timestamp inside hermes is UTC (`datetime` with `tzinfo=UTC`). Convert to Europe/Madrid ONLY at output edges (CLI/TUI display, tax reports). `src/hermes/infrastructure/time.py` exposes `now_utc()`, `to_madrid()`, `from_madrid()`.
- **Alternatives**: naive datetimes (silent bugs at DST boundaries); Madrid internally (breaks any exchange interop).
- **Rationale**: All exchange data is UTC. Spanish tax reports need Madrid. Set convention now → every Money/event/log carries UTC → convert once per output.
- **Cost of reversal**: HIGH — retrofit means touching every timestamp field in every event + every value object.

### D-5: Event schema versioning via `__event_schema_version__`

- **Decision**: Every domain event dataclass has an `__event_schema_version__: int = 1` class attribute. Ledger serializer writes it; deserializer reads it + dispatches through an upcaster chain (`v1 → v2 → v3`). Schema changes are NEVER breaking — always additive with default values or upcaster.
- **Alternatives**: no versioning (breaking changes require full reingest); pydantic model versioning; separate event-schema files.
- **Rationale**: SQLite events table will hold 5+ years of data. Once v1 events are written, v2 events must deserialize them. Cheap to establish convention now; expensive after 10k rows.
- **Cost of reversal**: MEDIUM — adding versioning to existing events requires writing an upcaster for every legacy event type.

### D-6: Feature flags via `TradingMode` enum + strategy toggles in config

- **Decision**: `TradingMode = Enum("paper", "shadow", "live")` guarded ONCE at use-case boundary (not sprinkled in domain). Per-strategy enable via `config/strategies.yaml`. Dry-run is the default (I-03 invariant).
- **Alternatives**: LaunchDarkly-style flag service (overkill for solo dev); scattered `if config.mode == "live"` (tech debt).
- **Rationale**: Single enum with single guard point = one place to audit. Per-strategy toggles let us enable Rebalancer + disable Donchian without a code change.
- **Cost of reversal**: LOW — enum → boolean is trivial; the discipline of "guard at boundary" is what saves later cost.

### D-7: Engine pause as first-class state (`PausableUseCase` mixin)

- **Decision**: Use cases that touch trading inherit from `PausableUseCase` mixin. `pause(reason)` and `resume()` are typed methods. `Paused` state emits `EnginePaused(reason)` domain event. Rebalance/Signal use cases check `_paused` state at entry.
- **Alternatives**: global `PAUSED` flag (untraceable); per-service systemd unit pause (kills observability during pause window).
- **Rationale**: ADR-010 CVE playbook says `hermes engine pause`. Making this a first-class use-case concept means every trading path automatically inherits pause capability. Adding it retroactively means editing every use case.
- **Cost of reversal**: LOW — mixin removal is mechanical.

### D-8: Schema migrations — forward-only, numbered, paused-during-migration

- **Decision**: `src/hermes/infrastructure/migrations/` holds numbered migration files (`0001_initial.py`, `0002_add_fees.py`, ...). `schema_version` table tracks applied version. Migrations run at startup if version mismatch. Zero live migrations — engine paused during migration.
- **Alternatives**: Alembic (heavy, ORM-coupled); SQLAlchemy migrations (dep we don't need); raw SQL files (no rollback safety).
- **Rationale**: Simple forward-only migrations are enough for a solo-dev SQLite setup. Paused-during-migration removes the entire live-migration-safety problem. Numbered files sort deterministically. This change ships the CONVENTION; #24 FIFO ledger adds `0001_initial.py`.
- **Cost of reversal**: MEDIUM — swapping to alembic later means porting the schema_version table + rewriting migrations in alembic style.

### D-9: Public API convention — `_prefix` for internal, `__all__` at package boundary

- **Decision**: Names starting with `_` are internal (not for import outside their module). Every package `__init__.py` defines `__all__` listing the intended-public names. Ruff enforces via `F401` (unused imports) + custom rule for `_prefix` violations at package boundary.
- **Alternatives**: no convention (drift); `_pkg_prefix` unwieldy; separate `_internal/` sub-package (over-engineered for solo dev).
- **Rationale**: Zero-cost convention now saves grepping "what's internal" once callers exist. Standard Python idiom.
- **Cost of reversal**: LOW.

### D-10: Performance mindset — profile-before-optimize, measured triggers

- **Decision**: `py-spy` as default profiler (sampling, prod-safe, Rust-written, near-zero overhead). `memray` for allocation deep-dives. `Decimal` for anything reconciled to ledger; `float`/numpy for indicators. Polars over pandas when DataFrame ops first land. **Numeric hot-spot triggers** (measured, not vibes) are documented in the conventions doc — see § 6.
- **Alternatives**: cProfile (dev only, overhead too high for prod); pytest-benchmark alone (regression only, not exploration).
- **Rationale**: Correctness first (I-01, I-02); performance is a subordinate constraint. But we WILL profile. Setting the trigger thresholds NOW prevents "should we optimize?" debate later — the answer is "if the number crosses the threshold, yes."
- **Cost of reversal**: LOW — swapping profilers is a dev-machine change.

### D-11: Polyglot posture — Rust YES (when triggered), Go NO for hermes

- **Decision**: PyO3 Rust extension for a proven hot spot. Numba as intermediate step. Go microservice: NO for solo dev + single VPS. Cython/mypyc: legacy, new work → PyO3.
- **Alternatives**: pure Python forever (accept the ceiling); polyglot from day one (premature).
- **Rationale**: Hexagonal architecture makes swapping a hot-path adapter for a Rust extension an ADAPTER change, not a rewrite. Go microservice adds gRPC + schema drift + deploy surface for zero real benefit at solo scale.
- **Cost of reversal**: LOW (never adding Rust is easy); adding then removing is medium (rewrite in Python).

### D-12: Testing patterns — property-based first, mutation-tested on money paths

- **Decision**: Every value object gets hypothesis property tests (composed via `@st.composite` + registered via `st.register_type_strategy`). Mutation testing (mutmut) targets `hermes.domain.**` — 100% kill on money paths. Test-double taxonomy documented (fake ledger, stub clock, mock notifier, spy events, contract test via respx).
- **Alternatives**: only example-based tests (misses edge cases hypothesis finds); mutation testing on everything (slow, low ROI on adapters).
- **Rationale**: Money math correctness is the highest-stakes property in the codebase. Property tests + mutation tests give proof, not vibes. Adapter contract tests via respx snapshot the parsed domain object (not raw JSON) — catches parser regressions cleanly.
- **Cost of reversal**: LOW — removing hypothesis is easy; removing mutation testing is easy.

### D-13: Report generation — pattern for CSV/PDF/XML (established, not implemented)

- **Decision**: Convention doc includes a pattern for report generators — dataclass → serializer function → snapshot test with syrupy. Applies uniformly to weekly rebalance reports, monthly reconciliation, Modelo 100/721 tax filings. This change DOCUMENTS the pattern; concrete reports land in Phase 2+.
- **Alternatives**: template engines (Jinja2 for HTML/PDF); direct string manipulation (fragile).
- **Rationale**: Weekly + monthly + yearly reports all fit the same shape. Establishing the pattern once means Phase 2+ implementations don't reinvent.
- **Cost of reversal**: LOW.

### D-14: LLM adapter safety — read-only enforcement + prompt-injection defense (pattern only)

- **Decision**: LLM adapters (Phase 3 #34) get a typed `ReadOnlyLedgerView` port that structurally cannot write. Prompts are sanitized at MCP boundary. This change DOCUMENTS the pattern; implementation in Phase 3.
- **Alternatives**: trust the LLM to be read-only (violates I-02); pass raw prompts without sanitization (prompt-injection risk).
- **Rationale**: ADR-001 mandates LLM never generates trade signals. Making the boundary structurally enforce this (via port typing) is stronger than documentation alone.
- **Cost of reversal**: LOW — pattern change is easy before real LLM code exists.

## Requirements

### R-1: Code conventions guide exists + lints clean

- **A-1.1**: `docs/guides/code-conventions.md` exists with 14 numbered sections matching the table below
- **A-1.2**: `markdownlint-cli2` reports 0 issues on the file
- **A-1.3**: Guides index auto-regenerates to include it
- **A-1.4**: Every section cites at least one of: ADR reference, engram research reference, external authoritative source (with URL)

Sections required:

| § | Title |
| --- | --- |
| 1 | Design patterns catalog |
| 2 | Error handling — hybrid Result/exceptions |
| 3 | Immutability — frozen dataclass everywhere in domain |
| 4 | Factory methods vs constructors |
| 5 | Async policy — AnyIO on asyncio |
| 6 | Performance mindset — measured triggers |
| 7 | Polyglot posture — Rust YES (when triggered), Go NO |
| 8 | Testing patterns — property + mutation + contract |
| 9 | Time handling — UTC internally, Madrid at edges |
| 10 | Event schema versioning + upcaster chain |
| 11 | Feature flags — TradingMode enum |
| 12 | Engine pause — PausableUseCase mixin |
| 13 | Schema migrations — forward-only, numbered |
| 14 | Public/internal API + LLM safety + reports + CLI UX + config reload |

### R-2: Error hierarchy exists + mypy strict

- **A-2.1**: `src/hermes/domain/errors.py` exports `HermesError`, `DomainError`, `InfraError`, and concrete subclasses (`CurrencyMismatch`, `InvalidLot`, `NetworkError`, `ExchangeError`, `LedgerError`)
- **A-2.2**: All classes pass `mypy --strict`
- **A-2.3**: Test asserts hierarchy shape: `CurrencyMismatch` is a `DomainError` is a `HermesError` (isinstance checks)

### R-3: Time module — UTC/Madrid convention

- **A-3.1**: `src/hermes/infrastructure/time.py` exports `now_utc()`, `to_madrid(dt)`, `from_madrid(dt)`
- **A-3.2**: `now_utc()` returns timezone-aware datetime with `tzinfo=UTC`
- **A-3.3**: Round-trip test: `from_madrid(to_madrid(now_utc())) == now_utc()` (modulo precision)
- **A-3.4**: DST boundary test — Spain switches CET (UTC+1) / CEST (UTC+2) in late March + late October

### R-4: Event base class with schema versioning

- **A-4.1**: `src/hermes/domain/events/base.py` exports `DomainEvent` base class + `SchemaVersion` type alias
- **A-4.2**: Every subclass MUST override `__event_schema_version__: int` (starts at 1)
- **A-4.3**: `DomainEvent` is frozen dataclass with `occurred_at: datetime` (UTC) + `correlation_id: str` (ULID)
- **A-4.4**: Test asserts subclass without version override raises at class-definition time

### R-5: Six value objects — Money, Price, Weight, Pair, Quantity, LotId

- **A-5.1**: All 6 files exist in `src/hermes/domain/value_objects/` + `__init__.py` re-exports
- **A-5.2**: All are `@dataclass(frozen=True, slots=True)`
- **A-5.3**: All have `from_str(s)` classmethod returning `Result[Self, DomainError]`
- **A-5.4**: `Money` supports `+`, `-`, `*` (with Decimal/int), `/` (with Decimal) — currency mismatch on binary ops returns `Result[Money, CurrencyMismatch]` (does NOT raise per D-1 hybrid rule for domain)
- **A-5.5**: `mypy --strict src/hermes/domain/value_objects/` reports 0 issues
- **A-5.6**: All 6 have hypothesis strategies in `tests/strategies/` + property tests in `tests/unit/domain/value_objects/`
- **A-5.7**: Coverage ≥ 95% on `hermes.domain.value_objects.*` (ADR-007 threshold)

### R-6: pyproject.toml updated with new runtime deps

- **A-6.1**: `returns>=0.24,<1` added to `dependencies` list
- **A-6.2**: `anyio>=4,<5` added to `dependencies` list
- **A-6.3**: `uv.lock` regenerated + committed
- **A-6.4**: `pip-audit --skip-editable` reports 0 vulnerabilities
- **A-6.5**: License check passes (both `returns` and `anyio` are BSD/MIT — no exception needed)

### R-7: Pre-flight green + all CI checks pass

- **A-7.1**: `bash scripts/dev/pr-preflight.sh --render-body phase-1-code-conventions-and-value-objects` — all 10 checks pass locally
- **A-7.2**: `unit tests + coverage` CI job green — bootstrap flag on coverage-check DOES NOT need to come off (this change adds real code but other packages remain empty; ratchet flag off in a follow-up when domain layer is more complete)
- **A-7.3**: `import-linter` still reports 3 contracts kept (new files under `domain/`, `infrastructure/` don't violate hexagonal boundaries)

## Design

Full content of `docs/guides/code-conventions.md` (~800 lines) is the primary artifact. Section outline shown in R-1; body content is the substance of the change. Key structural elements:

- **Each section**: opens with the convention statement, provides rationale (evidence-cited), shows a hermes example, notes the enforcement mechanism (mypy rule, ruff config, import-linter contract, pre-commit hook, or "discipline"), and lists what NOT to do
- **Cross-references**: every section links back to the ADRs it derives from (mostly ADR-003, ADR-007, ADR-008) and to the engram research entry (`phase-1-code-conventions-research-2026-08-23`)

Concrete implementation follows the doc's own conventions.

## Test plan

Done when:

1. All 7 requirements above pass (A-1.1 through A-7.3)
2. `bash scripts/dev/pr-preflight.sh --render-body phase-1-code-conventions-and-value-objects` → 10 checks pass
3. Every required CI check green on the PR (16 checks after this ships, including the new `licenses` and `reproducible`)
4. Manual sanity: import a value object, do arithmetic, verify Result returns work end-to-end

## Risks

- **`returns` is a new dep** — production-grade but adds ~2 MB. Zero known CVEs.
- **`anyio` unused in this change** (adapters land in later Phase 1 waves) — adding it now is speculative but matches the D-3 decision. Rationale: single-PR dep addition avoids "oh we need anyio" churn later.
- **`docs/guides/code-conventions.md` is long** (~800 lines). Reviewer fatigue risk. Mitigation: each section is self-contained; can be reviewed sequentially.
- **Coverage bootstrap flag stays on** — this change adds coverage on `value_objects/` but other packages (`application/`, `adapters/*/`, `entrypoints/`) remain empty. Ratcheting off `--bootstrap` here would fail those. Deferred until domain layer more complete (Wave 2-3).
- **Value objects use `Result` — noise cost** — every domain caller now `.bind()` / `.map()` on Money operations. This IS the whole point (mypy catches unhandled outcomes) but adds visible verbosity vs raw exceptions.

## Dependencies on other changes

- **No blockers** — this change is self-contained; establishes the pattern for everything downstream
- **Blocks**: #99 (domain events — needs `DomainEvent` base from R-4), #24 (FIFO ledger — needs value objects + migrations convention from D-8), #103 (config loading — uses `TradingMode` enum from D-6), everything else in Phase 1
