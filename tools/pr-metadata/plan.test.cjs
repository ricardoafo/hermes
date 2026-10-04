/**
 * Unit tests for tools/pr-metadata/plan.cjs.
 *
 * Run: node --test tools/pr-metadata/plan.test.cjs
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const { closedIssueNumber, planMetadata } = require('./plan.cjs');

const label = (name) => ({ name });
const bare = { milestone: null, labels: [], assignees: [] };
const issue = {
  milestone: { number: 5 },
  labels: [label('type:feat'), label('p1'), label('area:cli'), label('size:m'), label('sev-2')],
};

test('closing keywords in any case find the issue number', () => {
  assert.equal(closedIssueNumber('Summary\n\nCloses #472'), 472);
  assert.equal(closedIssueNumber('fixes #9 and more'), 9);
  assert.equal(closedIssueNumber('RESOLVED #31'), 31);
});

test('a mention without a closing keyword links nothing', () => {
  assert.equal(closedIssueNumber('Related to #12'), null);
  assert.equal(closedIssueNumber(null), null);
});

test('a bare PR inherits milestone, type, priority and area but not size or severity', () => {
  const plan = planMetadata(bare, issue);
  assert.equal(plan.milestone, 5);
  assert.deepEqual(plan.addLabels, ['type:feat', 'p1', 'area:cli']);
});

test('values already on the PR are never overwritten', () => {
  const pr = { milestone: { number: 6 }, labels: [label('type:fix'), label('p1')], assignees: [{ login: 'x' }] };
  const plan = planMetadata(pr, issue);
  assert.equal(plan.milestone, null);
  assert.deepEqual(plan.addLabels, ['area:cli']);
  assert.deepEqual(plan.addAssignees, []);
});

test('an unassigned PR gets the bot as assignee even without a linked issue', () => {
  const plan = planMetadata(bare, null);
  assert.deepEqual(plan, { milestone: null, addLabels: [], addAssignees: ['rafo-claude-bot'] });
});

test('a closing reference to another PR copies nothing', () => {
  const plan = planMetadata(bare, { ...issue, pull_request: {} });
  assert.equal(plan.milestone, null);
  assert.deepEqual(plan.addLabels, []);
});
