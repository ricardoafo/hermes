/**
 * Unit tests for tools/merge/decisions.cjs.
 *
 * Run: node --test tools/merge/decisions.test.cjs
 *
 * Zero deps: uses the built-in `node:test` runner (Node 20+).
 *
 * Every refusal path is covered because the whole reason this module
 * exists is that untested `script:` blocks shipped decisions nobody
 * asserted against a fixture.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  MERGEABLE_STATES,
  branchRefPath,
  classifyBranchDelete,
  isApproved,
  isMaintainerReview,
  isCandidate,
  shouldMerge,
  gateSkip,
  ciRestart,
  BATCH_CONFLICT_REASON,
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
} = require('./decisions.cjs');

// ============================================================
// Fixtures
// ============================================================

const approval = (login, author_association = 'COLLABORATOR') => ({ user: { login }, state: 'APPROVED', author_association });
const changes = (login, author_association = 'COLLABORATOR') => ({ user: { login }, state: 'CHANGES_REQUESTED', author_association });
const comment = (login, author_association = 'COLLABORATOR') => ({ user: { login }, state: 'COMMENTED', author_association });

const readyPr = (over = {}) => ({
  number: 1,
  draft: false,
  head: { sha: 'aaa', ref: 'feature/x' },
  mergeable: true,
  mergeable_state: 'clean',
  ...over,
});

// ============================================================
// isApproved
// ============================================================

test('isApproved: single approval passes', () => {
  assert.equal(isApproved([approval('alice')]), true);
});

test('isApproved: CHANGES_REQUESTED after APPROVED by same author overrides', () => {
  assert.equal(isApproved([approval('alice'), changes('alice')]), false);
});

test('isApproved: APPROVED after CHANGES_REQUESTED by same author overrides', () => {
  assert.equal(isApproved([changes('alice'), approval('alice')]), true);
});

test('isApproved: COMMENTED reviews are ignored, prior APPROVED still counts', () => {
  assert.equal(isApproved([approval('alice'), comment('alice')]), true);
});

test('isApproved: another author requesting changes does not block an approval', () => {
  assert.equal(isApproved([approval('alice'), changes('bob')]), true);
});

test('isApproved: an approval from someone outside the repository does not count', () => {
  assert.equal(isApproved([approval('drive-by', 'NONE')]), false);
  assert.equal(isApproved([approval('first-timer', 'FIRST_TIME_CONTRIBUTOR')]), false);
  assert.equal(isApproved([approval('past-author', 'CONTRIBUTOR')]), false);
});

test('isApproved: a review without author_association does not count', () => {
  assert.equal(isApproved([{ user: { login: 'alice' }, state: 'APPROVED' }]), false);
});

test('isApproved: an outsider requesting changes does not withdraw a maintainer approval', () => {
  assert.equal(isApproved([approval('owner', 'OWNER'), changes('drive-by', 'NONE')]), true);
});

test('isMaintainerReview: owner, member and collaborator count; everyone else does not', () => {
  for (const a of ['OWNER', 'MEMBER', 'COLLABORATOR']) {
    assert.equal(isMaintainerReview(approval('x', a)), true, a);
  }
  for (const a of ['CONTRIBUTOR', 'FIRST_TIME_CONTRIBUTOR', 'FIRST_TIMER', 'MANNEQUIN', 'NONE']) {
    assert.equal(isMaintainerReview(approval('x', a)), false, a);
  }
});

test('isApproved: empty review list is not approved', () => {
  assert.equal(isApproved([]), false);
});

// ============================================================
// isCandidate — every refusal branch has its own test
// ============================================================

test('isCandidate: clean + approved is a candidate', () => {
  const res = isCandidate({ pr: readyPr(), reviews: [approval('alice')] });
  assert.equal(res.eligible, true);
  assert.match(res.reason, /candidate/);
});

test('isCandidate: draft is refused', () => {
  const res = isCandidate({
    pr: readyPr({ draft: true }),
    reviews: [approval('alice')],
  });
  assert.equal(res.eligible, false);
  assert.match(res.reason, /draft/);
});

test('isCandidate: no approval is refused', () => {
  const res = isCandidate({ pr: readyPr(), reviews: [] });
  assert.equal(res.eligible, false);
  assert.match(res.reason, /not approved/);
});

test('isCandidate: dirty state is refused with a human-in-the-loop reason', () => {
  const res = isCandidate({
    pr: readyPr({ mergeable_state: 'dirty', mergeable: false }),
    reviews: [approval('alice')],
  });
  assert.equal(res.eligible, false);
  assert.match(res.reason, /CONFLICTS/);
});

test('isCandidate: mergeable=false is refused even if state is not dirty', () => {
  const res = isCandidate({
    pr: readyPr({ mergeable: false, mergeable_state: 'unknown' }),
    reviews: [approval('alice')],
  });
  assert.equal(res.eligible, false);
});

test('isCandidate: unstable state passes (non-required check red is fine)', () => {
  // The bug the previous merge queue shipped: it refused `unstable`,
  // stranding PRs where only a non-required check had failed. This
  // asserts the fix.
  const res = isCandidate({
    pr: readyPr({ mergeable_state: 'unstable' }),
    reviews: [approval('alice')],
  });
  assert.equal(res.eligible, true);
  assert.match(res.reason, /unstable/);
});

test('isCandidate: behind state passes (revalidation handles freshness)', () => {
  const res = isCandidate({
    pr: readyPr({ mergeable_state: 'behind' }),
    reviews: [approval('alice')],
  });
  assert.equal(res.eligible, true);
});

test('isCandidate: has_hooks state passes', () => {
  const res = isCandidate({
    pr: readyPr({ mergeable_state: 'has_hooks' }),
    reviews: [approval('alice')],
  });
  assert.equal(res.eligible, true);
});

test('isCandidate: blocked state is refused (usually CI still running)', () => {
  const res = isCandidate({
    pr: readyPr({ mergeable_state: 'blocked' }),
    reviews: [approval('alice')],
  });
  assert.equal(res.eligible, false);
  assert.match(res.reason, /blocked/);
});

test('isCandidate: unknown state is refused (allowlist, not denylist)', () => {
  const res = isCandidate({
    pr: readyPr({ mergeable_state: 'something_new_from_gh' }),
    reviews: [approval('alice')],
  });
  assert.equal(res.eligible, false);
});

test('MERGEABLE_STATES contents are the allowlist we expect', () => {
  assert.deepEqual(
    [...MERGEABLE_STATES].sort(),
    ['behind', 'clean', 'has_hooks', 'unstable'],
  );
});

// ============================================================
// shouldMerge — the re-assertion immediately before pulls.merge
// ============================================================

const readyMerge = (over = {}) => ({
  testedBase: 'base1',
  currentBase: 'base1',
  testedHead: 'head1',
  currentHead: 'head1',
  state: 'open',
  draft: false,
  mergeable: true,
  mergeableState: 'clean',
  reviews: [approval('alice')],
  ...over,
});

test('shouldMerge: everything unchanged and still approved -> merge', () => {
  const res = shouldMerge(readyMerge());
  assert.equal(res.merge, true);
});

test('shouldMerge: base moved during the run -> refuse', () => {
  const res = shouldMerge(readyMerge({ currentBase: 'base2' }));
  assert.equal(res.merge, false);
  assert.match(res.reason, /base moved/);
});

test('shouldMerge: head moved during the run -> refuse', () => {
  const res = shouldMerge(readyMerge({ currentHead: 'head2' }));
  assert.equal(res.merge, false);
  assert.match(res.reason, /head moved/);
});

test('shouldMerge: PR became draft during the run -> refuse', () => {
  const res = shouldMerge(readyMerge({ draft: true }));
  assert.equal(res.merge, false);
});

test('shouldMerge: PR closed during the run -> refuse', () => {
  const res = shouldMerge(readyMerge({ state: 'closed' }));
  assert.equal(res.merge, false);
});

test('shouldMerge: conflicts developed during the run -> refuse', () => {
  const res = shouldMerge(readyMerge({ mergeable: false, mergeableState: 'dirty' }));
  assert.equal(res.merge, false);
  assert.match(res.reason, /CONFLICTS/);
});

test('shouldMerge: approval withdrawn during the run -> refuse', () => {
  const res = shouldMerge(
    readyMerge({ reviews: [approval('alice'), changes('alice')] }),
  );
  assert.equal(res.merge, false);
  assert.match(res.reason, /approval withdrawn/);
});

// ============================================================
// gateSkip — the dangerous decision, so the strictest tests
// ============================================================

test('gateSkip: ancestor + CI success -> skip (fast-forward)', () => {
  const res = gateSkip({ mainIsAncestor: true, ciConclusion: 'success' });
  assert.equal(res.skip, true);
});

test('gateSkip: NOT ancestor -> run gates', () => {
  const res = gateSkip({ mainIsAncestor: false, ciConclusion: 'success' });
  assert.equal(res.skip, false);
  assert.match(res.reason, /not an ancestor/);
});

test('gateSkip: ancestor but CI failed -> run gates', () => {
  const res = gateSkip({ mainIsAncestor: true, ciConclusion: 'failure' });
  assert.equal(res.skip, false);
});

test('gateSkip: ancestor but CI never ran -> run gates ("cannot check" != clean)', () => {
  const res = gateSkip({ mainIsAncestor: true, ciConclusion: null });
  assert.equal(res.skip, false);
  assert.match(res.reason, /nothing/);
});

test('gateSkip: ancestor but CI cancelled -> run gates', () => {
  const res = gateSkip({ mainIsAncestor: true, ciConclusion: 'cancelled' });
  assert.equal(res.skip, false);
});

// ============================================================
// ciRestart — fail-open on any uncertainty
// ============================================================

test('ciRestart: trees byte-identical -> skip the restart', () => {
  const t = 'a'.repeat(40);
  const res = ciRestart({ mergedTree: t, validatedTree: t });
  assert.equal(res.restart, false);
});

test('ciRestart: trees differ -> restart', () => {
  const res = ciRestart({
    mergedTree: 'a'.repeat(40),
    validatedTree: 'b'.repeat(40),
  });
  assert.equal(res.restart, true);
});

test('ciRestart: null mergedTree -> restart', () => {
  const res = ciRestart({ mergedTree: null, validatedTree: 'a'.repeat(40) });
  assert.equal(res.restart, true);
});

test('ciRestart: null validatedTree -> restart', () => {
  const res = ciRestart({ mergedTree: 'a'.repeat(40), validatedTree: null });
  assert.equal(res.restart, true);
});

test('ciRestart: both null -> restart', () => {
  const res = ciRestart({ mergedTree: null, validatedTree: null });
  assert.equal(res.restart, true);
});

// ============================================================
// approvedAt + orderCandidates — the order a batch lands in
// ============================================================

const at = (login, state, submitted_at, author_association = 'COLLABORATOR') => ({
  user: { login },
  state,
  submitted_at,
  author_association,
});

test('approvedAt: the LATEST approval wins, so a re-approval moves the PR back', () => {
  const res = approvedAt([
    at('alice', 'APPROVED', '2026-10-01T10:00:00Z'),
    at('alice', 'COMMENTED', '2026-10-01T12:00:00Z'),
    at('alice', 'APPROVED', '2026-10-01T11:00:00Z'),
  ]);
  assert.equal(res, '2026-10-01T11:00:00Z');
});

test('approvedAt: no approval -> null', () => {
  assert.equal(approvedAt([at('alice', 'CHANGES_REQUESTED', '2026-10-01T10:00:00Z')]), null);
});

test('approvedAt: a later approval from outside the repository does not move the PR', () => {
  const res = approvedAt([
    at('owner', 'APPROVED', '2026-10-01T10:00:00Z', 'OWNER'),
    at('drive-by', 'APPROVED', '2026-10-01T11:00:00Z', 'NONE'),
  ]);
  assert.equal(res, '2026-10-01T10:00:00Z');
});

test('orderCandidates: oldest approval first', () => {
  const res = orderCandidates([
    { n: 1, approvedAt: '2026-10-01T12:00:00Z' },
    { n: 2, approvedAt: '2026-10-01T10:00:00Z' },
    { n: 3, approvedAt: '2026-10-01T11:00:00Z' },
  ]);
  assert.deepEqual(res.map((c) => c.n), [2, 3, 1]);
});

test('orderCandidates: equal approval times fall back to PR number', () => {
  const t = '2026-10-01T10:00:00Z';
  const res = orderCandidates([{ n: 9, approvedAt: t }, { n: 4, approvedAt: t }]);
  assert.deepEqual(res.map((c) => c.n), [4, 9]);
});

test('orderCandidates: unknown approval time sorts last, never first', () => {
  const res = orderCandidates([
    { n: 1, approvedAt: null },
    { n: 2, approvedAt: '2026-10-01T10:00:00Z' },
  ]);
  assert.deepEqual(res.map((c) => c.n), [2, 1]);
});

test('orderCandidates: does not modify its input', () => {
  const input = [{ n: 2, approvedAt: null }, { n: 1, approvedAt: null }];
  orderCandidates(input);
  assert.deepEqual(input.map((c) => c.n), [2, 1]);
});

// ============================================================
// admitHead + batchGateSkip
// ============================================================

test('admitHead: the approved head is admitted', () => {
  assert.equal(admitHead({ approvedHead: 'abc', fetchedHead: 'abc' }).admit, true);
});

test('admitHead: a head that moved after approval is dropped as "head moved"', () => {
  const res = admitHead({ approvedHead: 'abc', fetchedHead: 'def' });
  assert.equal(res.admit, false);
  assert.equal(res.reason, 'head moved');
});

test('admitHead: an unfetchable head is dropped', () => {
  assert.equal(admitHead({ approvedHead: 'abc', fetchedHead: null }).admit, false);
});

test('BATCH_CONFLICT_REASON names the combination, not the PR alone', () => {
  assert.equal(BATCH_CONFLICT_REASON, 'conflicts with main + earlier batch PRs');
});

test('batchGateSkip: a batch of two never skips, even when each head is proven', () => {
  const res = batchGateSkip({ size: 2, mainIsAncestor: true, ciConclusion: 'success' });
  assert.equal(res.skip, false);
  assert.match(res.reason, /batch of 2/);
});

test('batchGateSkip: a batch of one defers to gateSkip (skip when proven)', () => {
  const res = batchGateSkip({ size: 1, mainIsAncestor: true, ciConclusion: 'success' });
  assert.equal(res.skip, true);
});

test('batchGateSkip: a batch of one still gates when CI did not pass', () => {
  const res = batchGateSkip({ size: 1, mainIsAncestor: true, ciConclusion: null });
  assert.equal(res.skip, false);
});

// ============================================================
// bisect planning — driven against a gate oracle
// ============================================================

/**
 * Run the bisect plan to completion. The oracle is green exactly when
 * the tree contains none of `badSet`, which is how a real gate behaves
 * when each bad PR is independently broken.
 */
