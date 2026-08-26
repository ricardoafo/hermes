# Change: merge-queue-auto-close-v2

**SDD change**: `merge-queue-auto-close-v2`
**Date**: 2026-08-26
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#247)

Mirror of engram topic key `sdd/merge-queue-auto-close-v2/change`.

## Summary

Second attempt at automatic issue-close on PR merge. The v1 belt-and-suspenders from PR #233 relied on `closingIssuesReferences` (GraphQL). Since 2026-08-25 that field has been returning empty at merge time even when the PR body clearly contains `Closes #N` — the GraphQL index is populated asynchronously, and the merge-queue's belt-and-suspenders was running before indexing caught up. Silent no-op → issues stayed OPEN. v2 adds a body-regex fallback that unions with the GraphQL result and closes every referenced issue.

## Scope

### In

- **MOD**: `.github/workflows/merge-queue.yml` — replace the v1 belt-and-suspenders block inside the `Merge` step with a v2 that (a) runs the GraphQL query and captures whatever it returns, (b) parses the PR body with `/\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b/gi`, (c) unions the two sets, (d) idempotently closes each via `issues.update` + `issues.createComment`. Emits `core.info` for each source so a future regression is diagnosable from a single log grep. ~50 LOC delta.
- **Changelog fragment**: `changelog.d/wave-merge-queue-auto-close-v2.md`.

### Out

- Retro-close of the 6 issues silently missed by v1 (#236, #237, #241, #243, #244, #246 → closed manually as part of the reconciliation this change was born from — see #247 comment thread).
- Migrating the belt-and-suspenders to a separate reusable action — the file is <30 lines of runtime JS, extraction would cost more than it saves.
- Metrics/alerting on GraphQL-vs-body drift — a follow-up if the drift itself becomes signal-worthy.

## Decisions

### D-1: Body-regex fallback lives INSIDE the workflow, not a separate script

- **Decision**: parse the body directly in the `Merge` step's inline `actions/github-script@v9` block.
- **Alternatives**: (a) mint a new `.github/actions/close-linked-issues/action.yml` reusable action; (b) shell out to a `scripts/dev/close-linked-issues.sh` invoked from the workflow.
- **Rationale**: the regex is 1 line, the merge step already imports `pr.body` for `pulls.merge`'s `commit_message`. Extracting adds indirection without reducing complexity. The step is fully unit-testable by grepping the workflow log after any merge (see Test plan).
- **Cost of reversal**: LOW — extraction is a mechanical refactor.

### D-2: Body regex accepts past-tense forms (`closed`, `fixed`, `resolved`)

- **Decision**: `/\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b/gi`.
- **Alternatives**: only accept present-tense verbs (`closes`, `fixes`, `resolves`) — matches GitHub's official list.
- **Rationale**: authors sometimes write `Closed #240` in PR bodies. GitHub's own hook ignores those, but for the belt-and-suspenders we want maximal coverage — an occasional false positive (a body saying "we haven't closed #X yet") is cheaper than a missed close, because the actual close still requires `pulls.merge` to succeed AND the issue to be in the same repo AND the workflow to have `issues:write` permission.
- **Cost of reversal**: LOW.

### D-3: Union with GraphQL (do NOT skip GraphQL when body-regex hits)

- **Decision**: run both sources every time, dedupe with a `Set`.
- **Alternatives**: skip GraphQL if body-regex found anything.
- **Rationale**: GraphQL's `closingIssuesReferences` can pick up cross-repo references (e.g. `ricardoafo/other-repo#42`) that a naive body regex ignores. Belt-and-suspenders means running both.
- **Cost of reversal**: LOW.

### D-4: Idempotent — `issues.get` before `issues.update`

- **Decision**: refetch each issue via REST and skip if already closed.
- **Alternatives**: unconditional `issues.update({state: 'closed'})` (GitHub tolerates closing an already-closed issue).
- **Rationale**: `createComment` is NOT idempotent — every rerun would post another "Closed by #N via merge queue" comment. The `issues.get` check adds 1 REST call per issue but eliminates the comment-spam failure mode of a re-run.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Every merged PR with `Closes #N` in body closes the linked issue

- **A-1.1**: After the next merge-queue run against a PR containing `Closes #NNN` in the body, `gh issue view NNN --json state --jq .state` returns `"CLOSED"`.
- **A-1.2**: Merge-queue log for that PR contains a line matching `#XX: closed linked issue #NN` OR `#XX: linked issue #NN already closed — skip`.

### R-2: v2 diagnostic output visible in workflow log

- **A-2.1**: For every merged PR, the workflow log contains one line matching `closingIssuesReferences returned N nodes`.
- **A-2.2**: For every merged PR whose body has `Closes/Fixes/Resolves #N`, the log contains one line matching `PR body regex found closing references`.

### R-3: No comment spam on re-runs

- **A-3.1**: If the merge-queue workflow re-runs (manual dispatch, workflow_run retry) after a PR already merged, no additional "Closed by #N via merge queue" comment is added to the linked issues.

## Design

Full YAML replacement inside `.github/workflows/merge-queue.yml` `Merge` step — see the diff on the branch. Key structural change: v1 had ONE `try/for/catch` around GraphQL+close-loop; v2 splits into `try/set-add/catch` (GraphQL), unconditional body-regex `set.add`, then a single `for/try/catch` close-loop over the deduped set.

Body regex breakdown:

```text
\b                                # word boundary
(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)  # closes | closed | close | fixes | fixed | fix | resolves | resolved | resolve
\s+                               # one or more whitespace
#(\d+)                            # capture issue number
\b                                # word boundary
```

## Test plan

1. **Verify v2 fires on this PR's own merge**: after the PR merges, `gh issue view 247` should be `CLOSED` and the merge-queue log for this PR should contain the two info lines from R-2.
2. **Idempotency spot check**: re-dispatch the merge-queue workflow with `workflow_dispatch` after merge — no duplicate close comments appear on #247.
3. **Regex sanity**: run the same regex against sampled PR bodies from #236, #237, #241, #243, #244, #246 in a scratch node REPL — every one produces at least one match.

## Risks

- **R1** — Regex over-matches (e.g. a body saying "we tracked this in Closed #123 last week" would erroneously close #123). Mitigation: the mislabel is recoverable (`gh issue reopen`), and the false-positive rate is tiny in a well-groomed process. Prefer maximal coverage.
- **R2** — Body-regex hits a stale issue number that was later renumbered/deleted. Mitigation: `issues.get` before update — a 404 falls into the per-issue catch block, logs a warning, and moves on.
- **R3** — GraphQL query starts returning cross-repo refs that the body regex cannot see. Mitigation: `union of sources`, so GraphQL results are always included even when body-regex is empty.

## Dependencies on other changes

- Predecessor: PR #233 (v1 belt-and-suspenders) — this change modifies the block it introduced.
- Cross-references issue reconciliation: see the retro-close comments on #24, #26, #27, #28, #180, #181, #198, #201 and the explicit-close batch on #174, #184, #194, #221, #228, #232, #234, #235, #238, #239, #240 — all closed manually by the reconciliation pass that surfaced this defect.
