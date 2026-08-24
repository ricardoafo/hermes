/**
 * The decisions `merge-queue.yml` makes, as pure functions.
 *
 * ## Why this file exists (not just bash inside a workflow)
 *
 * Logic inside a `script:` block or a `run:` step can only be tested by
 * merging it and watching. That is exactly how the previous merge queue
 * shipped three defects nobody caught in review:
 *
 *   - trusted `mergeable_state` as final without polling; got `unknown`
 *     and skipped valid candidates
 *   - refused `unstable` state as if it meant "not mergeable" (it doesn't —
 *     it means a NON-required check is red; required ones are green)
 *   - relied on `pulls.merge` fast-forward when the base had moved and
 *     silently deleted the reasoning in the squash body
 *
 * Every one of those was a hypothesis living in YAML with no way to
 * assert against a fixture. This module is the fix — the judgement lives
 * here, the I/O stays in the workflow.
 *
 * ## Why CommonJS in a Python project
 *
 * `actions/github-script` runs Node and uses `require()`. It can load
 * this file straight from a sparse checkout of `tools/merge`, with no
 * `npm ci` and no build step. The `select` job runs ~50 times a day —
 * every CI completion, every approval, every merge, hourly cron — and
 * adding `setup-node` + install to each pass would consume most of the
 * self-hosted runner's headroom for a step that is otherwise API calls
 * and nothing else.
 *
 * Types come from JSDoc. The real discipline is `decisions.test.cjs`,
 * which is the thing prior queue iterations never had.
 *
 * ## The shape of the data
 *
 * Every function takes PLAIN OBJECTS shaped like the GitHub REST
 * responses, never an octokit client. Fixtures reach them, real
 * production data reaches them; nothing else has to.
 */

/**
 * @typedef {{ user: { login: string }, state: string }} Review
 * @typedef {{ number: number, draft: boolean, head: { sha: string, ref: string },
 *             mergeable: boolean | null, mergeable_state: string }} PullSummary
 */

/**
 * States in which a pull request may be merged.
 *
 * An ALLOWLIST, not a denylist: a state we have never seen is treated as
 * "do not merge", which is the safe direction to be wrong in.
 *
 *   clean     — mergeable, everything green
 *   unstable  — a NON-required check is red or skipped; required ones green
 *   behind    — behind the base, which is normal here — revalidation
 *               merges into the current tip before testing, so "behind"
 *               is a fact about the branch, not a blocker
 *   has_hooks — mergeable, repository has pre-receive hooks
 *
 * `blocked` is deliberately absent: it usually means a required check
 * hasn't concluded yet (CI still running, or approval missing). Next
 * trigger picks it up. `dirty` means real conflicts.
 */
const MERGEABLE_STATES = new Set(['clean', 'unstable', 'behind', 'has_hooks']);

/**
 * Has a human approved this pull request as it currently stands?
 *
 * The last OPINIONATED review per author wins, so a CHANGES_REQUESTED
 * that came after an APPROVE correctly overrides it. COMMENTED reviews
 * are noise and must not displace either — a reviewer leaving a note
 * after approving has not withdrawn the approval, and treating that as
 * a withdrawal would strand the PR.
 *
 * @param {Review[]} reviews - in the order the API returned them (oldest first)
 * @returns {boolean}
 */
function isApproved(reviews) {
  const latest = new Map();
  for (const r of reviews) {
    if (r.state === 'APPROVED' || r.state === 'CHANGES_REQUESTED') {
      latest.set(r.user.login, r.state);
    }
  }
  return [...latest.values()].includes('APPROVED');
}

/**
 * Should this pull request be revalidated in this pass?
 *
 * Returns a REASON in every case, including the yes. A log line that
 * says why it skipped is the difference between "nothing to do" and
 * "I looked at the wrong thing" — one is a quiet week, the other is a
 * silent failure that costs a human hour to notice.
 *
 * @param {{ pr: PullSummary, reviews: Review[] }} input
 * @returns {{ eligible: boolean, reason: string }}
 */
function isCandidate({ pr, reviews }) {
  if (pr.draft) {
    return { eligible: false, reason: 'draft — not a candidate' };
  }
  if (!isApproved(reviews)) {
    return { eligible: false, reason: 'not approved — waiting on review' };
  }
  if (pr.mergeable === false || pr.mergeable_state === 'dirty') {
    return { eligible: false, reason: 'CONFLICTS — a human must resolve them' };
  }
  if (!MERGEABLE_STATES.has(pr.mergeable_state)) {
    return {
      eligible: false,
      reason: `mergeable_state='${pr.mergeable_state}' — not merging`,
    };
  }
  return { eligible: true, reason: `candidate (${pr.mergeable_state})` };
}