const simulate = (order, badSet) => {
  const bad = new Set(badSet);
  const trees = [];
  let state = bisectInit(order);
  for (let step = bisectNext(state); !step.done; step = bisectNext(state)) {
    if (step.known) {
      state = bisectRecord(state, step.known);
      continue;
    }
    trees.push(step.tree);
    const culprit = step.tree.find((n) => bad.has(n));
    state = bisectRecord(state, {
      passed: culprit === undefined,
      gate: culprit === undefined ? null : `pytest (#${culprit})`,
    });
  }
  return { state, trees };
};

test('bisect: an empty batch gates nothing', () => {
  const { state, trees } = simulate([], []);
  assert.deepEqual(trees, []);
  assert.deepEqual(state.good, []);
  assert.deepEqual(state.bad, []);
});

test('bisect: an all-good batch costs exactly one gate run', () => {
  const { state, trees } = simulate([1, 2, 3, 4, 5, 6], []);
  assert.deepEqual(trees, [[1, 2, 3, 4, 5, 6]]);
  assert.deepEqual(state.good, [1, 2, 3, 4, 5, 6]);
  assert.deepEqual(state.bad, []);
});

test('bisect: a batch of one that fails marks that PR bad, with its gate', () => {
  const { state, trees } = simulate([7], [7]);
  assert.deepEqual(trees, [[7]]);
  assert.deepEqual(state.good, []);
  assert.deepEqual(state.bad, [{ n: 7, gate: 'pytest (#7)' }]);
});

