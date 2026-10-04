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
 * The gates take minutes. In that window someone else may have merged
 * (moving `main`), the author may have pushed
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
 * The queue knows the tree it validated: the batch tree its gates ran
 * on, or, on the fast path, the head tree CI ran on. When the squash
 * merges reproduce that tree exactly, `main` holds nothing unvalidated.
 * Comparing TREES (not SHAs) is what makes the proof honest — a squash
 * always produces a new commit with a new SHA, but the same input trees
 * produce the same output tree by definition.
 *
 * ## Fail direction
 *
 * Unsure means RUN. A missing tree, an API that did not answer,
 * anything not an exact byte match — all restart. The cost of a
 * needless run is a few minutes on a self-hosted runner; the cost of
 * skipping a real difference is `main` HEAD nobody validated.
 *
 * The merge queue's OWN restart is a separate decision (`afterMerge`):
 * it continues the queue rather than re-proving a tree.
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

/**
 * When did this pull request receive its most recent approval?
 *
 * The queue lands PRs in the order they were approved, so a re-approval
 * after new commits moves the PR to the back: the reviewer's latest
 * consent is what the queue honours, not the first one.
 *
 * @param {Array<Review & { submitted_at?: string|null }>} reviews
 * @returns {string|null} ISO timestamp, or null when nothing is approved
 */
function approvedAt(reviews) {
  let latest = null;
  for (const r of reviews) {
    if (r.state !== 'APPROVED' || !r.submitted_at) {
      continue;
    }
    if (latest === null || Date.parse(r.submitted_at) > Date.parse(latest)) {
      latest = r.submitted_at;
    }
  }
  return latest;
}

/**
 * The order a batch is built and merged in: oldest approval first, PR
 * number as the tie-break.
 *
 * Deterministic on purpose. The batch tree is `main` plus each PR merged
 * in this order, and the squash merges replay the same order, so two
 * passes over the same candidates must agree on it. A candidate with no
 * approval time sorts last rather than first: "unknown" must not jump
 * the queue.
 *
 * @template {{ n: number, approvedAt: string|null }} C
 * @param {C[]} candidates
 * @returns {C[]} a sorted copy; the input is not modified
 */
function orderCandidates(candidates) {
  const at = (c) => (c.approvedAt ? Date.parse(c.approvedAt) : Number.POSITIVE_INFINITY);
  return [...candidates].sort((a, b) => {
    const ta = at(a);
    const tb = at(b);
    if (ta !== tb) {
      return ta < tb ? -1 : 1;
    }
    return a.n - b.n;
  });
}

/**
 * Reason recorded when a PR cannot be merged into the batch being built.
 *
 * The PR may merge cleanly into `main` alone; it is the combination with
 * PRs approved before it that conflicts. It is dropped from this pass and
 * reconsidered by the next one, on the new `main`.
 */
const BATCH_CONFLICT_REASON = 'conflicts with main + earlier batch PRs';

/**
 * May the head fetched for this PR join the batch?
 *
 * The batch must contain the commit that was APPROVED. A head that moved
 * between Select and the build is a commit nobody reviewed.
 *
 * @param {{ approvedHead: string, fetchedHead: string|null }} input
 * @returns {{ admit: boolean, reason: string }}
 */
function admitHead(input) {
  if (!input.fetchedHead) {
    return { admit: false, reason: 'could not fetch the PR head' };
  }
  if (input.fetchedHead !== input.approvedHead) {
    return { admit: false, reason: 'head moved' };
  }
  return { admit: true, reason: `head ${input.approvedHead.slice(0, 7)} is the approved one` };
}

/**
 * May a whole batch skip the gates?
 *
 * Only a batch of exactly one PR can: that is the single-PR case
 * `gateSkip` was written for, where the tree that lands may be the very
 * tree CI validated on the head. Two or more PRs merged together make a
 * tree no CI run has ever seen, whatever each head's own CI said.
 *
 * @param {{ size: number, mainIsAncestor: boolean, ciConclusion: string|null }} input
 * @returns {{ skip: boolean, reason: string }}
 */
