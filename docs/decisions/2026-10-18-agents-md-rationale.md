# AGENTS.md keeps the rules; their reasons live here

**Date**: 2026-10-18
**Author**: AI Bot on behalf of the Code Owner
**Related**: AGENTS.md, #1195, #1198, [2026-08-23 fetch-before-launch tooling](2026-08-23-fetch-before-launch-tooling.md)

## Decision

AGENTS.md loads into every agent session, so it holds the rules and the links
to their owners, and nothing else. The precedents and reasons behind the
rules are kept here, one line each, so trimming the file loses no history.
`scripts/dev/agents-md-check.py` keeps it under its line budget and stops it
from naming a fixed ADR range again.

## Context

By 18 Oct AGENTS.md was 505 lines (27 KB), about 7,000 tokens read at the
start of every session. Around 40% of it was rationale, precedent and an
operator runbook. It had also drifted:

- It named "ADRs 001-010" while 29 ADRs existed.
- A "Rule 15" sat in a list of 13, and "Three rules" was followed by five.
- It told agents to run bare `gh issue create` in one place and forbade it
  in another.
- It gave coverage as "90% line, 85% branch". `coverage-check.py` sets
  per-package floors, and `pyproject.toml` has one combined `fail_under =
  90`.
- It called mutation testing "the load-bearing gate" while cosmic-ray
  covered two domain packages weekly.

## Why each rule exists

- **Issue first, via `create-issue.sh`**: #245 was created without a
  milestone or assignee, which the Code Owner called a huge process
  violation. The wrapper refuses that shape.
- **`create-pr.sh`, never bare `gh pr create`**: bare PR creation skipped the
  body lint and broke CI on PR #112 and PR #119.
- **Design runs its commands against the tree**: PR #119 dropped
  `--bootstrap` on a design position nobody had run, and CI failed.
- **Changelog fragments**: through the #124-#132 queue every concurrent PR
  edited `[Unreleased]` and forced a rebase cascade. A fragment per PR
  removes the shared line.
- **Change spec gate (#194)**: between 21 and 24 Aug about 180 PRs shipped
  with only 4 change specs; the SDD flow was skipped silently. Two wrappers
  now refuse it. Writing specs for already-shipped PRs is out of scope.
- **Reference specs by path**: an apply phase spent about 200k tokens
  re-describing an existing `change.md` inside a sub-agent prompt.
- **`launch-agent.sh` before a worktree sub-agent**: the 22 Aug wave-3
  batch lost two rebases to a stale local `main`.
- **Checkpoint discipline**: on 24 Aug a sub-agent wrote about 200 lines
  without committing or pushing, and a rate limit cost a recovery cycle.
  The recovery runbook is in `scripts/dev/sub-agent-prompt-checklist.md`.
- **`# gitleaks:allow` on fake credentials**: PR #132 (Slack adapter) lost
  an amend-and-push cycle because `# noqa: S105` does not satisfy the
  secrets scan.
- **Bot authors every commit**: a commit authored as the operator trips
  `require_last_push_approval`, and the operator can't approve a PR the
  operator opened.
- **Zero footprint, with a research carve-out (#1198)**: outside names
  leak context and age badly. Research that weighs prior art can't work
  without naming it, so dated research and accepted ADRs may.

## Consequences

- One owner per rule: coverage and mutation floors in
  `docs/guides/test-tiers.md`, merging in `docs/guides/pr-flow.md`, the
  sub-agent prompt text and runbook in
  `scripts/dev/sub-agent-prompt-checklist.md`.
- A new rule goes into AGENTS.md as the rule; its story goes here.
