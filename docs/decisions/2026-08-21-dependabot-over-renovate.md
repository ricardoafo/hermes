# Dependabot over Renovate for dependency updates

**Date**: 2026-08-21
**Author**: Maintainer
**Related**: ADR-010 §Renovate (partially superseded by this entry)

## Decision

Use Dependabot (native GitHub) for automated dependency updates. Skip
Renovate.

## Context

ADR-010 originally chose Renovate over Dependabot on the grounds that
Renovate had two features Dependabot lacked at the time of writing:

1. **Cooldown period** — delay before opening a PR on a brand-new release,
   to catch malicious yanks in the 7-day window
2. **Package grouping** — bundle patch updates into a single PR to reduce
   notification firehose

Both features have since landed in Dependabot (cooldown via the
`cooldown:` key, grouping via the `groups:` key). ADR-010's original
rationale for choosing Renovate no longer holds.

## Alternatives considered (post-parity)

**Renovate**:

- More advanced package rules (conditional automerge based on age, semver
  tier, package category)
- Dependency dashboard (single issue tracks all pending)
- Broader ecosystem support (unused for hermes — Python + GH Actions only)

**Dependabot**:

- Zero external app to install / trust with repo access
- Native to GitHub — better integration with Security Advisories,
  Dependency Graph, Vulnerability Alerts
- Simpler config surface — one YAML file, no external service
- Cooldown + grouping already sufficient for hermes' needs

## Rationale

For a solo-dev private repo with ~50 direct dependencies (Python + GH
Actions), Dependabot's simplicity wins:

- No third-party app to install, review, or grant repo access to
- Fewer moving parts = fewer things to break
- GitHub-native integration with Security Advisories = single dashboard for
  all vulnerability state
- Renovate's advanced features only pay off at team scale or polyglot
  scale — hermes is neither

## Consequences

**What this locks in**:

- Renovate app never installed
- Configuration lives in `.github/dependabot.yml` — one file, one syntax
- Cooldown + grouping strategies remain (same discipline, different tool)
- Auto-merge conditions handled by the existing CI's `ci-success` gate
  plus branch protection's `require_code_owner_reviews` — the maintainer still
  reviews every dependency update PR (no silent auto-merge)

**What this forecloses**:

- Renovate's per-PR dependency-dashboard signal
- Fine-grained conditional automerge rules (e.g., "auto-merge patches
  only for these 8 well-known deps") — Dependabot doesn't support this
  granularity out of the box. If we need it later, we add a GH Actions
  workflow that inspects Dependabot-authored PRs and enables auto-merge
  when conditions match. Deferred until we hit the pain.

**What we'll revisit**:

- If polyglot scope appears (e.g., adding a JS TUI component or a Rust
  perf-critical helper), reconsider Renovate for cross-ecosystem uniform
  rules
- If Dependabot cooldown or grouping features regress or are deprecated

## ADR-010 status

Not modified (AGENTS.md prohibition #10 — ADRs are immutable once
Accepted). This decision-journal entry supersedes the specific
"Renovate over Dependabot" choice in ADR-010 §Renovate. The overarching
dependency-management discipline (7-day cooldown, grouped patches,
manual review for majors, SHA-pinning for GH Actions) is unchanged.
