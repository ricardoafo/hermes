/**
 * Builds and gates ONE batch tree for `merge-queue.yml`.
 *
 * ## What a pass does
 *
 *   1. Reset to the `main` tip Select resolved.
 *   2. Merge each candidate's approved head, in approval order, with a
 *      real `--no-ff` merge commit. A head that moved or a merge that
 *      conflicts is dropped from this pass with a reason.
 *   3. Gate the result once. Green: every admitted PR is mergeable.
 *   4. Red with more than one PR: bisect (`bisectInit` / `bisectNext` /
 *      `bisectRecord` in `decisions.cjs`) until each PR is proven good
 *      or bad.
 *
 * ## Why the I/O is injected
 *
 * Every branching decision lives in `decisions.cjs`. What remains here is
 * the loop that feeds those decisions, and it takes its git and gate
 * operations as an `ops` object, so `batch.test.cjs` drives it with a
 * fake repository in milliseconds instead of a runner in minutes.
 * `gitOps` is the real implementation the workflow passes in.
 */

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');

const {
  BATCH_CONFLICT_REASON,
  admitHead,
  batchGateSkip,
  bisectInit,
  bisectNext,
  bisectRecord,
} = require('./decisions.cjs');

/**
 * @typedef {{ n: number, head: string, approvedAt?: string|null }} Candidate
 *
 * @typedef {{
 *   reset: () => void,
 *   fetchHead: (n: number) => string|null,
 *   merge: (n: number) => boolean,
 *   tree: () => string,
 *   containsBase: (n: number) => boolean,
 *   gate: () => { passed: boolean, gate: string|null },
 *   ciConclusion: (head: string) => Promise<string|null>,
 * }} BatchOps
 *
 * @typedef {{
 *   base: string,
 *   merge: Array<{ n: number, head: string }>,
 *   dropped: Array<{ n: number, reason: string }>,
 *   bad: Array<{ n: number, head: string, gate: string }>,
 *   steps: Array<{ n: number, tree: string }>,
 *   finalTree: string|null,
 *   gated: boolean,
 *   gateRuns: number,
 *   fastPath: string|null,
 * }} BatchResult
 */

/**
 * Gate name recorded when a bisect step cannot even build its tree.
 *
 * Every PR merged cleanly into the full batch, but bisect builds trees
 * WITHOUT some of them. A PR stacked on a sibling that turned out bad
 * can conflict once that sibling is left out; it is red for this pass.
 */
const BISECT_CONFLICT_GATE = 'merge (conflicts without the PRs set aside)';

/**
 * Build the batch, gate it, bisect if red.
 *
 * @param {{ base: string, candidates: Candidate[], ops: BatchOps,
 *           log?: (line: string) => void }} input
 * @returns {Promise<BatchResult>}
 */