test('bisect: one bad among six is isolated; the other five stay good in order', () => {
  const { state, trees } = simulate([1, 2, 3, 4, 5, 6], [5]);
  assert.deepEqual(state.good, [1, 2, 3, 4, 6]);
  assert.deepEqual(state.bad, [{ n: 5, gate: 'pytest (#5)' }]);
  // The first half is settled before the second is tried, every tree
  // built after that keeps the proven-good PRs underneath, and a tree
  // already gated red is never gated again.
  assert.deepEqual(trees, [
    [1, 2, 3, 4, 5, 6],
    [1, 2, 3],
    [1, 2, 3, 4, 5],
    [1, 2, 3, 4],
    [1, 2, 3, 4, 6],
  ]);
});

test('bisect: two bad PRs are both isolated', () => {
  const { state } = simulate([1, 2, 3, 4, 5, 6], [2, 6]);
  assert.deepEqual(state.good, [1, 3, 4, 5]);
  assert.deepEqual(state.bad.map((b) => b.n), [2, 6]);
});

test('bisect: every PR bad -> nothing good, every PR named', () => {
  const { state } = simulate([1, 2, 3], [1, 2, 3]);
  assert.deepEqual(state.good, []);
  assert.deepEqual(state.bad.map((b) => b.n), [1, 2, 3]);
});