function batchGateSkip(input) {
  if (input.size !== 1) {
    return {
      skip: false,
      reason: `batch of ${input.size} — the combined tree is new`,
    };
  }
  return gateSkip(input);
}

/**
 * @typedef {{
 *   order: number[],
 *   good: number[],
 *   bad: Array<{ n: number, gate: string }>,
 *   pending: number[][],
 *   red: Record<string, string>,
 * }} BisectState
 */

/**
 * Start isolating the PRs that turn a batch red.
 *
 * `pending` is a STACK whose top is the last element. It starts with
 * the whole batch, so a green batch costs exactly one gate run and
 * bisect only pays when something is actually wrong.
 *
 * @param {number[]} order - PR numbers in merge order
 * @returns {BisectState}
 */
function bisectInit(order) {
  return {
    order: [...order],
    good: [],
    bad: [],
    pending: order.length ? [[...order]] : [],
    red: {},
  };
}

/**
 * Keep `ns` in merge order, whatever order they were collected in.
 *
 * @param {number[]} order
 * @param {number[]} ns
 * @returns {number[]}
 */
function inMergeOrder(order, ns) {
  const wanted = new Set(ns);
  return order.filter((n) => wanted.has(n));
}

/**
 * What to gate next.
 *
 * The tree to build is `main` + every PR already proven good + the set
 * under test, in merge order. Proven-good PRs stay in the tree because
 * they are going to land: a set that is only green WITHOUT them would be
 * red once merged after them.
 *
 * `known` is set when that exact tree was already gated red. It happens
 * every time a first half comes back green: the second half on top of it
 * rebuilds the tree that just failed. Re-running the gates there would
 * spend minutes proving a known fact, so the caller records `known`
 * instead of gating.
 *
 * @param {BisectState} state
 * @returns {{ done: true, reason: string }
 *         | { done: false, set: number[], tree: number[], reason: string,
 *             known: { passed: false, gate: string } | null }}
 */
function bisectNext(state) {
  if (state.pending.length === 0) {
    return {
      done: true,
      reason: `${state.good.length} good, ${state.bad.length} bad`,
    };
  }
  const set = state.pending[state.pending.length - 1];
  const tree = inMergeOrder(state.order, [...state.good, ...set]);
  const gate = state.red[tree.join(',')];
  return {
    done: false,
    set: [...set],
    tree,
    reason: gate === undefined
      ? `gating ${set.length} PR(s) on top of ${state.good.length} proven good`
      : `${set.length} PR(s) on top of ${state.good.length} proven good — this tree already failed`,
    known: gate === undefined ? null : { passed: false, gate },
  };
}

/**
 * Record the gate verdict for the set `bisectNext` returned.
 *
 *   green            — the whole set is good
 *   red, one PR      — that PR is bad; the failing gate is kept so the
 *                      author is told what to fix
 *   red, several PRs — split in two; the FIRST half is gated first, so
 *                      every PR before a set is settled by the time the
 *                      set is tested, and the tree under test is always
 *                      what would land if the set were the last good one
 *
 * @param {BisectState} state
 * @param {{ passed: boolean, gate: string|null }} verdict
 * @returns {BisectState} a new state; the input is not modified
 */
function bisectRecord(state, verdict) {
  const set = state.pending[state.pending.length - 1];
  if (!set) {
    throw new Error('bisectRecord called with nothing pending');
  }
  const pending = state.pending.slice(0, -1);
  if (verdict.passed) {
    return {
      ...state,
      pending,
      good: inMergeOrder(state.order, [...state.good, ...set]),
    };
  }
  const gate = verdict.gate || 'unknown gate';
  const tree = inMergeOrder(state.order, [...state.good, ...set]).join(',');
  const red = { ...state.red, [tree]: gate };
  if (set.length === 1) {
    return {
      ...state,
      pending,
      red,
      bad: [...state.bad, { n: set[0], gate }],
    };
  }
  const mid = Math.ceil(set.length / 2);
  return {
    ...state,
    pending: [...pending, set.slice(mid), set.slice(0, mid)],
    red,
  };
}