/**
 * Immediately before merging: is what we tested still what would land?
 *
 * The gates take minutes. In that window the sibling PR in the same
 * matrix may have merged (moving `main`), the author may have pushed
 * (moving the head), or the reviewer may have dismissed their approval.
 * "It was true when we started" is not consent, and it is not a
 * validation of the tree that would actually land.
 *
 * @param {{
 *   testedBase: string, currentBase: string,
 *   testedHead: string, currentHead: string,
 *   state: string, draft: boolean,
 *   mergeable: boolean | null, mergeableState: string,
 *   reviews: Review[],
 * }} input
 * @returns {{ merge: boolean, reason: string }}
 */
function shouldMerge(input) {
  if (input.currentBase !== input.testedBase) {
    return {
      merge: false,
      reason: `base moved during the run (${input.testedBase} -> ${input.currentBase})`,
    };
  }
  if (input.currentHead !== input.testedHead) {
    return {
      merge: false,
      reason: 'head moved during the run — not merging what was not tested',
    };
  }
  if (input.state !== 'open' || input.draft) {
    return { merge: false, reason: 'no longer an open, ready pull request' };
  }
  if (input.mergeable === false || input.mergeableState === 'dirty') {
    return { merge: false, reason: 'CONFLICTS — a human must resolve them' };
  }
  if (!isApproved(input.reviews)) {
    return { merge: false, reason: 'approval withdrawn during the run' };
  }
  return { merge: true, reason: `validated against base ${input.testedBase}` };
}

/**
 * May this candidate skip the tree-verification gates?
 *
 * When the approved head ALREADY CONTAINS the current main tip,
 * merging it is a fast-forward, so the tree that would land is
 * identical to the head tree CI has already validated. Running the
 * gates on that same tree re-proves a proven thing.
 *
 * ## Why this is the dangerous decision
 *
 * Every other function here refuses to merge when unsure, and the cost
 * of being wrong is a stall a human notices. A wrong TRUE here MERGES
 * A TREE NOTHING CHECKED, silently. So BOTH conditions are required
 * and neither is inferred:
 *
 *   - the head must contain the main tip that was RESOLVED and
 *     asserted by the caller — a fast-forward against a stale tip
 *     proves nothing
 *   - CI must have actually concluded `success` on that head. A
 *     fast-forward says the tree did not change; it says nothing about
 *     whether it was green in the first place
 *
 * Anything else runs the gates. "Cannot check" is not "clean".
 *
 * @param {{ mainIsAncestor: boolean, ciConclusion: string|null }} input
 * @returns {{ skip: boolean, reason: string }}
 */
function gateSkip(input) {
  if (!input.mainIsAncestor) {
    return {
      skip: false,
      reason: 'main is not an ancestor of the head — this tree is new',
    };
  }
  if (input.ciConclusion !== 'success') {
    return {
      skip: false,
      reason: `CI on this head concluded '${input.ciConclusion || 'nothing'}' — not proven green`,
    };
  }
  return {
    skip: true,
    reason: 'head already contains main; CI validated this exact tree',
  };
}

/**
 * After a merge, does `main` need CI to run against it again?
 *
 * ## Why the dispatch exists at all
 *
 * A push made with GITHUB_TOKEN raises no events, so merging from this
 * workflow leaves `main` with zero check runs and the next queued PR
 * with no `workflow_run` completion to wake it. The merge step
 * hand-starts both to close that gap.
 *
 * ## When it is safe to skip the ci.yml restart
 *
 * The queue merges only when the approved head already contains the
 * main tip (fast-forward), so squashing produces a tree IDENTICAL to
 * the head tree CI already validated. Comparing TREES (not SHAs) is
 * what makes the proof honest — a squash always produces a new commit
 * with a new SHA, but the same input trees produce the same output
 * tree by definition.
 *
 * ## Fail direction
 *
 * Unsure means RUN. A missing tree, an API that did not answer,
 * anything not an exact byte match — all restart. The cost of a
 * needless run is a few minutes on a self-hosted runner; the cost of
 * skipping a real difference is `main` HEAD nobody validated.
 *
 * The revalidate workflow's OWN restart is a separate decision (see
 * merge-queue.yml) — it always fires, because it continues the queue
 * rather than re-proving a tree.
 *
 * @param {{ mergedTree: string|null, validatedTree: string|null }} input
 * @returns {{ restart: boolean, reason: string }}
 */
function ciRestart(input) {
  const { mergedTree, validatedTree } = input;
  if (!mergedTree || !validatedTree) {
    return {
      restart: true,
      reason: 'could not resolve both trees — "cannot check" is not "unchanged"',
    };
  }
  if (mergedTree !== validatedTree) {
    return {
      restart: true,
      reason: `the squash produced tree ${mergedTree.slice(0, 7)}, CI validated ${validatedTree.slice(0, 7)}`,
    };
  }
  return {
    restart: false,
    reason: `tree ${mergedTree.slice(0, 7)} is byte-identical to the one CI passed on this head`,
  };
}

module.exports = {
  MERGEABLE_STATES,
  isApproved,
  isCandidate,
  shouldMerge,
  gateSkip,
  ciRestart,
};