async function runBatch({ base, candidates, ops, log = () => {} }) {
  /** @type {BatchResult} */
  const result = {
    base,
    merge: [],
    dropped: [],
    bad: [],
    steps: [],
    finalTree: null,
    gated: false,
    gateRuns: 0,
    fastPath: null,
  };

  ops.reset();
  /** @type {Candidate[]} */
  const admitted = [];
  for (const c of candidates) {
    const admission = admitHead({ approvedHead: c.head, fetchedHead: ops.fetchHead(c.n) });
    if (!admission.admit) {
      result.dropped.push({ n: c.n, reason: admission.reason });
      log(`#${c.n}: dropped — ${admission.reason}`);
      continue;
    }
    if (!ops.merge(c.n)) {
      result.dropped.push({ n: c.n, reason: BATCH_CONFLICT_REASON });
      log(`#${c.n}: dropped — ${BATCH_CONFLICT_REASON}`);
      continue;
    }
    admitted.push(c);
    const tree = ops.tree();
    result.steps.push({ n: c.n, tree });
    log(`#${c.n}: merged into the batch — tree ${tree}`);
  }

  if (admitted.length === 0) {
    log('batch is empty — nothing to gate');
    return result;
  }

  const headOf = new Map(admitted.map((c) => [c.n, c.head]));
  const only = admitted.length === 1 ? admitted[0] : null;
  const skip = batchGateSkip({
    size: admitted.length,
    mainIsAncestor: only ? ops.containsBase(only.n) : false,
    ciConclusion: only ? await ops.ciConclusion(only.head) : null,
  });
  log(`${skip.skip ? 'SKIPPING' : 'running'} gates — ${skip.reason}`);
  if (skip.skip) {
    result.fastPath = skip.reason;
    result.merge = admitted.map((c) => ({ n: c.n, head: c.head }));
    result.finalTree = ops.tree();
    return result;
  }

  result.gated = true;
  let state = bisectInit(admitted.map((c) => c.n));
  let built = state.order.join(',');
  let goodTree = null;
  for (let step = bisectNext(state); !step.done; step = bisectNext(state)) {
    log(`${step.reason}: ${step.set.map((n) => '#' + n).join(', ')}`);
    const key = step.tree.join(',');
    if (step.known === null && key !== built) {
      ops.reset();
      built = step.tree.every((n) => ops.merge(n)) ? key : null;
    }
    let verdict;
    if (step.known !== null) {
      verdict = step.known;
    } else if (built === key) {
      result.gateRuns += 1;
      verdict = ops.gate();
    } else {
      verdict = { passed: false, gate: BISECT_CONFLICT_GATE };
    }
    if (verdict.passed) {
      goodTree = ops.tree();
    }
    log(`  -> ${verdict.passed ? 'green' : `red (${verdict.gate})`}`);
    state = bisectRecord(state, verdict);
  }

  result.merge = state.good.map((n) => ({ n, head: headOf.get(n) }));
  result.bad = state.bad.map((b) => ({ n: b.n, head: headOf.get(b.n), gate: b.gate }));
  result.finalTree = state.good.length ? goodTree : null;
  return result;
}

/**
 * The real git + gate operations, run in the checked-out workspace.
 *
 * The gates script and this module are passed in as copies pinned from
 * `main` before the first reset: once a batch is checked out, the
 * workspace holds the PRs' versions of every file, including these.
 *
 * @param {{ cwd: string, base: string, gatesScript: string, failFile: string,
 *           ciConclusion: (head: string) => Promise<string|null> }} input
 * @returns {BatchOps}
 */
function gitOps({ cwd, base, gatesScript, failFile, ciConclusion }) {
  const git = (args) => spawnSync('git', args, { cwd, encoding: 'utf8' });
  const must = (args) => {
    const r = git(args);
    if (r.status !== 0) {
      throw new Error(`git ${args.join(' ')} failed: ${r.stderr}`);
    }
    return r.stdout.trim();
  };
  const ref = (n) => `refs/heads/batch-pr-${n}`;
  return {
    reset() {
      must(['reset', '--hard', '--quiet', base]);
      // Untracked leftovers of a previous tree must not leak into the next
      // one. Ignored files (the virtualenv, caches) survive on purpose, so
      // each bisect step does not reinstall every dependency.
      must(['clean', '-fd', '--quiet']);
    },
    fetchHead(n) {
      const r = git(['fetch', '--no-tags', '--force', 'origin', `refs/pull/${n}/head:${ref(n)}`]);
      return r.status === 0 ? must(['rev-parse', ref(n)]) : null;
    },
    merge(n) {
      const r = git([
        '-c', 'user.name=revalidate', '-c', 'user.email=revalidate@invalid',
        'merge', '--no-ff', '--no-edit', '-m', `batch: #${n}`, ref(n),
      ]);
      if (r.status !== 0) {
        git(['merge', '--abort']);
        return false;
      }
      return true;
    },
    tree() {
      return must(['rev-parse', 'HEAD^{tree}']);
    },
    containsBase(n) {
      return git(['merge-base', '--is-ancestor', base, ref(n)]).status === 0;
    },
    gate() {
      fs.writeFileSync(failFile, '');
      const r = spawnSync('bash', [gatesScript, failFile, base], { cwd, stdio: 'inherit' });
      if (r.status === 0) {
        return { passed: true, gate: null };
      }
      const named = fs.existsSync(failFile) ? fs.readFileSync(failFile, 'utf8').trim() : '';
      return { passed: false, gate: named || `gates script exited ${r.status}` };
    },
    ciConclusion,
  };
}

module.exports = { BISECT_CONFLICT_GATE, runBatch, gitOps };
