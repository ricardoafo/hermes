/**
 * Consistency tests for .github/workflows/merge-queue.yml.
 *
 * Run: node --test tools/merge/workflow.test.cjs
 *
 * The no-op concurrency group (#485) and Select's `if:` must describe
 * the same events from opposite sides: an event Select skips must not
 * take the single pending slot of `revalidate-and-merge`, and an event
 * Select runs must queue there. Both live in YAML, so this test lifts
 * the two expressions out and evaluates them over every trigger shape.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const WORKFLOW = fs.readFileSync(
  path.join(__dirname, '..', '..', '.github', 'workflows', 'merge-queue.yml'),
  'utf8',
);

/** Folded YAML scalar that follows `key: >-` at the given indent. */
function foldedScalar(key, indent) {
  const lines = WORKFLOW.split('\n');
  const start = lines.findIndex((l) => l === `${' '.repeat(indent)}${key}: >-`);
  assert.notEqual(start, -1, `no folded "${key}:" at indent ${indent}`);
  const body = [];
  for (const line of lines.slice(start + 1)) {
    if (line.trim() !== '' && !line.startsWith(' '.repeat(indent + 2))) break;
    body.push(line.trim());
  }
  return body.join(' ');
}

/** Evaluate the GitHub-expression subset used here (==, !=, &&, ||, parens). */
function evaluate(expr, github) {
  const js = expr.replace(/(!|=)=/g, '$1==').replace(/\bgithub\./g, 'github?.');
  // eslint-disable-next-line no-new-func
  return Boolean(new Function('github', `return (${js});`)(github));
}

const groupExpr = foldedScalar('group', 2);
const selectIf = foldedScalar('if', 4);
const noopCondition = groupExpr.match(/^\$\{\{ (.*) && format\('mq-noop-\{0\}', github\.run_id\) \|\| 'revalidate-and-merge' \}\}$/)?.[1];

const QUEUE_APP = 'hermes-automation-bot[bot]';
const GREEN_PR_CI = { event_name: 'workflow_run', event: { workflow_run: { conclusion: 'success', event: 'pull_request' } } };
const RED_PR_CI = { event_name: 'workflow_run', event: { workflow_run: { conclusion: 'failure', event: 'pull_request' } } };

const EVENTS = [
  { event_name: 'push', actor: 'ricardoafo' },
  { event_name: 'push', actor: QUEUE_APP },
  { event_name: 'schedule' },
  { event_name: 'workflow_dispatch' },
  ...['pull_request', 'push'].flatMap((runEvent) =>
    ['success', 'failure', 'cancelled', 'skipped', 'timed_out', null].map((conclusion) => ({
      event_name: 'workflow_run', event: { workflow_run: { conclusion, event: runEvent } },
    }))),
  ...['approved', 'commented', 'changes_requested'].map((state) => ({
    event_name: 'pull_request_review', event: { review: { state } },
  })),
];

test('concurrency group: shaped as <no-op condition> && mq-noop-<run_id> || revalidate-and-merge', () => {
  assert.ok(noopCondition, `unexpected group expression: ${groupExpr}`);
});

for (const github of EVENTS) {
  const label = `${github.event_name} ${JSON.stringify(github.event ?? {})}`;
  test(`no-op group is exactly the events Select skips: ${label}`, () => {
    assert.equal(evaluate(noopCondition, github), !evaluate(selectIf, github));
  });
}

test('a failed CI run goes to a no-op group, a green one queues for real', () => {
  assert.equal(evaluate(noopCondition, GREEN_PR_CI), false);
  assert.equal(evaluate(noopCondition, RED_PR_CI), true);
});

test('CI on main and the queue\'s own merges take no merge pass (#694)', () => {
  const greenMainCi = { event_name: 'workflow_run', event: { workflow_run: { conclusion: 'success', event: 'push' } } };
  assert.equal(evaluate(selectIf, greenMainCi), false);
  assert.equal(evaluate(selectIf, { event_name: 'push', actor: QUEUE_APP }), false);
  assert.equal(evaluate(selectIf, { event_name: 'push', actor: 'ricardoafo' }), true);
});
