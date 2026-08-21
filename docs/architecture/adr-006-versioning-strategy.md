# ADR-006: Versioning strategy — SemVer for the release surface, numbered SQL migrations for the ledger, versioned config schemas for parameters

**Date**: 2026-08-20
**Status**: Accepted
**Accepted**: 2026-08-21
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: —

## Context

Hermes is a solo-operator system. It has no public HTTP API, no consumer
library, no downstream integrators. On paper, "versioning" seems overspec'd:
one operator, one deployment, one machine.

The paper answer is wrong. Three independent surfaces demand disciplined
versioning even at N=1:

1. **The release surface** — every deployment is a change to something that
   moves real money. Rolling back to "yesterday's build that worked" requires
   knowing what yesterday's build was, and what changed between then and now.
   Git SHAs are necessary but not sufficient; a human-readable version is what
   goes in Telegram notifications, dashboard headers, and post-mortems.
2. **The SQLite schema** — the ledger accumulates historical value over years
   (see [ADR-002](./adr-002-tax-posture.md)). Every schema change must be
   applied idempotently on startup, in a defined order, without ambiguity
   about which changes ran and which did not. A backup restored from six
   months ago must reach today's schema by the same numbered path a fresh
   install takes.
3. **The configuration schema** — parameters like rebalance band, target
   weights, fee schedules, tax-guard thresholds live in YAML. When a
   parameter is renamed, removed, or gets a new required field, deployments
   with old config must either reject startup (loud failure) or migrate
   (quiet success) — never load partial config and act on it.

The three surfaces are related but distinct. A rebalance-band tightening
does not require a code release; a code release should not silently accept
malformed config; a migration should not require a config change. Each
surface has its own versioning story.

## Decision

Hermes uses three coordinated versioning schemes, each fit to its surface.

### 1. Release surface: Semantic Versioning (SemVer 2.0.0)

The project has a single `version` field in `pyproject.toml`. It follows
SemVer 2.0.0 with the following mapping:

- **MAJOR** — breaking operational change. Requires the operator to read the
  release notes before deploying. Examples: schema migration that is not
  fully backward-compatible with the previous minor's queries; exchange
  cutover requiring key re-issuance; introduction of a new required
  configuration key without a safe default.
- **MINOR** — new feature, backward-compatible. A new engine mode
  (`hermes rebalance --dry-run`), a new anomaly detector, a new dashboard
  screen. Old configs load; old queries still work.
- **PATCH** — bug fix, backward-compatible. No new features. No new
  configuration required. Safe to deploy without reading release notes
  beyond the changelog.

**Pre-release tags** follow SemVer's pre-release suffix rules:

- `0.x.y` — Phase 0-1 development. Nothing yet reaches production; breakage
  expected. Users of a `0.x` release cannot count on stability.
- `1.0.0-shadow.N` — pre-release cuts during shadow mode (Aug-Nov 2026).
  Shadow mode is production-quality behavior against tiny real-capital
  sums; failures are still expected but the numbers move.
- `1.0.0` — the first release cleared for €1000+ real capital deployment
  (target: end 2026-Q4). From `1.0.0` onward, MAJOR bumps trigger a manual
  cutover checklist.

**Version bump rules**:

- Every PR that changes production behavior updates `CHANGELOG.md` with a
  `[Unreleased]` entry categorized as Added / Changed / Deprecated /
  Removed / Fixed / Security (Keep-a-Changelog convention).
- Release cuts happen on a manual `release` workflow that: bumps the
  `version` field per the categorized changes, moves `[Unreleased]` into
  a versioned section, tags git, and (Phase 2+) publishes to a
  private release feed.
- **No automated version bumping.** Every release is a human decision.

### 2. SQLite ledger: numbered SQL migrations, applied on startup

Schema evolution is captured as **immutable, numbered SQL files** in
`src/hermes/adapters/ledger/migrations/`:

```text
001_initial.sql
002_add_llm_audit_table.sql
003_add_config_history_table.sql
```

Rules:

- **Files are immutable once merged.** A shipped migration is never edited.
  If `001_initial.sql` had a mistake, it is corrected by `002_fix_initial.sql`,
  not by rewriting `001`. This is what makes the migration sequence
  deterministic across every deployment.