/**
 * Should the merge pass hand-start another one when it is done?
 *
 * A pass that merged something, or stopped early because the world
 * moved, leaves work the next pass can do on the new `main`. A pass that
 * merged nothing would only rebuild the same batch, fail the same gates
 * and dispatch itself again, forever. Those wait for a real trigger: a
 * push, an approval, a CI completion or the hourly cron.
 *
 * @param {{ dryRun: boolean, merged: number, stoppedEarly: boolean }} input
 * @returns {{ redispatch: boolean, reason: string }}
 */
function afterMerge(input) {
  if (input.dryRun) {
    return { redispatch: false, reason: 'dry run — nothing changed, nothing to continue' };
  }
  if (input.stoppedEarly) {
    return {
      redispatch: true,
      reason: 'stopped before the batch was done — rebuild on the new main',
    };
  }
  if (input.merged > 0) {
    return {
      redispatch: true,
      reason: `merged ${input.merged} — pick up newly approved PRs`,
    };
  }
  return {
    redispatch: false,
    reason: 'merged nothing — a re-run would repeat this pass',
  };
}

/**
 * Hidden marker that ties a "gates failed" comment to one head.
 *
 * Keyed on the head, not the PR: a new push is a new candidate and
 * deserves a fresh verdict, while re-running the same head must not
 * repost the same comment every pass.
 *
 * @param {string} head
 * @returns {string}
 */
function badMarker(head) {
  return `<!-- merge-queue-bad:${head} -->`;
}

/**
 * The comment left on a PR the batch proved red.
 *
 * @param {{ n: number, head: string, gate: string, base: string }} input
 * @returns {{ body: string, marker: string }}
 */
function badComment(input) {
  const marker = badMarker(input.head);
  const body = [
    marker,
    `The merge queue did not merge #${input.n}: the \`${input.gate}\` gate failed on`,
    `\`main\` ${input.base.slice(0, 7)} + the batch PRs approved before it + head ${input.head.slice(0, 7)}.`,
    '',
    `The queue will not try this head again: it is labelled \`${GATES_FAILED_LABEL}\`.`,
    'Push a fix (a new head is tried again), or remove the label to retry this one.',
  ].join('\n');
  return { body, marker };
}

/**
 * Label on a PR whose current head failed the merged-tree gates.
 */
const GATES_FAILED_LABEL = 'merge-queue:gates-failed';

/**
 * Should Select pass over this PR because its head already failed?
 *
 * A gate failure that comes from the PR itself fails the same way every
 * pass: #435 was rebuilt and re-gated every pass for an hour and a half
 * (2026-10-03). Once a head has been told it failed AND carries the
 * label, Select skips it. A new push is a new head and is tried again;
 * removing the label retries the same head.
 *
 * @param {{ head: string, labels: string[], comments: Array<{ body?: string|null }> }} input
 * @returns {{ skip: boolean, reason: string }}
 */
function skipFailedHead(input) {
  if (!input.labels.includes(GATES_FAILED_LABEL)) {
    return { skip: false, reason: 'no gates-failed label' };
  }
  if (!hasBadComment(input.comments, input.head)) {
    return { skip: false, reason: 'gates failed on an older head; this head is new' };
  }
  return {
    skip: true,
    reason: `gates already failed on head ${input.head.slice(0, 7)} — waiting for a push or the label's removal`,
  };
}

/**
 * Has this head already been told its gates failed?
 *
 * @param {Array<{ body?: string|null }>} comments
 * @param {string} head
 * @returns {boolean}
 */
function hasBadComment(comments, head) {
  const marker = badMarker(head);
  return comments.some((c) => (c.body || '').includes(marker));
}

/**
 * Label the queue puts on a PR it cannot merge itself. A human merges
 * it by hand; until the label is removed, Select passes over it so one
 * PR the queue cannot land never blocks the PRs behind it.
 */
const HAND_MERGE_LABEL = 'merge-queue:hand-merge';

/**
 * May the queue take this PR, given what it touches and what its token
 * may write?
 *
 * GitHub refuses (403) to let an App create or update anything under
 * `.github/workflows/` unless the App holds the Workflows permission.
 * Finding that out at `pulls.merge` time, after gating a whole batch
 * with the PR underneath, stalls every PR behind it. Checking the file
 * list up front costs one API call per candidate.
 *
 * @param {{ files: string[], labels: string[], canMergeWorkflows: boolean }} input
 * @returns {{ eligible: boolean, label: boolean, reason: string }}
 */
