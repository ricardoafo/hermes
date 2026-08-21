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
  isApproved,
  isCandidate,
  shouldMerge,
  gateSkip,
  ciRestart,
} = require('./decisions.cjs');

// ============================================================
// Fixtures
// ============================================================

const approval = (login) => ({ user: { login }, state: 'APPROVED' });
const changes = (login) => ({ user: { login }, state: 'CHANGES_REQUESTED' });
const comment = (login) => ({ user: { login }, state: 'COMMENTED' });

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
