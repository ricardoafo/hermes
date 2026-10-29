/**
 * Unit tests for tools/merge/batch.cjs.
 *
 * Run: node --test tools/merge/batch.test.cjs
 *
 * The orchestration is driven against a fake repository: a list of the
 * PRs merged on top of `main`, a set of heads, and a gate oracle. Every
 * test asserts on the result the workflow's merge step consumes.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const { BISECT_CONFLICT_GATE, gateEnv, runBatch, gitOps } = require('./batch.cjs');
const { BATCH_CONFLICT_REASON } = require('./decisions.cjs');

/**
 * A fake repository.
 *
 * `heads` maps PR number to the head the remote holds now. `conflicts`
 * maps a PR to the PRs whose presence it conflicts with (an empty list
 * means it conflicts with main itself); `needs` maps a PR to a PR it is
 * stacked on, so merging it WITHOUT that one conflicts. The gate is red
 * whenever the tree holds a PR in `bad`.
 */
const fakeRepo = ({
  heads, bad = [], conflicts = {}, needs = {}, ancestor = false, ci = null,
}) => {
  let tree = [];
  const gated = [];
  const ops = {
    reset() {
      tree = [];
    },
    fetchHead(n) {
      return heads[n] ?? null;
    },
    merge(n) {
      const clash = conflicts[n];
      if (clash && (clash.length === 0 || clash.some((m) => tree.includes(m)))) {
        return false;
      }
      if (needs[n] !== undefined && !tree.includes(needs[n])) {
        return false;
      }
      tree.push(n);
      return true;
    },
    tree() {
      return `tree(${tree.join(',')})`;
    },
    containsBase() {
      return ancestor;
    },
    gate() {
      gated.push([...tree]);
      const culprit = tree.find((n) => bad.includes(n));
      return culprit === undefined
        ? { passed: true, gate: null }
        : { passed: false, gate: `pytest (#${culprit})` };
    },
    async ciConclusion() {
      return ci;
    },
  };
  return { ops, gated };
};

const cands = (...ns) => ns.map((n) => ({ n, head: `h${n}` }));
const headsOf = (...ns) => Object.fromEntries(ns.map((n) => [n, `h${n}`]));

test('runBatch: six green PRs gate once and all merge, in order', async () => {
  const { ops, gated } = fakeRepo({ heads: headsOf(1, 2, 3, 4, 5, 6) });
  const res = await runBatch({ base: 'm', candidates: cands(1, 2, 3, 4, 5, 6), ops });
  assert.equal(res.gated, true);
  assert.equal(res.gateRuns, 1);
  assert.deepEqual(gated, [[1, 2, 3, 4, 5, 6]]);
  assert.deepEqual(res.merge.map((m) => m.n), [1, 2, 3, 4, 5, 6]);
  assert.equal(res.finalTree, 'tree(1,2,3,4,5,6)');
  assert.deepEqual(res.steps.map((s) => s.tree), [
    'tree(1)', 'tree(1,2)', 'tree(1,2,3)', 'tree(1,2,3,4)', 'tree(1,2,3,4,5)', 'tree(1,2,3,4,5,6)',
  ]);
});

test('runBatch: a moved head is dropped as "head moved" and never merged in', async () => {
  const { ops, gated } = fakeRepo({ heads: { 1: 'h1', 2: 'pushed-after-approval' } });
  const res = await runBatch({ base: 'm', candidates: cands(1, 2), ops });
  assert.deepEqual(res.dropped, [{ n: 2, reason: 'head moved' }]);
  assert.deepEqual(gated, [[1]]);
  assert.deepEqual(res.merge.map((m) => m.n), [1]);
});

test('runBatch: a PR that conflicts with an earlier batch PR is dropped', async () => {
  const { ops } = fakeRepo({ heads: headsOf(1, 2, 3), conflicts: { 3: [1] } });
  const res = await runBatch({ base: 'm', candidates: cands(1, 2, 3), ops });
  assert.deepEqual(res.dropped, [{ n: 3, reason: BATCH_CONFLICT_REASON }]);
  assert.deepEqual(res.merge.map((m) => m.n), [1, 2]);
});

test('runBatch: one bad PR among six is isolated; the five good ones merge', async () => {
  const { ops } = fakeRepo({ heads: headsOf(1, 2, 3, 4, 5, 6), bad: [5] });
  const res = await runBatch({ base: 'm', candidates: cands(1, 2, 3, 4, 5, 6), ops });
  assert.deepEqual(res.merge.map((m) => m.n), [1, 2, 3, 4, 6]);
  assert.deepEqual(res.bad, [{ n: 5, head: 'h5', gate: 'pytest (#5)' }]);
  assert.equal(res.finalTree, 'tree(1,2,3,4,6)');
  assert.equal(res.gateRuns, 5);
});