function queueCanMerge(input) {
  if (input.labels.includes(HAND_MERGE_LABEL)) {
    return {
      eligible: false,
      label: false,
      reason: `labelled ${HAND_MERGE_LABEL} — waiting for a human merge`,
    };
  }
  const workflows = input.files.filter((f) => f.startsWith('.github/workflows/'));
  if (workflows.length > 0 && !input.canMergeWorkflows) {
    return {
      eligible: false,
      label: true,
      reason: `touches ${workflows.join(', ')} and the queue's App cannot write workflows — merge by hand`,
    };
  }
  return { eligible: true, label: false, reason: 'queue can merge it' };
}

/**
 * What does a failed `pulls.merge` mean for this PR and for the pass?
 *
 *   race        — 405 / 409: base or head moved mid-flight. Stop, and
 *                 let the next pass rebuild; nothing is wrong with the PR.
 *   needs-human — 403: GitHub will never let this token merge this PR
 *                 (workflow files without the Workflows permission,
 *                 rules the App cannot bypass). Label it for a human,
 *                 stop, and let the next pass rebuild WITHOUT it.
 *   error       — anything else. Same as needs-human for the PR, but the
 *                 run ends red so someone looks at the queue itself.
 *
 * Every kind stops the loop: later PRs were gated with this one
 * underneath, so landing them without it would land a tree nothing
 * checked. The label is what keeps the next pass from tripping on the
 * same PR — that is how one PR stops being a head-of-line block.
 *
 * @param {{ status: number|undefined, message: string }} input
 * @returns {{ kind: 'race'|'needs-human'|'error', label: boolean, fatal: boolean, reason: string }}
 */
function classifyMergeFailure(input) {
  if (input.status === 405 || input.status === 409) {
    return {
      kind: 'race', label: false, fatal: false,
      reason: `merge raced a moving base or head (${input.status})`,
    };
  }
  if (input.status === 403) {
    return {
      kind: 'needs-human', label: true, fatal: false,
      reason: `GitHub refused the queue's token: ${input.message}`,
    };
  }
  return {
    kind: 'error', label: true, fatal: true,
    reason: `unexpected merge failure (${input.status ?? '?'}): ${input.message}`,
  };
}

/**
 * The comment that tells a PR why the queue handed it to a human.
 *
 * @param {{ n: number, reason: string }} input
 * @returns {{ body: string }}
 */
function handMergeComment(input) {
  return {
    body:
      `The merge queue can't merge #${input.n} itself: ${input.reason}.\n\n` +
      `It is labelled \`${HAND_MERGE_LABEL}\` and the queue skips it from now on. ` +
      'Merge it by hand, or remove the label once the cause is fixed.',
  };
}

/**
 * Select's whole verdict on one open PR, in the order the checks run.
 *
 * Select used to compose isCandidate, queueCanMerge and skipFailedHead
 * inline in the workflow, where only a live run could test the
 * composition. Here it is one pure function, fed by recorded API replies
 * in decisions.test.cjs.
 *
 * @param {{ pr: PullSummary & { labels?: Array<{ name: string }> },
 *           reviews: Review[], files: string[],
 *           comments: Array<{ body?: string|null }>,
 *           canMergeWorkflows: boolean }} input
 * @returns {{ candidate: { n: number, head: string, approvedAt: string|null }|null,
 *             reason: string, label: boolean, warn: boolean }}
 */
function selectDecision(input) {
  const { pr, reviews } = input;
  const base = isCandidate({ pr, reviews });
  if (!base.eligible) {
    // Conflicts are the only refusal a human must act on.
    return { candidate: null, reason: base.reason, label: false, warn: base.reason.includes('CONFLICTS') };
  }
  const labels = (pr.labels || []).map((l) => l.name);
  const access = queueCanMerge({ files: input.files, labels, canMergeWorkflows: input.canMergeWorkflows });
  if (!access.eligible) {
    return { candidate: null, reason: access.reason, label: access.label, warn: false };
  }
  const failed = skipFailedHead({ head: pr.head.sha, labels, comments: input.comments });
  if (failed.skip) {
    return { candidate: null, reason: failed.reason, label: false, warn: false };
  }
  return {
    candidate: { n: pr.number, head: pr.head.sha, approvedAt: approvedAt(reviews) },
    reason: base.reason,
    label: false,
    warn: false,
  };
}