test('bisect: every gated tree is a prefix-ordered subset of the batch', () => {
  const order = [11, 4, 9, 2, 30, 7, 5];
  const { trees } = simulate(order, [9, 7]);
  for (const tree of trees) {
    const positions = tree.map((n) => order.indexOf(n));
    assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
  }
});

test('bisectNext: a tree already gated red comes back as known, with its gate', () => {
  let state = bisectInit([1, 2]);
  state = bisectRecord(state, { passed: false, gate: 'ruff check' });
  state = bisectRecord(state, { passed: true, gate: null });
  const step = bisectNext(state);
  assert.deepEqual(step.tree, [1, 2]);
  assert.deepEqual(step.known, { passed: false, gate: 'ruff check' });
});

test('bisectNext: a fresh tree is not known', () => {
  assert.equal(bisectNext(bisectInit([1, 2])).known, null);
});

test('bisectRecord: does not modify the state it is given', () => {
  const state = bisectInit([1, 2]);
  bisectRecord(state, { passed: false, gate: 'ruff check' });
  assert.deepEqual(state.pending, [[1, 2]]);
});

test('bisectRecord: a red verdict with no gate name still names something', () => {
  const state = bisectRecord(bisectInit([3]), { passed: false, gate: null });
  assert.deepEqual(state.bad, [{ n: 3, gate: 'unknown gate' }]);
});

