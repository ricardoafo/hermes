# Change: merge-queue-auto-close-v4-fetch

**SDD change**: `merge-queue-auto-close-v4-fetch`
**Date**: 2026-08-26
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#267)

Mirror of engram topic key `sdd/merge-queue-auto-close-v4-fetch/change`.

## Summary

v3 (PR #262) landed the token-split idea correctly but used `require('@actions/github').getOctokit(...)` — which fails at runtime with `MODULE_NOT_FOUND` because the merge queue's `actions/checkout@v7 with sparse-checkout: tools/merge` step leaves the workspace with NO `node_modules`. The crash killed everything after `pulls.merge`: the belt-and-suspenders close loop, the delete-ref step, and the hand-dispatch of the next queue pass all silently no-op'd. Observed on #266 (2026-08-26 12:17). v4 replaces the Octokit-via-require with three direct `fetch()` calls to GitHub's REST API — Node 20+ has fetch built-in, no bundle dependency.

## Scope

### In

- **MOD**: `.github/workflows/merge-queue.yml` `Merge` step — replace the `require('@actions/github').getOctokit(...)` construction with three inline async functions (`issueState`, `closeIssue`, `commentIssue`) that use built-in `fetch()` + WORKFLOW_TOKEN + GitHub's REST API v3. Preserves every semantic of v3 (union of GraphQL + body regex, idempotent close, diagnostic markers).
- Update the block header from "v3" to "v4" and document the failure mode so v5's debugger sees the full history.
- **NEW**: `changelog.d/wave-merge-queue-auto-close-v4-fetch.md`.

### Out

- Broader refactor of the Merge step (extract to a reusable action). The step is ~200 lines of imperative logic that IS shared across every automated merge; extracting is worth doing but out of scope for this hotfix.
- Manual retro-close of #260, #261, #264 (silently missed by the v3 crash) — will be done as part of this PR's own merge (v4 will close them AFTER this PR merges via its own body's `Closes` references).

## Decisions

### D-1: `fetch()` over Octokit

- **Decision**: use Node 20+'s built-in `fetch()` for the three issue-close REST calls.
- **Alternatives**: (a) install `@actions/github` via `npm ci` before the step; (b) call the workspace path `${GITHUB_ACTION_PATH}/node_modules/@actions/github`; (c) hardcode the module path.
- **Rationale**: `fetch()` has ZERO dependencies. The alternatives either add a workflow step (npm install) or hardcode an implementation detail (action's internal path) that GitHub may change without notice.
- **Cost of reversal**: LOW — if a future need requires Octokit's higher-level features (retries, pagination), swap back to Octokit + install step.

### D-2: Three thin helper functions, not a single "close" function

- **Decision**: separate `issueState`, `closeIssue`, `commentIssue`.
- **Alternatives**: single `closeIssueAndComment(n)` that does all three.
- **Rationale**: the idempotency check (`issueState`) reads first; the write path (`closeIssue` + `commentIssue`) writes. Separation lets us skip the writes when state === 'closed' without threading a control-flow flag through a monolithic function.
- **Cost of reversal**: LOW.

### D-3: Each helper throws on non-2xx; the outer per-issue try/catch handles

- **Decision**: helpers `throw` a `new Error(...)` on non-`res.ok`. The existing per-issue try/catch turns the throw into a `core.warning` and continues to the next issue.
- **Alternatives**: helpers return `{ok, error}` tuples.
- **Rationale**: matches JavaScript idioms; keeps helper bodies tiny. The outer catch is unchanged from v3.
- **Cost of reversal**: LOW.

## Requirements

### R-1: v4 successfully closes linked issues on merge

- **A-1.1**: After this PR merges, `gh issue view 267 --json state --jq .state` returns `"CLOSED"` — closed by v4's own execution against this PR.
- **A-1.2**: Merge-queue log for this PR contains a line matching `#XX: closed linked issue #267`.

### R-2: No MODULE_NOT_FOUND

- **A-2.1**: Merge-queue log for THIS PR contains NO `Cannot find module '@actions/github'`.
- **A-2.2**: `grep -F "require('@actions/github')" .github/workflows/merge-queue.yml` returns nothing.

### R-3: v3's other semantics preserved

- **A-3.1**: The GraphQL + body-regex union logic is unchanged.
- **A-3.2**: The idempotency check (`state === 'closed' → skip`) is preserved.
- **A-3.3**: The delete-ref + hand-dispatch steps AFTER the close loop run to completion.

## Design

Direct-fetch helpers replacing the Octokit instance:

```javascript
const ghApiHeaders = {
  'Authorization': `Bearer ${process.env.WORKFLOW_TOKEN}`,
  'Accept': 'application/vnd.github+json',
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'hermes-merge-queue/v4',
};
const ghApiBase = `https://api.github.com/repos/${owner}/${repo}`;

async function issueState(n) {
  const res = await fetch(`${ghApiBase}/issues/${n}`, { headers: ghApiHeaders });
  if (!res.ok) throw new Error(`GET issue #${n}: ${res.status} ${await res.text()}`);
  return (await res.json()).state; // 'open' | 'closed'
}
async function closeIssue(n) {
  const res = await fetch(`${ghApiBase}/issues/${n}`, {
    method: 'PATCH',
    headers: { ...ghApiHeaders, 'Content-Type': 'application/json' },
    body: JSON.stringify({ state: 'closed', state_reason: 'completed' }),
  });
  if (!res.ok) throw new Error(`PATCH issue #${n}: ${res.status} ${await res.text()}`);
}
async function commentIssue(n, text) {
  const res = await fetch(`${ghApiBase}/issues/${n}/comments`, {
    method: 'POST',
    headers: { ...ghApiHeaders, 'Content-Type': 'application/json' },
    body: JSON.stringify({ body: text }),
  });
  if (!res.ok) throw new Error(`POST comment #${n}: ${res.status} ${await res.text()}`);
}
```

The close loop becomes:

```javascript
for (const issueNumber of closableIssues) {
  try {
    const state = await issueState(issueNumber);
    if (state === 'closed') {
      core.info(`#${number}: linked issue #${issueNumber} already closed — skip`);
      continue;
    }
    await closeIssue(issueNumber);
    await commentIssue(issueNumber,
      `Closed by #${number} via merge queue (belt-and-suspenders; GitHub auto-close did not fire).`);
    core.info(`#${number}: closed linked issue #${issueNumber}`);
  } catch (err) {
    core.warning(`#${number}: failed to close linked issue #${issueNumber}: ${err.message}`);
  }
}
```

## Test plan

1. Land this PR. Grep the merge-queue log for THIS PR — expect `#XX: closed linked issue #267` AND no `Cannot find module` line.
2. Run `python scripts/dev/verify_auto_close.py --limit 3` post-merge — every audited PR should show `v3_ok` (name unchanged despite the v4 fix — the diagnostic marker is `closed linked issue`, common to v3 + v4).
3. Any subsequent PR with `Closes #N` in body: linked issue auto-closes without manual intervention.

## Risks

- **R1** — GitHub REST API rate limit for the workflow token is 5000/hour. Each merge does 3 calls per linked issue; typical PR has 1-2 linked issues → ≤ 6 calls per merge. At even 100 merges/hour, we're at 3% of the budget. Negligible.
- **R2** — `fetch()` timeout defaults to 300s in Node 20+; a hung GitHub API call could delay the workflow. Mitigation: the per-issue try/catch converts any error (including timeout) to a warning; the merge itself already succeeded before this code runs.
- **R3** — Future Node runtime downgrade breaks `fetch()`. Mitigation: workflow's `runs-on` pin covers Node 20+; a downgrade would break many other actions too.

## Dependencies on other changes

- Predecessor: PR #262 (v3 token split) — this fixes the crash v3 introduced.
- Cross-references: manually-closed stragglers #260, #261, #264, #257 (silently missed by v3 crash) — will be retro-closed as part of this PR merging + v4 firing.
