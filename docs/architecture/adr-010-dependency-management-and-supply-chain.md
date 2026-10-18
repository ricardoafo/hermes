# ADR-010: Dependency management + supply chain — uv-managed lockfile, Renovate for updates, safety + pip-audit for CVE scanning, documented response process

**Date**: 2026-08-20
**Status**: Accepted
**Accepted**: 2026-08-21
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: —
**Superseded in part by**: decision-journal entries [Dependabot over Renovate](../decisions/2026-08-21-dependabot-over-renovate.md) (Renovate), [Drop `safety`](../decisions/2026-08-22-drop-safety.md) (§3, `safety` + `pip-audit` becomes `pip-audit`) and [VPS installs from hashed requirements](../decisions/2026-10-02-vps-install-from-hashed-requirements.md) (§1, VPS install step)

## Context

Hermes handles credentials that unlock real capital and personal data that is
subject to LOPDGDD. A compromise via a dependency — a package that ships a
credential-exfiltration payload, a transitive package that quietly gains
maintainer control, a lockfile that drifts from what CI actually resolves —
is the class of incident that ends projects. This is not hypothetical: 2024's
`ua-parser-js` incident, 2023's `PyTorch`-dependency-confusion event, and the
ongoing crypto-address-swap malware campaigns targeting web3 libraries all
demonstrate that supply-chain attacks are the default modern threat model for
any Python project that touches money.

The counterweight is that supply-chain hygiene tooling is easy to overspec.
Full-cycle SBOM signing, in-toto attestations for every commit, and Sigstore
verification of every publish are appropriate at large-org scale; at solo-
operator scale, they add process without adding safety over a smaller,
disciplined kit.

The question is: **which pieces of the supply-chain toolkit earn their
maintenance cost at solo scale, and what is the concrete response process
when a CVE lands on a package we depend on?**

## Decision

Hermes uses a **four-piece dependency stack**, with a documented weekly
review cadence and a documented CVE-response process.

### 1. Package manager: `uv`

- **`uv` is the canonical package manager.** `pip` is available for
  interop but is not the default path.
- `pyproject.toml` declares direct dependencies with **compatible-release
  version specifiers** (e.g. `httpx>=0.28,<0.29`, `pydantic>=2,<3`).
- `uv.lock` is the resolved, hash-pinned lockfile. **Committed** to git.
  Deterministic across dev machines, CI, and the VPS.
- `uv sync --frozen` is the install command in CI and on the VPS.
  `--frozen` refuses to update the lock; a lock update requires a
  separate PR.
- **No `requirements.txt` in production.** If interop is needed,
  `uv export` produces one on demand.

### 2. Update automation: Renovate (self-hosted config)

- **Renovate over Dependabot** for the configurability (grouped
  updates, custom schedules, security-only lanes, per-manager rules).
  Dependabot is functional but less flexible; the small config
  investment pays off in signal-to-noise.
- Config lives at `renovate.json` at repo root.
- **Groups**:
  - `security` — CVE-triggered updates; opens PR immediately, no
    schedule.
  - `patch` — patch-level bumps; grouped, opened weekly (Sunday).
  - `minor` — minor bumps; grouped, opened weekly.
  - `major` — major bumps; each opens its own PR (needs consideration).
  - `dev-only` — dev dependencies (pytest plugins, ruff, etc.);
    grouped, opened weekly, lower priority.
- **Auto-merge**: only for `security` + `patch` groups AFTER CI
  passes (all tests + linters green). Minor and major always human-
  reviewed.
- **PR labels** — every Renovate PR is labeled `dependencies` +
  its group. Facilitates weekly review.

### 3. CVE scanning: `safety` + `pip-audit`

- **Both tools** run in CI on every PR + weekly scheduled scan.
  They query different vulnerability databases (safety uses
  PyUp; pip-audit uses OSV + PyPI advisories). Overlap is good;
  false negatives on one may be caught by the other.
- CI fails on any advisory of severity ≥ MEDIUM on a production
  dependency. Dev-only advisories log a warning but do not fail.
- **Exceptions require an inline `known_advisories.yaml` entry**
  with: advisory ID, package + version, justification (why this
  advisory does not apply to our use case), review-by date. Every
  quarter, exceptions are re-audited.

### 4. Supply-chain integrity: hash-pinning + published-hash verification

- `uv.lock` includes SHA256 hashes for every wheel and sdist.
  `uv sync` verifies hashes on install.