test('bisectRecord: refuses to record when nothing is pending', () => {
  assert.throws(() => bisectRecord(bisectInit([]), { passed: true, gate: null }));
});

// ============================================================
// afterMerge — the re-dispatch must not loop on a red batch
// ============================================================

test('afterMerge: merged something -> re-dispatch', () => {
  assert.equal(afterMerge({ dryRun: false, merged: 2, stoppedEarly: false }).redispatch, true);
});

test('afterMerge: stopped early (main moved) -> re-dispatch even with nothing merged', () => {
  assert.equal(afterMerge({ dryRun: false, merged: 0, stoppedEarly: true }).redispatch, true);
});

test('afterMerge: merged nothing -> no re-dispatch (would rerun the same red batch)', () => {
  const res = afterMerge({ dryRun: false, merged: 0, stoppedEarly: false });
  assert.equal(res.redispatch, false);
  assert.match(res.reason, /merged nothing/);
});

test('afterMerge: dry run never re-dispatches', () => {
  assert.equal(afterMerge({ dryRun: true, merged: 3, stoppedEarly: true }).redispatch, false);
});

// ============================================================
// badComment + hasBadComment — one comment per head
// ============================================================

test('badComment: names the gate and the tested head, behind a head marker', () => {
  const head = 'f'.repeat(40);
  const { body, marker } = badComment({ n: 12, head, gate: 'mypy', base: 'a'.repeat(40) });
  assert.equal(marker, `<!-- merge-queue-bad:${head} -->`);
  assert.ok(body.startsWith(marker));
  assert.match(body, /`mypy` gate failed/);
  assert.match(body, /head fffffff/);
});