- **A `migrations` table tracks applied migrations by filename + sha256.**
  On startup the ledger reads `migrations`, computes the set of unapplied
  files by name, and applies them in numeric order inside a single
  transaction per file. A sha mismatch (someone edited a shipped migration)
  is a fatal startup error.
- **Migrations are SQL, not Python.** SQLite's DDL is expressive enough for
  every schema change we plausibly need; keeping migrations as `.sql`
  files makes them reviewable in plain text and executable independently
  of the Python runtime. Data migrations that require computation live in
  paired `.py` files invoked by a companion SQL file marker; the SQL
  file remains the source of truth for "was this applied".
- **No Alembic.** Alembic is overkill; a ~40-line `apply_migrations()`
  function in `src/hermes/adapters/ledger/migrator.py` handles the entire
  workflow. See [ADR-003 section 03](./adr-003-solution-architecture/03-persistence-schema.md#migrations--plain-sql-numbered-applied-on-startup).
- **Backward-compatibility rule**: an N.x.y minor bump must be able to
  read data written by any N.x.z earlier patch. A MAJOR bump can require
  a schema change that breaks that guarantee; the release notes must call
  it out explicitly.

### 3. Configuration schema: versioned pydantic-settings models

Every YAML config file has a `schema_version: N` top-level field. The
loading code chooses a `pydantic` model by version:

```python
class ExchangeConfigV1(BaseModel):
    schema_version: Literal[1] = 1
    base_url: str
    ws_url: str
    # ...

class ExchangeConfigV2(BaseModel):
    schema_version: Literal[2] = 2
    endpoints: EndpointBlock          # V2 nests URL fields
    # ...

ExchangeConfig = ExchangeConfigV1 | ExchangeConfigV2
```

Rules:

- **Adding an optional field with a safe default** does NOT bump the schema
  version. Old configs continue to load and gain the default silently.
- **Adding a required field** bumps the schema version and requires an
  in-place config migration (either automated, or documented in the
  release notes with a `hermes config migrate` command).
- **Removing or renaming a field** bumps the schema version. The old
  version's model remains readable for one MAJOR release cycle, then may
  be dropped in the following MAJOR.
- **Every config load logs `{"file": "...", "schema_version": N}`** so a
  post-mortem can determine what config was in effect at any past moment
  from journald.
- **The loader never accepts an unknown or newer schema version.** A
  version-newer-than-the-code error is fatal at startup — better than
  running with a config the code cannot honor.

### Cross-scheme coordination

- Release notes reference SQL migration numbers and config schema
  versions when either changes. Example: "v0.3.0 — MINOR. Adds
  migration 004_add_open_positions_atr_column, bumps
  `strategy.yaml` schema_version 1 → 2 (new field
  `donchian.atr_multiplier`, default 2.0)."
- The startup log line captures all three: build version, applied
  migration count, config schema version. Debugging in production
  starts from that log line.
- Semantic version compatibility is asserted from Day 1 by an
  integration test that boots the app against a snapshot of every
  previous minor's schema + config, verifies clean startup, and asserts
  no data loss.

## Why

### Why SemVer for a solo-user system

SemVer's contract is between the version and the operator's *expectations*.
The contract still applies even when the operator and the developer are the
same person, because that person is not the same person across time. A
version bump is a durable message to future-me about whether tomorrow's
deployment is safe to run without reading anything, or requires care.

The alternative — CalVer (YYYY.MM.DD) — makes the "am I looking at a
breaking change" question expensive: every version bump requires reading the
changelog. SemVer's MAJOR / MINOR / PATCH answers that question in the
version string itself.

### Why numbered SQL files, not Alembic

Alembic is the standard Python migration framework. It is excellent for
what it does. It also is:

- A dependency with its own release cadence and occasional breaking changes
- Opinionated about branching / merging migration histories in ways that
  matter for teams but are noise for a solo operator
- Producing migration files (`autogenerate`) that are Python code with
  Alembic-specific idioms — harder to review outside of Alembic's mental
  model than plain SQL
- Requiring more setup than a solo-operator system justifies

A 40-line `apply_migrations()` function is enough. `.sql` files are the
lingua franca of database changes. If we ever outgrow this, migrating from
plain-SQL numbered files to Alembic is straightforward; the reverse is
not.

### Why config schemas need versions when we could just use pydantic

Pydantic validates that a loaded config matches its model. It does not
validate that the config file the *operator* is loading matches the config
schema the *code* expects. If a field is renamed in code without touching
the operator's `config/exchange.yaml`, pydantic can either accept the old
config (silently ignoring the removed field) or reject it. Neither is
right in isolation; the correct answer is version-aware behavior.

An explicit `schema_version: N` in the YAML makes the operator's intent
explicit: "this file was written against schema N; please load me under
that model". The code then knows whether it can handle N, and if not,
what to do (usually: fatal error, prompt for migration).

### Why "no automated version bumping"

Automated version bumping (e.g. semantic-release + conventional-commits
that decides MAJOR / MINOR / PATCH from commit-message prefixes) is
tempting but wrong for hermes' risk profile. A tool that decides a MINOR
bump is safe when it should have been MAJOR is a mechanical wrong answer
to a judgment question. Every release is a decision by the operator; the
tooling shows what changed, the human decides what version that adds up
to.

## Consequences

### Positive

- **Rollback is unambiguous.** The startup log line captures the tuple
  `(version, migration_count, config_schema_version)`. Reverting is
  redeploying an older tag and letting migrations rewind is out of scope
  (migrations do not rewind — see below).
- **Every schema and config change is reviewable in isolation.** SQL
  migrations are text files that read top-to-bottom; pydantic model
  changes are visible in the diff.
- **Post-mortems have a stable version reference.** "The 2027-04-11
  fee-overcount bug shipped in v0.7.3 and was fixed in v0.7.4" is a
  full description without SHA-hunting.
- **Config compatibility is an assertion, not a hope.** The versioned
  loader forbids silent partial-load; a version mismatch is loud.

### Negative

- **No downgrade migrations.** SQL migrations are forward-only. If a
  migration is bad, the fix is another migration, not a rewind. Recovery
  from a genuinely bad migration is: restore from backup + apply the
  corrected migration path forward. This is deliberate — reversible
  migrations double the surface area and rarely get tested.
- **Config-file authoring becomes more work.** Every YAML file gets a
  `schema_version` field. It is small work but is friction.
- **Manual version bump requires discipline.** Nothing enforces "if you
  add a required config field, bump MAJOR". PR review is the enforcement
  mechanism.

### Neutral

- **The `CHANGELOG.md` is human-authored in Keep-a-Changelog format.**
  Six categories (Added / Changed / Deprecated / Removed / Fixed /
  Security) map cleanly onto the SemVer bump decision.
- **`hermes version` (CLI command)** prints the tuple
  `{version, migration_count, config_schema_versions}`. Same source
  of truth as the startup log line.
- **Release notes live in the repo,** not in a wiki. Every version tag
  has a corresponding CHANGELOG section.

## What would change this decision

- **A second operator joins.** SemVer's contract becomes multi-party;
  the discipline is more important, but the scheme itself does not
  change. Config schemas may need per-tenant considerations if the
  operators run different setups.
- **A public API is introduced** (e.g. a webhook consumer). SemVer's
  contract now covers external consumers; the compatibility guarantees
  become stricter for what the API surface accepts and returns.
- **Migration count grows large enough to slow startup** (~100+
  migrations). We would introduce a "compact" migration `0N_baseline.sql`
  that captures the schema state at a released MAJOR and requires that
  the DB have applied a specific prior migration count to skip it.
  Below ~100 migrations this is optimization; above, it starts to matter.
- **The Python packaging ecosystem retires `pyproject.toml`.** Unlikely
  in the ADR's lifetime; if it happens, the `version` field moves.

## Cross-references

- [ADR-003 section 03](./adr-003-solution-architecture/03-persistence-schema.md#migrations--plain-sql-numbered-applied-on-startup)
  — where migrations live in code
- [ADR-003 section 09](./adr-003-solution-architecture/09-deployment.md) —
  release workflow (self-hosted vs GH-hosted runner)
- [ADR-009 — Documentation strategy (CHANGELOG lives in the repo)](./adr-009-documentation-strategy.md)
- [ADR-010 — Dependency management (dependency updates get their own
  version bumps per severity)](./adr-010-dependency-management-and-supply-chain.md)

## Pending

- **`hermes version` CLI command scaffold** — trivial; part of Phase 0
- **Release workflow** — GitHub Actions workflow that runs the manual
  version bump; defined in Phase 0 alongside CI
- **Migration folder + first migration file** — created when the
  ledger module lands (Phase 1)
