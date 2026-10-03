/**
 * Decides which tracker metadata a newly opened PR should inherit from
 * the issue it closes, so writers stop setting milestone, labels and
 * assignee by hand (and stop forgetting them).
 *
 * Pure functions only; pr-housekeeping.yml does the API calls. Nothing
 * already on the PR is ever removed or overwritten.
 */

const CLOSING_REF = /\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b/i;
const INHERITED_LABEL = /^(?:type:|area:|p\d$)/;
const DEFAULT_ASSIGNEE = 'rafo-claude-bot';

function closedIssueNumber(body) {
  const match = CLOSING_REF.exec(body || '');
  return match ? Number(match[1]) : null;
}

/**
 * @param {{milestone: ?{number:number}, labels: {name:string}[], assignees: {login:string}[]}} pr
 * @param {?{milestone: ?{number:number}, labels: {name:string}[], pull_request?: object}} issue
 * @returns {{milestone: ?number, addLabels: string[], addAssignees: string[]}}
 */
function planMetadata(pr, issue) {
  const plan = { milestone: null, addLabels: [], addAssignees: [] };
  if (pr.assignees.length === 0) plan.addAssignees.push(DEFAULT_ASSIGNEE);
  if (!issue || issue.pull_request) return plan;

  if (!pr.milestone && issue.milestone) plan.milestone = issue.milestone.number;

  const present = new Set(pr.labels.map((l) => l.name));
  // The PR title already decides the change type; an issue's type label
  // fills in only when the PR has none, so the two never disagree.
  const prHasType = [...present].some((n) => n.startsWith('type:'));
  for (const { name } of issue.labels) {
    if (!INHERITED_LABEL.test(name) || present.has(name)) continue;
    if (name.startsWith('type:') && prHasType) continue;
    plan.addLabels.push(name);
  }
  return plan;
}

module.exports = { closedIssueNumber, planMetadata, DEFAULT_ASSIGNEE };
