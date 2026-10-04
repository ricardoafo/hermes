/**
 * Unit tests for tools/pr-metadata/ai-review.cjs.
 *
 * Run: node --test tools/pr-metadata/ai-review.test.cjs
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  MARKER, parseReview, outcome, parseMetrics, metricsLine, renderComment, renderFailure, stickyCommentId,
} = require('./ai-review.cjs');

const meta = { headSha: 'abcdef0123456789', runUrl: 'https://github.com/o/r/actions/runs/1' };

function raw(overrides = {}) {
  return JSON.stringify({
    verdict: 'approve',
    risk: 'low',
    for_rick: {
      what_changes: 'The status command shows the last bar time.',
      why_now: 'Closes #144 for MVP1.',
      risk_reason: 'Read-only display code.',
      evidence: 'Two new unit tests; CI green.',
    },
    findings: [],
    ...overrides,
  });
}

const blocking = { severity: 'blocking', title: 'Rounding', detail: 'Uses float.', file: 'src/a.py', line: 3 };
const optional = { severity: 'optional', title: 'Name', detail: 'Rename x.', file: '', line: 0 };

test('a clean approve passes the check', () => {
  assert.deepEqual(outcome(parseReview(raw())), { pass: true, reason: 'AI review approved' });
});

test('an approve that lists a blocking finding fails closed', () => {
  const result = outcome(parseReview(raw({ findings: [blocking] })));
  assert.equal(result.pass, false);
  assert.equal(result.reason, 'AI review approved but listed 1 blocking finding(s)');
});

test('changes needed and needs decision both fail the check', () => {
  assert.equal(outcome(parseReview(raw({ verdict: 'changes_needed' }))).pass, false);
  assert.equal(outcome(parseReview(raw({ verdict: 'needs_decision' }))).pass, false);
});

test('optional findings alone do not block an approve', () => {
  assert.equal(outcome(parseReview(raw({ findings: [optional] }))).pass, true);
});

test('empty, non-JSON or off-schema output throws instead of passing', () => {
  assert.throws(() => parseReview(''), /empty/);
  assert.throws(() => parseReview('not json'), /not JSON/);
  assert.throws(() => parseReview(raw({ verdict: 'lgtm' })), /verdict must be one of/);
  assert.throws(() => parseReview(raw({ risk: 'none' })), /risk must be one of/);
  assert.throws(() => parseReview(raw({ findings: 'none' })), /findings must be an array/);
  assert.throws(() => parseReview(raw({ for_rick: { what_changes: 'x' } })), /for_rick.why_now/);
  assert.throws(() => parseReview(raw({ findings: [{ ...blocking, severity: 'major' }] })), /severity/);
});

test('the comment starts with the sticky marker and leads with the plain-language card', () => {
  const body = renderComment(parseReview(raw()), meta);
  assert.ok(body.startsWith(`${MARKER}\n## For Rick: ready for your approval`));
  assert.match(body, /\*\*What changes for you:\*\* The status command shows the last bar time\./);
  assert.match(body, /\*\*Risk:\*\* low\. Read-only display code\./);
  assert.match(body, /abcdef0 · \[run\]\(https:\/\/github\.com\/o\/r\/actions\/runs\/1\)/);
});

test('blocking and optional findings are grouped with their location', () => {
  const body = renderComment(parseReview(raw({ verdict: 'changes_needed', findings: [blocking, optional] })), meta);
  assert.match(body, /## For Rick: changes needed/);
  assert.match(body, /\*\*Blocking \(1\)\*\*\n\n- \*\*Rounding\*\* \(`src\/a\.py:3`\): Uses float\./);
  assert.match(body, /\*\*Optional \(1\)\*\*\n\n- \*\*Name\*\*: Rename x\./);
});

test('a needs-decision verdict states the decision', () => {
  const review = parseReview(raw({
    verdict: 'needs_decision',
    for_rick: { ...JSON.parse(raw()).for_rick, decision: 'Keep the 5% cap or raise it?' },
  }));
  assert.match(renderComment(review, meta), /\*\*Your decision:\*\* Keep the 5% cap or raise it\?/);
});

test('model text cannot mention people or forge the marker', () => {
  const review = parseReview(raw({
    for_rick: { ...JSON.parse(raw()).for_rick, what_changes: `ping @ricardoafo ${MARKER} <!-- hidden -->` },
  }));
  const body = renderComment(review, meta);
  assert.equal(body.split(MARKER).length, 2);
  assert.ok(!body.includes('@ricardoafo'));
  assert.ok(!body.includes('<!-- hidden'));
});

test('a failed run says the PR is not ready', () => {
  const body = renderFailure('AI review output is empty', meta);
  assert.ok(body.startsWith(MARKER));
  assert.match(body, /not ready for approval until it passes/);
});

test('the sticky comment is found by its marker, not by author', () => {
  const comments = [{ id: 1, body: 'hello' }, { id: 2, body: `${MARKER}\n## For Rick` }, { id: 3, body: null }];
  assert.equal(stickyCommentId(comments), 2);
  assert.equal(stickyCommentId([{ id: 1, body: `quote ${MARKER}` }]), null);
});

const execution = JSON.stringify([
  { type: 'system', subtype: 'init' },
  {
    type: 'result',
    duration_ms: 252400,
    num_turns: 23,
    total_cost_usd: 0.8412,
    usage: { input_tokens: 1200, cache_creation_input_tokens: 40800, output_tokens: 6100, cache_read_input_tokens: 512000 },
  },
]);

test('metrics come from the result message at the end of the execution file', () => {
  assert.deepEqual(parseMetrics(execution), {
    seconds: 252, turns: 23, costUsd: 0.8412, inputTokens: 42000, outputTokens: 6100, cacheReadTokens: 512000,
  });
});

test('a missing or unfinished execution file gives no metrics instead of throwing', () => {
  assert.equal(parseMetrics(''), null);
  assert.equal(parseMetrics('not json'), null);
  assert.equal(parseMetrics(JSON.stringify([{ type: 'assistant' }])), null);
});

test('the metrics line shows time, turns, tokens and API-price cost', () => {
  assert.equal(metricsLine(parseMetrics(execution)), '4m 12s · 23 turns · 42k in / 6k out / 512k cached tokens · $0.84 at API prices');
  assert.equal(metricsLine(null), '');
});

test('the comment footer carries the metrics when measured', () => {
  const body = renderComment(parseReview(raw()), { ...meta, metrics: parseMetrics(execution) });
  assert.match(body, /\[run\]\(https:\/\/github\.com\/o\/r\/actions\/runs\/1\) · 4m 12s · 23 turns/);
});
