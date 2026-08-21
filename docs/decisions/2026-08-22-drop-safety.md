# Drop `safety` from Python dev deps — pip-audit + Dependabot alerts cover it

**Date**: 2026-08-22
**Author**: claude on behalf of Ricardo
**Related**: ADR-010 §3 CVE scanning (partially superseded by this entry), `openspec/changes/phase-0-python-toolchain/`

## Decision

Drop `safety` from `pyproject.toml` dev dependencies and from the CI
`quality` job. Rely on `pip-audit` alone for direct CVE scanning, backed
by GitHub Dependabot security alerts and semgrep's `p/security-audit`
ruleset for orthogonal coverage.

## Context

ADR-010 §3 CVE scanning specifies `safety AND pip-audit (both)` for
redundancy across different vulnerability databases (PyUp vs OSV). That
was written when both tools ran clean with no auth.

The current `safety` 3.7.0 (installed via `uv sync --frozen` on
2026-08-22 during Phase 0 apply verification) has two blockers:

1. **Auth required**. `safety check --full-report` has been deprecated
   since 2024-06-01 and is being replaced by `safety scan`, which
   requires either an interactive `safety auth` login (dev stage) or a
   `--key <API_KEY>` for cicd stage. There is no zero-config path
   suitable for a private solo-dev repo.
2. **`safety scan` crashes**. On our Python 3.14 + typer/click stack,
   invoking `safety scan --output screen` raises `RuntimeError: There
   is no active click context.` from typer internals — a version
   incompatibility between safety 3.7.0 and its own transitive deps.

The combination (auth-required + crash) makes safety a net-negative
for CI reliability today.

## Alternatives considered

**Keep safety, provision an API key**:

- Requires PyUp account + API key management + storing that secret in
  GitHub Environments
- Adds another failure mode (rate limits, upstream service outages)
- Adds another vendor to trust with our dep graph
- Rejected — cost exceeds the marginal database-overlap benefit

**Downgrade safety to a pre-auth version** (2.x):

- Freezes an old tool version with its own known bugs
- Requires excluding it from Dependabot updates
- Rejected — leaves us on a dead-end branch

**Switch to `pip-audit` alone**:

- Zero-config, no auth
- OSV + PyPI advisories database is broad (though PyUp has some gaps
  covered)
- Combined with Dependabot alerts (which use GitHub Advisory Database
  = OSV + curated PRs) and semgrep p/security-audit, coverage is
  comparable to safety+pip-audit
- Chosen

## Rationale

At solo-dev scale, the marginal benefit of a second vuln database
(PyUp vs OSV) does not outweigh the cost of an auth-required CI tool
that currently crashes. The layered defense stays intact:

- **Direct CVE scan**: `pip-audit --skip-editable` (OSV + PyPI advisories)
- **Continuous alerts**: GitHub Dependabot security alerts (Advisory Database)
- **Idiomatic bug scan**: semgrep `p/security-audit` + `p/python`

If a vuln appears in PyUp that OSV misses AND that Dependabot doesn't
surface AND that semgrep doesn't catch, we accept the (small) gap.
Revisit if that risk materializes.

## Consequences

**What this locks in**:

- `pip-audit` is the sole direct CVE scanner
- `pip-audit --skip-editable` is the invocation (not `--strict`, which
  upgrades the local-editable skip to a hard error)
- CI `quality` job's dep-audit step is one command shorter
- No PyUp account / API key to manage

**What this forecloses**:

- No CI signal on PyUp-only advisories (small overlap gap)
- Cannot easily re-add safety later without either (a) provisioning
  auth or (b) waiting for safety maintainers to restore a zero-config
  path

**What we'll revisit**:

- If safety publishes a zero-config `--offline` mode or drops the
  auth requirement, reconsider
- If OSV misses a real vulnerability that PyUp catches AND that
  Dependabot doesn't surface, escalate

## ADR-010 status

Not modified (AGENTS.md prohibition #10 — ADRs are immutable once
Accepted). This decision-journal entry supersedes the specific
"safety AND pip-audit (both)" choice in ADR-010 §3. The overarching
CVE-scanning discipline (scan on every PR, block on ≥ MEDIUM on
production deps, exceptions via `known_advisories.yaml`) is unchanged;
only the specific tool set changed.

## Related concurrent adjustments

Discovered together during the same Phase 0 apply verification:

- **`pytest` bumped `>=8.3,<9` → `>=9,<10`**: pytest 8.4.2 has PYSEC-2026-1845; fix lands in 9.0.3.
- **`syrupy` bumped `>=4.7,<5` → `>=5,<6`**: syrupy 4.x caps pytest at <9; forced by the pytest bump.
- **`pytest-cov>=5,<7` added**: `coverage[toml]` provides the tool, but `--cov` args require the pytest plugin.
- **`mutmut` removed from Phase 0 deps**: `libcst` (transitive) lacks a Python 3.14 wheel and needs Rust to build. `[tool.mutmut]` config in `pyproject.toml` stays (inert without the tool). Add back in Phase 1 with the mutation-testing GH workflow.

These are individually minor and share the same "reality-vs-plan"
theme, so they're captured here rather than one entry each. Any of
them individually could have been a standalone entry if their scope
grew.