test('hasBadComment: finds the marker for the same head', () => {
  const head = 'b'.repeat(40);
  const { body } = badComment({ n: 1, head, gate: 'pytest', base: 'c'.repeat(40) });
  assert.equal(hasBadComment([{ body: 'lgtm' }, { body }], head), true);
});

test('hasBadComment: a comment for an older head does not count', () => {
  const { body } = badComment({ n: 1, head: 'old', gate: 'pytest', base: 'c' });
  assert.equal(hasBadComment([{ body }, { body: null }], 'new'), false);
});

// ============================================================
// queueCanMerge — workflow files and the hand-merge label
// ============================================================

const {
  HAND_MERGE_LABEL, queueCanMerge, classifyMergeFailure, handMergeComment,
} = require('./decisions.cjs');

test('queueCanMerge: a PR touching workflows is labelled for a human when the App cannot write them', () => {
  const res = queueCanMerge({
    files: ['src/a.py', '.github/workflows/cd.yml'], labels: [], canMergeWorkflows: false,
  });
  assert.equal(res.eligible, false);
  assert.equal(res.label, true);
  assert.match(res.reason, /\.github\/workflows\/cd\.yml/);
});

test('queueCanMerge: workflow changes are fine once the App may write workflows', () => {
  const res = queueCanMerge({
    files: ['.github/workflows/cd.yml'], labels: [], canMergeWorkflows: true,
  });
  assert.equal(res.eligible, true);
});

test('queueCanMerge: a PR already labelled for a human is skipped without relabelling', () => {
  const res = queueCanMerge({ files: ['src/a.py'], labels: [HAND_MERGE_LABEL], canMergeWorkflows: true });
  assert.deepEqual([res.eligible, res.label], [false, false]);
});

test('queueCanMerge: an ordinary PR is eligible', () => {
  const res = queueCanMerge({ files: ['src/a.py', 'docs/x.md'], labels: ['type:feat'], canMergeWorkflows: false });
  assert.equal(res.eligible, true);
});

// ============================================================
// classifyMergeFailure — one PR's failure must not block the queue
// ============================================================

test('classifyMergeFailure: 405 and 409 are races, no label, not fatal', () => {
  for (const status of [405, 409]) {
    const res = classifyMergeFailure({ status, message: 'x' });
    assert.deepEqual([res.kind, res.label, res.fatal], ['race', false, false]);
  }
});

test('classifyMergeFailure: 403 labels the PR for a human and keeps the run green', () => {
  const res = classifyMergeFailure({
    status: 403,
    message: 'refusing to allow a GitHub App to create or update workflow `.github/workflows/cd.yml` without `workflows` permission',
  });
  assert.deepEqual([res.kind, res.label, res.fatal], ['needs-human', true, false]);
  assert.match(res.reason, /workflows/);
});

test('classifyMergeFailure: anything else labels the PR and turns the run red', () => {
  const res = classifyMergeFailure({ status: 500, message: 'boom' });
  assert.deepEqual([res.kind, res.label, res.fatal], ['error', true, true]);
});

test('classifyMergeFailure: a missing status is an error, never a race', () => {
  assert.equal(classifyMergeFailure({ status: undefined, message: 'network' }).kind, 'error');
});

