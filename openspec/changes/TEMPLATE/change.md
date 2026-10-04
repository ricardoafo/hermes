# Change: {change-name}

**SDD change**: `{change-name}`
**Date**: YYYY-MM-DD
**Status**: Proposed | Applied | Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/{change-name}/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

3-5 sentences. What is shipping, what is the main architectural pick,
and what is the main risk. A reviewer should decide whether to read
further based on this alone.

## Scope

### In

- Bulleted list of what this change delivers.
- File-level granularity is fine; module-level is fine.

### Out

- Bulleted list of what is deferred, with the target phase or
  follow-up issue if known.

## Decisions

### D-1: {short name for the decision}

- **Decision**: One sentence — the chosen path.
- **Alternatives**: 1-3 discarded options with one-line reason for
  rejection.
- **Rationale**: Why the chosen path beats alternatives. Cite ADR,
  invariant, or constraint by number.
- **Cost of reversal**: LOW | MEDIUM | HIGH — what would need to
  change if we reverse this later.

### D-2: {next decision}

...

## Requirements

Each requirement is a testable statement. Each acceptance criterion
is a shell-verifiable check.

### R-1: {short title}

- **A-1.1**: `<command that must exit 0>` or `<file that must exist>`
  or `<content that must match>`
- **A-1.2**: Next criterion for the same requirement.

### R-2: {next requirement}

...

## Design

Copy-ready content for the apply phase. Examples:

- Full text of new config files (`pyproject.toml`, `renovate.json`, ...)
- Function signatures or interface stubs
- Code snippets showing exact structure
- File-tree diagram of what gets created / modified / deleted
- Mermaid diagrams where they show a relationship the text alone
  would take a paragraph to explain

Keep this section a working artifact — the `sdd-apply` phase should
be able to implement from Design alone without needing to re-derive.

## Test plan

How we know this change is done. Typically:

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the scaffolding PR.
4. Any human-review sign-offs required.

Test tiers touched: <!-- unit, property, contract, integration, e2e, mutation, or none -->

The line above is copied into the PR body's `## Testing` section by
`render-pr-body.sh`; `create-pr.sh` refuses a PR without it.

## Risks

Non-obvious failure modes discovered during design + their
mitigations. Do NOT list generic "software has bugs" — list the
specific things that could bite that a reviewer wouldn't spot from
the diff alone.

- **Risk 1**: Description + mitigation.
- **Risk 2**: ...

## Dependencies on other changes

- Other ADRs affected (with promotion path)
- Other open PRs that must land first
- External settings changes (GH ruleset, environment secrets, VPS
  provisioning) with pointer to who applies them