/**
 * REST path of a branch ref, each segment encoded on its own.
 *
 * Octokit encodes a `ref` parameter as ONE path segment, so
 * `heads/feat/x` went out as `heads%2Ffeat%2Fx` and GitHub answered
 * 422 (after #477, the branch stayed; #490). Slashes in a branch name are
 * path separators here and must reach GitHub as `/`.
 *
 * @param {'ref'|'refs'} kind - `ref` to read one ref, `refs` to delete it
 * @param {string} branch - e.g. `feat/148-binance-testnet-spot`
 * @returns {string} e.g. `git/refs/heads/feat/148-binance-testnet-spot`
 */
function branchRefPath(kind, branch) {
  const encoded = branch.split('/').map(encodeURIComponent).join('/');
  return `git/${kind}/heads/${encoded}`;
}

/**
 * What a branch delete attempt amounted to.
 *
 * 404 and 422 mean "no such ref" for a delete, which is what we want
 * when `delete_branch_on_merge` got there first. They count as gone
 * only once a read of the ref confirms it is absent: a 422 from a
 * malformed path also looks like "no such ref", and that is the bug
 * that left merged branches behind.
 *
 * @param {{ deleteStatus: number|undefined, refStillExists: boolean|undefined }} input
 *   `refStillExists` is undefined when it was not (or could not be) checked.
 * @returns {{ outcome: 'deleted'|'already-gone'|'failed', reason: string }}
 */
function classifyBranchDelete(input) {
  const { deleteStatus, refStillExists } = input;
  if (deleteStatus === 204) {
    return { outcome: 'deleted', reason: 'deleted' };
  }
  if ((deleteStatus === 404 || deleteStatus === 422) && refStillExists === false) {
    return { outcome: 'already-gone', reason: `already deleted (${deleteStatus})` };
  }
  if (refStillExists === true) {
    return { outcome: 'failed', reason: `delete refused (${deleteStatus ?? '?'}) and the branch still exists` };
  }
  const unchecked = refStillExists === undefined ? ' and the branch could not be checked' : '';
  return { outcome: 'failed', reason: `delete refused (${deleteStatus ?? '?'})${unchecked}` };
}

/**
 * Label on the one open issue the queue posts its alerts to.
 */
const ALERTS_LABEL = 'merge-queue:alerts';

/**
 * One alert line for the shared alerts issue, so whoever watches it
 * (a person or a Claude session following the repo) sees a red batch
 * without opening each PR: which PR, which branch, which gate or tests.
 *
 * @param {{ n: number, ref: string, head: string, gate: string, base: string }} input
 * @returns {{ body: string }}
 */
function alertComment(input) {
  return {
    body: [
      `${badMarker(input.head)}`,
      `**#${input.n}** (\`${input.ref}\`, head ${input.head.slice(0, 7)}) failed the merge-queue gates on main ${input.base.slice(0, 7)}.`,
      '',
      `Failing: \`${input.gate}\``,
      '',
      'The PR is labelled and skipped until its branch gets a fix.',
    ].join('\n'),
  };
}

module.exports = {
  branchRefPath,
  classifyBranchDelete,
  MERGEABLE_STATES,
  BATCH_CONFLICT_REASON,
  isApproved,
  isCandidate,
  shouldMerge,
  gateSkip,
  ciRestart,
  approvedAt,
  orderCandidates,
  admitHead,
  batchGateSkip,
  bisectInit,
  bisectNext,
  bisectRecord,
  afterMerge,
  badComment,
  hasBadComment,
  HAND_MERGE_LABEL,
  GATES_FAILED_LABEL,
  skipFailedHead,
  queueCanMerge,
  classifyMergeFailure,
  handMergeComment,
  ALERTS_LABEL,
  alertComment,
  selectDecision,
};