test('handMergeComment: names the reason and the label to remove', () => {
  const { body } = handMergeComment({ n: 431, reason: 'no workflows permission' });
  assert.match(body, /#431/);
  assert.match(body, /no workflows permission/);
  assert.ok(body.includes(HAND_MERGE_LABEL));
});


// ============================================================
// skipFailedHead — a head that failed the gates is not re-gated
// ============================================================

const { GATES_FAILED_LABEL, skipFailedHead } = require('./decisions.cjs');

test('skipFailedHead: a labelled head that was told it failed is skipped', () => {
  const head = 'a'.repeat(40);
  const { body } = badComment({ n: 435, head, gate: 'pytest: tests/x.py::test_y', base: 'b'.repeat(40) });
  const res = skipFailedHead({ head, labels: [GATES_FAILED_LABEL], comments: [{ body }] });
  assert.equal(res.skip, true);
  assert.match(res.reason, /aaaaaaa/);
});

test('skipFailedHead: a new push is a new head and is tried again', () => {
  const { body } = badComment({ n: 435, head: 'old', gate: 'pytest', base: 'b' });
  const res = skipFailedHead({ head: 'new', labels: [GATES_FAILED_LABEL], comments: [{ body }] });
  assert.equal(res.skip, false);
});

test('skipFailedHead: removing the label retries the same head', () => {
  const head = 'c'.repeat(40);
  const { body } = badComment({ n: 1, head, gate: 'mypy', base: 'd' });
  assert.equal(skipFailedHead({ head, labels: [], comments: [{ body }] }).skip, false);
});

test('badComment: tells the writer the head will not be retried and how to retry', () => {
  const { body } = badComment({ n: 9, head: 'e'.repeat(40), gate: 'pytest: tests/a.py::t', base: 'f'.repeat(40) });
  assert.match(body, /will not try this head again/);
  assert.ok(body.includes(GATES_FAILED_LABEL));
  assert.match(body, /tests\/a\.py::t/);
});

// ============================================================
// selectDecision — Select's whole verdict, on recorded API replies
// ============================================================

const { selectDecision } = require('./decisions.cjs');

// Recorded from the GitHub API on 2026-10-04 (see each file's _source):
// #471 open, unreviewed, `blocked`; #467 open, approved, `blocked`.
const recorded = (n) => {
  const raw = JSON.parse(
    require('node:fs').readFileSync(require('node:path').join(__dirname, 'fixtures', `pr-${n}-recorded.json`), 'utf8'),
  );
  return {
    pr: raw.pull,
    reviews: raw.reviews,
    files: raw.files.map((f) => f.filename),
    comments: raw.comments,
    canMergeWorkflows: false,
  };
};
// The same recorded PR once its required check went green.
const green = (input) => ({ ...input, pr: { ...input.pr, mergeable_state: 'clean' } });

test('selectDecision: an unreviewed recorded PR is not a candidate', () => {
  const res = selectDecision(recorded(471));
  assert.equal(res.candidate, null);
  assert.match(res.reason, /not approved/);
  assert.equal(res.label, false);
});

test('selectDecision: an approved recorded PR with red or pending CI (blocked) waits', () => {
  const res = selectDecision(recorded(467));
  assert.equal(res.candidate, null);
  assert.match(res.reason, /blocked/);
});

test('selectDecision: the approved recorded PR becomes a candidate once green, carrying head and approval time', () => {
  const input = green(recorded(467));
  const res = selectDecision(input);
  assert.deepEqual(res.candidate, {
    n: 467,
    head: input.pr.head.sha,
    approvedAt: input.reviews.find((r) => r.state === 'APPROVED').submitted_at,
  });
});

test('selectDecision: a green, approved PR touching workflows is handed to a human while the App cannot write them', () => {
  const input = green(recorded(467));
  const res = selectDecision({ ...input, files: [...input.files, '.github/workflows/cd.yml'] });
  assert.equal(res.candidate, null);
  assert.equal(res.label, true);
  assert.match(res.reason, /\.github\/workflows\/cd\.yml/);
});

test('selectDecision: a head whose gates already failed is skipped until it changes', () => {
  const input = green(recorded(467));
  const { body } = badComment({ n: 467, head: input.pr.head.sha, gate: 'pytest', base: 'a'.repeat(40) });
  const res = selectDecision({
    ...input,
    pr: { ...input.pr, labels: [...input.pr.labels, { name: GATES_FAILED_LABEL }] },
    comments: [...input.comments, { body }],
  });
  assert.equal(res.candidate, null);
  assert.match(res.reason, /already failed/);
});

test('selectDecision: conflicts are the one refusal logged as a warning', () => {
  const input = recorded(467);
  const res = selectDecision({ ...input, pr: { ...input.pr, mergeable: false, mergeable_state: 'dirty' } });
  assert.equal(res.candidate, null);
  assert.equal(res.warn, true);
});

test('selectDecision + orderCandidates: green approved PRs come out oldest approval first', () => {
  const base = green(recorded(467));
  const at = (iso) => [{ user: { login: 'ricardoafo' }, state: 'APPROVED', submitted_at: iso, author_association: 'OWNER' }];
  const picks = [
    { ...base, pr: { ...base.pr, number: 3 }, reviews: at('2026-10-04T03:00:00Z') },
    { ...base, pr: { ...base.pr, number: 1 }, reviews: at('2026-10-04T01:00:00Z') },
    { ...base, pr: { ...base.pr, number: 2 }, reviews: at('2026-10-04T02:00:00Z') },
  ].map((i) => selectDecision(i).candidate);
  assert.deepEqual(orderCandidates(picks).map((c) => c.n), [1, 2, 3]);
});

test('branchRefPath: slashes in a branch name stay path separators', () => {
  assert.equal(
    branchRefPath('refs', 'feat/148-binance-testnet-spot'),
    'git/refs/heads/feat/148-binance-testnet-spot',
  );
  assert.equal(branchRefPath('ref', 'feat/148-x'), 'git/ref/heads/feat/148-x');
});

test('branchRefPath: other reserved characters inside a segment are encoded', () => {
  assert.equal(branchRefPath('refs', 'fix/a#b?c'), 'git/refs/heads/fix/a%23b%3Fc');
});

test('classifyBranchDelete: 204 is a delete', () => {
  assert.equal(classifyBranchDelete({ deleteStatus: 204, refStillExists: undefined }).outcome, 'deleted');
});

test('classifyBranchDelete: 404/422 with the ref confirmed absent is already gone', () => {
  for (const deleteStatus of [404, 422]) {
    assert.equal(classifyBranchDelete({ deleteStatus, refStillExists: false }).outcome, 'already-gone');
  }
});

test('classifyBranchDelete: 422 while the branch still exists is a failure, not "already gone"', () => {
  const r = classifyBranchDelete({ deleteStatus: 422, refStillExists: true });
  assert.equal(r.outcome, 'failed');
  assert.match(r.reason, /still exists/);
});

test('classifyBranchDelete: an unchecked ref is never assumed gone', () => {
  assert.equal(classifyBranchDelete({ deleteStatus: 404, refStillExists: undefined }).outcome, 'failed');
});

test('classifyBranchDelete: 403 is a failure even if the ref is absent', () => {
  assert.equal(classifyBranchDelete({ deleteStatus: 403, refStillExists: false }).outcome, 'failed');
});

// ============================================================
// alertComment — one line per red head on the shared alerts issue
// ============================================================

const { ALERTS_LABEL, alertComment } = require('./decisions.cjs');

test('alertComment: names the PR, branch, head, failing tests and base', () => {
  const head = 'a'.repeat(40);
  const { body } = alertComment({
    n: 435, ref: 'feat/386-trade-eur-values', head, gate: 'pytest: tests/x.py::test_y', base: 'b'.repeat(40),
  });
  assert.match(body, /#435/);
  assert.match(body, /feat\/386-trade-eur-values/);
  assert.match(body, /tests\/x\.py::test_y/);
  assert.match(body, /aaaaaaa/);
  assert.match(body, /bbbbbbb/);
});

test('alertComment: carries the per-head marker, so the same head is alerted once', () => {
  const head = 'c'.repeat(40);
  const { body } = alertComment({ n: 1, ref: 'x', head, gate: 'mypy', base: 'd' });
  assert.equal(hasBadComment([{ body }], head), true);
  assert.equal(ALERTS_LABEL, 'merge-queue:alerts');
});
