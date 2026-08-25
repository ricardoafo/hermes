### Fixed

- **Merge queue explicitly closes linked issues** + **sub-agent
  template requires PR body implementation detail**
  (`.github/workflows/merge-queue.yml`,
  `scripts/dev/sub-agent-prompt-checklist.md`): follow-up to #225
  addressing the observed 2026-08-25 behaviour where PRs with correct
  `Closes #N` references (visible in GraphQL's
  `closingIssuesReferences`) merged without actually closing the
  linked issues. The queue-merge path via `pulls.merge` API silently
  bypasses GitHub's built-in auto-close hook. Fix: after `pulls.merge`
  succeeds, iterate `closingIssuesReferences` via GraphQL and call
  `issues.update` explicitly for each still-OPEN linked issue.
  Idempotent (closing already-closed issues is a no-op). Adds a
  linking comment on each closure for audit. Also extends the
  sub-agent prompt checklist with item 10: PR body must include a
  technical implementation explanation (design decisions, new
  abstractions, interactions with existing code), not just a one-line
  summary. Mermaid diagrams welcome when they clarify architecture,
  flow, or state — but not when a paragraph would say the same thing.