test('runBatch: finalTree is the tree of main + the good PRs, even when the last step was red', async () => {
  const { ops } = fakeRepo({ heads: headsOf(1, 2), bad: [2] });
  const res = await runBatch({ base: 'm', candidates: cands(1, 2), ops });
  assert.deepEqual(res.merge.map((m) => m.n), [1]);
  assert.equal(res.finalTree, 'tree(1)');
});

test('runBatch: a PR stacked on a bad sibling is red once the sibling is set aside', async () => {
  const { ops } = fakeRepo({ heads: headsOf(1, 2, 3), bad: [2], needs: { 3: 2 } });
  const res = await runBatch({ base: 'm', candidates: cands(1, 2, 3), ops });
  assert.deepEqual(res.merge.map((m) => m.n), [1]);
  assert.deepEqual(res.bad.map((b) => [b.n, b.gate]), [
    [2, 'pytest (#2)'],
    [3, BISECT_CONFLICT_GATE],
  ]);
});

test('runBatch: every PR bad -> nothing merges and finalTree is null', async () => {
  const { ops } = fakeRepo({ heads: headsOf(1, 2), bad: [1, 2] });
  const res = await runBatch({ base: 'm', candidates: cands(1, 2), ops });
  assert.deepEqual(res.merge, []);
  assert.equal(res.finalTree, null);
  assert.deepEqual(res.bad.map((b) => b.n), [1, 2]);
});

test('runBatch: a batch of one whose head contains main and passed CI skips the gates', async () => {
  const { ops, gated } = fakeRepo({ heads: headsOf(4), ancestor: true, ci: 'success' });
  const res = await runBatch({ base: 'm', candidates: cands(4), ops });
  assert.equal(res.gated, false);
  assert.match(res.fastPath, /CI validated this exact tree/);
  assert.deepEqual(gated, []);
  assert.deepEqual(res.merge, [{ n: 4, head: 'h4' }]);
  assert.equal(res.finalTree, 'tree(4)');
});

test('runBatch: a batch of one without a green CI run is gated like today', async () => {
  const { ops, gated } = fakeRepo({ heads: headsOf(4), ancestor: true, ci: 'failure' });
  const res = await runBatch({ base: 'm', candidates: cands(4), ops });
  assert.equal(res.gated, true);
  assert.equal(res.fastPath, null);
  assert.deepEqual(gated, [[4]]);
});

test('runBatch: a batch of one that fails its gates is bad, not merged', async () => {
  const { ops } = fakeRepo({ heads: headsOf(4), bad: [4] });
  const res = await runBatch({ base: 'm', candidates: cands(4), ops });
  assert.deepEqual(res.merge, []);
  assert.deepEqual(res.bad, [{ n: 4, head: 'h4', gate: 'pytest (#4)' }]);
});

test('runBatch: two proven heads together are never fast-pathed', async () => {
  const { ops, gated } = fakeRepo({ heads: headsOf(1, 2), ancestor: true, ci: 'success' });
  const res = await runBatch({ base: 'm', candidates: cands(1, 2), ops });
  assert.equal(res.gated, true);
  assert.deepEqual(gated, [[1, 2]]);
});

test('runBatch: no candidates -> nothing gated, nothing to merge', async () => {
  const { ops, gated } = fakeRepo({ heads: {} });
  const res = await runBatch({ base: 'm', candidates: [], ops });
  assert.deepEqual(gated, []);
  assert.deepEqual(res.merge, []);
  assert.equal(res.gated, false);
});

test('gitOps: exposes every operation runBatch needs', () => {
  const ops = gitOps({
    cwd: '.', base: 'x', gatesScript: 'g.sh', failFile: 'f', ciConclusion: async () => null,
  });
  assert.deepEqual(
    Object.keys(ops).sort(),
    ['ciConclusion', 'containsBase', 'fetchHead', 'gate', 'merge', 'reset', 'tree'],
  );
});

test('gateEnv: drops every token the step carries and keeps the rest', () => {
  const env = gateEnv({
    'INPUT_GITHUB-TOKEN': 'ghs_x',
    ACTIONS_RUNTIME_TOKEN: 'r',
    ACTIONS_ID_TOKEN_REQUEST_TOKEN: 'i',
    GITHUB_TOKEN: 'g',
    gh_token: 'lower',
    PATH: '/usr/bin',
    HOME: '/home/runner',
    UV_CACHE_DIR: '/cache',
  });
  assert.deepEqual(env, { PATH: '/usr/bin', HOME: '/home/runner', UV_CACHE_DIR: '/cache' });
});