- **Trusted-publishers** (PyPI's OIDC-based publish flow) is preferred
  when adding a new dependency — packages published via trusted-
  publisher have provenance metadata the CI can verify. Not enforced
  as a hard gate today (too many important packages haven't adopted
  it yet); tracked as a preference.
- **SBOM generation** — `cyclonedx-python-lib` produces a
  CycloneDX-format SBOM on each release. Committed under
  `docs/sbom/vN.N.N.json`. Not published externally; retained for
  audit + post-incident traceability.

### CVE-response process (formal)

When a CVE lands on a package hermes uses, the response is:

1. **Detect** — one of: Renovate opens a `security` PR, or the
   weekly `safety` + `pip-audit` scan flags an advisory, or an
   external notification (mailing list, Twitter, GitHub advisory
   subscription).
2. **Triage** — within 24 hours, classify per severity:
   - **CRITICAL / HIGH** — the running engine is at risk right now
     if the vulnerable code path is exercised. Pause writes,
     patch, redeploy, resume. See "Immediate patch" playbook below.
   - **MEDIUM** — patch within 72 hours in the normal PR flow.
     No pause needed.
   - **LOW** — patch in the next weekly Renovate cycle.
3. **Assess exploitability** — the CVE description often overstates
   applicability. Confirm the vulnerable code path is one hermes
   actually exercises. If not, downgrade the effective severity;
   still patch to maintain hygiene but do not treat as CRITICAL.
4. **Patch** — merge the update PR. If no upstream fix exists, pin
   to a known-safe version (possibly older); if no known-safe
   version, temporary vendor-and-patch is acceptable with an
   opened upstream issue.
5. **Verify** — post-patch, re-run `safety` + `pip-audit` +
   full test suite. Zero advisories on production packages.
6. **Communicate** — write a decisions-journal entry (per
   ADR-009) with the CVE ID, the fix, the assessed exploitability,
   and any follow-up.

#### Immediate patch playbook (CRITICAL / HIGH)

- Pause the rebalancer and swing daemons via `hermes engine pause --reason "CVE-YYYY-NNNNN"`.
- Take a manual backup (`hermes backup --snapshot`).
- Merge the security PR after CI green.
- Redeploy to VPS via the release workflow.
- Resume engines via `hermes engine resume`.
- Publish a decisions-journal entry within 24 hours of resume.

### What we do NOT do (yet)

- **Sigstore / in-toto attestations for our own releases** — no
  external consumers, so provenance for our releases has no
  downstream verifier. If we ever ship a release to an external
  system, this changes.
- **Vendor / mirror upstream packages** — solo scale + PyPI
  reliability makes this operational overhead we do not need.
  If PyPI has a sustained outage, `uv.lock`'s hash-pinning +
  local wheel cache mean we can continue without new installs
  for weeks.
- **Full-cycle attestation of build artifacts** — no build
  artifacts leave our infrastructure. `pyproject.toml` +
  `uv.lock` is the release surface.
- **Third-party dependency-review services** (Snyk, WhiteSource,
  Sonatype Nexus) — cost + friction exceed the safety improvement
  over `safety` + `pip-audit` at our scale.

### Update cadence summary

| Update type      | Cadence               | Auto-merge?      | Human review?           |
| ---------------- | --------------------- | ---------------- | ----------------------- |
| Security patches | Immediate on advisory | Yes, if CI green | Post-merge notification |
| Patch updates    | Weekly (Sunday PR)    | Yes, if CI green | Weekly summary review   |
| Minor updates    | Weekly (Sunday PR)    | No               | Reviewed in weekly loop |
| Major updates    | Per-PR, no schedule   | No               | ADR-scale consideration |
| Dev-only updates | Weekly (Sunday PR)    | Yes, if CI green | Weekly summary review   |

### Concrete direct-dependency set (Phase 0-1 forecast)

Not exhaustive; sets the vocabulary for what "our dependencies" means.

| Package                | Purpose                            | Category   |
| ---------------------- | ---------------------------------- | ---------- |
| `httpx`                | HTTP client for adapters           | Production |
| `pydantic`             | Value objects + config models      | Production |
| `pydantic-settings`    | Layered config loading             | Production |
| `structlog`            | Structured logging                 | Production |
| `typer`                | CLI framework                      | Production |
| `rich`                 | CLI rendering                      | Production |
| `textual`              | TUI framework                      | Production |
| `apscheduler`          | Non-systemd scheduling (fallback)  | Production |
| `binance-connector`    | Binance API client (or wrap httpx) | Production |
| `python-telegram-bot`  | Telegram delivery                  | Production |
| `ulid-py`              | Correlation IDs                    | Production |
| `pytest`               | Test runner                        | Dev-only   |
| `hypothesis`           | Property-based tests               | Dev-only   |
| `syrupy`               | Snapshot tests                     | Dev-only   |
| `mutmut`               | Mutation tests                     | Dev-only   |
| `respx`                | HTTP mocking                       | Dev-only   |
| `freezegun`            | Time freezing                      | Dev-only   |
| `coverage`             | Coverage measurement               | Dev-only   |
| `ruff`                 | Linter + formatter                 | Dev-only   |
| `mypy`                 | Type checker                       | Dev-only   |
| `import-linter`        | Import direction enforcement       | Dev-only   |
| `safety`               | CVE scanning                       | Dev-only   |
| `pip-audit`            | CVE scanning                       | Dev-only   |
| `cyclonedx-python-lib` | SBOM generation                    | Dev-only   |
| `pre-commit`           | Hook orchestration                 | Dev-only   |

## Why

### Why uv, not pip / pip-tools / poetry / pdm

- **`uv` is 10-100× faster** than pip for resolution and installs.
  At Phase 0-1 dependency count (~25 direct + ~150 transitive), the
  time difference is a couple of seconds; at reasonable growth over
  the project's life, it matters.
- **Rust implementation, zero Python runtime dependency for the tool
  itself.** The tool that manages Python packages should not require
  Python to run. Bootstrapping is trivial.
- **Lockfile is deterministic and hash-verified by default.** No
  toggle-to-enable; no separate `pip-compile` step.
- **`uv sync --frozen`** is the correct semantics — install exactly
  the lockfile, refuse to update. Poetry's `install` has drifted
  around this contract over versions.
- **Actively maintained by Astral,** the same team that ships `ruff`.
  A single vendor for Python-tooling ergonomics is a plus, not a
  concentration risk at solo scale.

pip is available for interop; if uv breaks in a way that stops the
project, pip still works. `uv.lock` is convertible to `requirements.txt`
via `uv export`.

### Why Renovate over Dependabot

Dependabot works. Renovate is more configurable:

- **Grouping** — Renovate groups patch updates across packages into
  one PR; Dependabot opens one PR per package. At a plausible
  weekly rate (5-15 patch bumps), one PR is manageable, 15 is not.
- **Custom schedules per group** — Renovate can defer major bumps
  entirely, expedite security-only; Dependabot's schedule model
  is per-manager, not per-group.
- **Auto-merge conditions** — Renovate can require specific CI checks
  before auto-merging; Dependabot's auto-merge is coarser.
- **Renovate self-hosted or GitHub-hosted app** — both options
  available; GitHub-hosted for solo, self-hosted for team.

The cost is: Renovate config takes ~30 minutes to write and reason
about. Dependabot is 3 lines. The 30 minutes is once, the payoff is
weekly.

### Why safety AND pip-audit (both)

- **Different vulnerability databases.** safety pulls from PyUp
  (subscription-model with a free tier); pip-audit pulls from OSV
  (Google) + PyPI advisories. Coverage overlaps but is not
  identical.
- **Redundancy is cheap.** Both run in seconds; the CI budget cost is
  negligible; false negatives on one may be caught by the other.
- **Divergence is signal.** When only one flags an advisory, the
  investigation reveals whether it's a database completeness
  issue or a genuine disagreement about applicability.

### Why hash-pinning is worth the CI cost

Hash-pinning defeats the specific attack of "attacker publishes a
different wheel with the same version". Without hash-pinning, a
compromise of PyPI (or a package's maintainer account) can flow
into our next `uv sync`. With hash-pinning, the compromise
manifests as a hash mismatch that fails install loudly.

The cost: `uv.lock` is larger and diffs on every real update. Both
are acceptable in exchange for the attack the hash defeats.

### Why SBOM without publishing it externally

CycloneDX SBOM generation is ~5 seconds per release. Keeping it in
`docs/sbom/vN.N.N.json` costs nothing beyond disk. When (not if) a
CVE lands and we need to answer "was version 1.3.7 vulnerable?",
the SBOM is the answer without archaeology.

Publishing externally would mean: hosting a public feed, maintaining
signing infrastructure, and (per current practice) providing
attestations. All are premature at solo scale; the internal SBOM is
the payoff without the ceremony.

### Why we auto-merge patches but not minors

- **Patch bumps** — by SemVer contract, no new features, no
  breaking changes. CI green means the API surface still works.
  Auto-merging patches after CI is safe under the SemVer bet.
- **Minor bumps** — new features, backward-compatible. New
  features may be exercised inadvertently (a config change, a
  new default). Human review is warranted.
- **Major bumps** — breaking changes by contract; always ADR- or
  decisions-journal-scale consideration.

The SemVer bet is imperfect (upstream packages sometimes ship
breaking changes as patches; PyPI has no gatekeeper). The
mitigation is: CI must be comprehensive enough that a broken
patch update fails a test.

### Why the CVE response process is written down

Muscle memory in the middle of an incident is unreliable. A
written playbook cuts response time from "figure it out" to
"execute the steps". Cost is 30 minutes of writing; benefit is
hours saved in the median incident.

Especially for CRITICAL / HIGH, the sequence (pause → backup →
patch → redeploy → resume → journal) is not obvious under stress.
The runbook (per ADR-009) is where this playbook expands into
concrete commands; this ADR is where the process is committed.

## Consequences

### Positive

- **Reproducibility is deterministic.** `uv sync --frozen` on any
  machine produces the same installed set as on any other.
- **CVE response has a written contract.** Detection through post-
  incident writeup is 6 steps; the ambiguity is minimal.
- **Renovate takes weekly dep hygiene off the operator's plate.**
  Security patches merge themselves (after CI); minor / major
  bumps land as a weekly PR queue.
- **SBOM answers "were we vulnerable" queries without
  archaeology.** Every release has an inspectable dependency
  manifest with hashes.
- **Auto-merge policy prevents scope-creep from mundane bumps.**
  Every merged patch is CI-green; every merged minor was human-
  reviewed.

### Negative

- **Renovate config is a maintained artifact.** `renovate.json`
  needs updating as new grouping rules or manager types are needed.
  Not free.
- **`safety` requires the free-tier API token to be provisioned in
  CI.** Small setup task; occasional rate-limit surprises.
- **Rollback-on-bad-update requires a revert PR, not a config
  toggle.** If a patch merged auto and broke prod (missed by CI),
  the fix is a revert. Acceptable at solo scale; would be too
  slow at high change velocity.
- **SBOM in-repo has minor privacy exposure.** The dependency set
  is visible in the public repo (if the project is public);
  attackers know which packages are targets. Mitigation: the
  info is already inferable from `pyproject.toml`; SBOM adds
  little.

### Neutral

- **The `known_advisories.yaml` file exists to be edited under
  duress.** When a CVE lands that we cannot immediately patch
  (upstream fix not out yet), a temporary exception with a
  review-by date is the pressure valve.
- **CI runs `safety` + `pip-audit` on every PR AND weekly on the
  main branch.** The weekly scan catches advisories that landed
  after the last PR merged.
- **Trusted-publishers preference is aspirational.** Not enforced
  today; tracked so we can enforce when the ecosystem catches up.

## What would change this decision

- **Team growth.** Renovate config would gain reviewer allowlists;
  auto-merge policy tightens (patches also human-reviewed);
  weekly review becomes a rotating role.
- **External consumers.** Sigstore signing + attestations become
  required for provenance downstream.
- **Regulated deployment target** (banking or health) — SBOM
  publication, in-toto attestations, and third-party audit tooling
  become mandatory rather than optional.
- **`uv` sunsets or fragments.** Migration to `pip-tools` or
  another manager. `uv.lock` is not portable, but `pyproject.toml`
  is; the lock can be regenerated.
- **A dependency we rely on is compromised at scale.** Case-by-case;
  triggers vendor-and-patch on the compromised path, plus a
  broader audit of trust posture for adjacent packages.

## Cross-references

- [ADR-006](./adr-006-versioning-strategy.md) — release process
  invokes the SBOM step; version bumps categorize dependency
  updates in the CHANGELOG
- [ADR-007](./adr-007-testing-strategy.md) — CI-green is the
  auto-merge gate; test suite is what protects against bad
  auto-merged patches
- [ADR-008](./adr-008-observability-strategy.md) — CVE-triggered
  engine pause fires via the Telegram-alert path
- [ADR-009](./adr-009-documentation-strategy.md) — CVE-response
  writeups live in `docs/decisions/`; playbooks live in
  `docs/runbook/`
- [ADR-003 section 09](./adr-003-solution-architecture/09-deployment.md)
  — CI runners (self-hosted on laptop for tests-with-secrets;
  GH-hosted for public-safe checks)

## Pending

- **`renovate.json`** — created in Phase 0
- **CI workflow additions** — `safety` + `pip-audit` steps
  landed in Phase 0
- **`known_advisories.yaml`** — file scaffold + example entry
  committed in Phase 0; kept empty in normal operation
- **First SBOM** — generated at v0.1.0 release
- **CVE-response runbook expansion** — `docs/runbook/cve-response.md`
  in Phase 1 with concrete command snippets
- **Renovate config review** — 30-day check-in after Phase 0 to
  tune group sizes + noise-vs-signal
