/**
 * Turns the AI reviewer's structured output into the sticky PR comment
 * and the pass/fail outcome of the "AI review" check (#590).
 *
 * Pure functions only; ai-review.yml does the API calls. The model never
 * posts anything itself: it only returns JSON, and this module decides
 * what reaches the PR. Anything malformed fails closed.
 */

const MARKER = '<!-- hermes-ai-review -->';
// The account ai-review.yml posts with (the job's GITHUB_TOKEN).
const AUTHOR = 'github-actions[bot]';
// Paths that steer the reviewer or the merge gates. A PR touching them
// can rewrite what the review checks, so its own review never passes the
// check: Rick decides (#778).
const REVIEW_PATH = [/^\.github\//, /^tools\/pr-metadata\//, /^AGENTS\.md$/];
const VERDICTS = ['approve', 'changes_needed', 'needs_decision'];
const RISKS = ['low', 'medium', 'high'];
const SEVERITIES = ['blocking', 'optional'];
const MAX_TEXT = 4000;

const VERDICT_LABEL = {
  approve: 'Approve',
  changes_needed: 'Changes needed',
  needs_decision: 'Needs your decision',
};

function requireText(value, field) {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`AI review output: ${field} must be a non-empty string`);
  }
  return value;
}

function requireEnum(value, allowed, field) {
  if (!allowed.includes(value)) {
    throw new Error(`AI review output: ${field} must be one of ${allowed.join(', ')}; got ${JSON.stringify(value)}`);
  }
  return value;
}

/**
 * Parses and validates the reviewer's JSON.
 *
 * @param {string} raw the action's `structured_output`
 * @returns {{verdict: string, risk: string, forRick: object, findings: object[]}}
 * @throws {Error} when the output is missing, not JSON, or breaks the schema
 */
function parseReview(raw) {
  if (typeof raw !== 'string' || raw.trim() === '') {
    throw new Error('AI review output is empty: the reviewer returned no structured result');
  }
  let data;
  try {
    data = JSON.parse(raw);
  } catch (err) {
    throw new Error(`AI review output is not JSON: ${err.message}`);
  }
  const fr = data.for_rick || {};
  const findings = Array.isArray(data.findings) ? data.findings : null;
  if (!findings) throw new Error('AI review output: findings must be an array');
  return {
    verdict: requireEnum(data.verdict, VERDICTS, 'verdict'),
    risk: requireEnum(data.risk, RISKS, 'risk'),
    forRick: {
      whatChanges: requireText(fr.what_changes, 'for_rick.what_changes'),
      whyNow: requireText(fr.why_now, 'for_rick.why_now'),
      riskReason: requireText(fr.risk_reason, 'for_rick.risk_reason'),
      evidence: requireText(fr.evidence, 'for_rick.evidence'),
      decision: typeof fr.decision === 'string' ? fr.decision : '',
    },
    findings: findings.map((f, i) => ({
      severity: requireEnum(f.severity, SEVERITIES, `findings[${i}].severity`),
      title: requireText(f.title, `findings[${i}].title`),
      detail: requireText(f.detail, `findings[${i}].detail`),
      file: typeof f.file === 'string' ? f.file : '',
      line: Number.isInteger(f.line) && f.line > 0 ? f.line : null,
    })),
  };
}

/**
 * Changed paths that steer the reviewer or the merge gates.
 *
 * @param {string} nameStatus `git diff --name-status` output
 * @returns {string[]} the changed paths under {@link REVIEW_PATH}
 */
function reviewPathChanges(nameStatus) {
  const paths = String(nameStatus || '')
    .split('\n')
    .flatMap((line) => line.split('\t').slice(1))
    .filter(Boolean);
  return [...new Set(paths.filter((p) => REVIEW_PATH.some((re) => re.test(p))))];
}

/**
 * Turns an Approve on a PR that touches the review path into a decision
 * for Rick: such a PR can change what its own review checks.
 *
 * @param {ReturnType<typeof parseReview>} review
 * @param {string[]} touched from {@link reviewPathChanges}
 */
function guardReviewPath(review, touched) {
  if (review.verdict !== 'approve' || touched.length === 0) return review;
  return {
    ...review,
    verdict: 'needs_decision',
    forRick: {
      ...review.forRick,
      decision: `This PR changes the review or merge gates themselves (${touched.slice(0, 3).join(', ')}${touched.length > 3 ? ', …' : ''}), so the AI review cannot vouch for it on its own. The reviewer found no blocking problem; approve if you accept the change.`,
    },
  };
}

/**
 * The check passes only on an Approve with no blocking finding. An
 * Approve that still lists a blocking finding contradicts itself and
 * fails, so a confused reviewer can never wave a PR through.
 *
 * @returns {{pass: boolean, reason: string}}
 */
function outcome(review) {
  const blocking = review.findings.filter((f) => f.severity === 'blocking').length;
  if (review.verdict === 'approve' && blocking === 0) {
    return { pass: true, reason: 'AI review approved' };
  }
  if (review.verdict === 'approve') {
    return { pass: false, reason: `AI review approved but listed ${blocking} blocking finding(s)` };
  }
  return { pass: false, reason: `AI review: ${VERDICT_LABEL[review.verdict]}` };
}

// Model text is untrusted: it must not ping people, forge the sticky
// marker, break out of the comment's layout, or carry links, images or
// code spans that dress injected text up as ours (#778).
function clean(text) {
  return String(text)
    .slice(0, MAX_TEXT)
    .split(MARKER).join('')
    .replace(/<!--|-->/g, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/<\s*\/?\s*(img|a)\b[^>]*>/gi, '')
    .replace(/`/g, '')
    .replace(/@(?=[A-Za-z0-9-])/g, '@​');
}

function oneLine(text) {
  return clean(text).replace(/\s*\n\s*/g, ' ');
}

/**
 * Reads time, turns, tokens and the API-price cost from the action's
 * execution file, whose last element is the session's result message.
 * Reviews bill to a Claude plan, so the dollar figure is what the same
 * review would cost at API prices: the yardstick for cost efficiency.
 *
 * @param {?string} content the execution file's text
 * @returns {?{seconds: number, turns: number, costUsd: number, inputTokens: number, outputTokens: number, cacheReadTokens: number}}
 */
function parseMetrics(content) {
  let data;
  try {
    data = JSON.parse(content || '');
  } catch {
    return null;
  }
  const last = Array.isArray(data) ? data[data.length - 1] : null;
  if (!last || last.type !== 'result' || typeof last.duration_ms !== 'number') return null;
  const usage = last.usage || {};
  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  return {
    seconds: Math.round(num(last.duration_ms) / 1000),
    turns: num(last.num_turns),
    costUsd: num(last.total_cost_usd),
    inputTokens: num(usage.input_tokens) + num(usage.cache_creation_input_tokens),
    outputTokens: num(usage.output_tokens),
    cacheReadTokens: num(usage.cache_read_input_tokens),
  };
}

/**
 * @param {?ReturnType<typeof parseMetrics>} metrics
 * @returns {string} one line, or empty when nothing was measured
 */
function metricsLine(metrics) {
  if (!metrics) return '';
  const m = Math.floor(metrics.seconds / 60);
  const sec = metrics.seconds % 60;
  const kTok = (n) => `${Math.round(n / 1000)}k`;
  return `${m}m ${sec}s · ${metrics.turns} turns · ${kTok(metrics.inputTokens)} in / ${kTok(metrics.outputTokens)} out / ${kTok(metrics.cacheReadTokens)} cached tokens · $${metrics.costUsd.toFixed(2)} at API prices`;
}

/**
 * @param {ReturnType<typeof parseReview>} review
 * @param {{headSha: string, runUrl: string, metrics?: ?object}} meta
 * @returns {string} the sticky comment body
 */
function renderComment(review, meta) {
  const { pass } = outcome(review);
  const fr = review.forRick;
  const blocking = review.findings.filter((f) => f.severity === 'blocking');
  const optional = review.findings.filter((f) => f.severity === 'optional');
  const lines = [
    MARKER,
    `## For Rick: ${pass ? 'ready for your approval' : VERDICT_LABEL[review.verdict].toLowerCase()}`,
    '',
    `**What changes for you:** ${oneLine(fr.whatChanges)}`,
    '',
    `**Why now:** ${oneLine(fr.whyNow)}`,
    '',
    `**Risk:** ${review.risk}. ${oneLine(fr.riskReason)}`,
    '',
    `**What proves it works:** ${oneLine(fr.evidence)}`,
    '',
    `**AI review verdict:** ${VERDICT_LABEL[review.verdict]}`,
  ];
  if (review.verdict === 'needs_decision' && fr.decision) {
    lines.push('', `**Your decision:** ${oneLine(fr.decision)}`);
  }
  const renderFinding = (f) => {
    const where = f.file ? ` (\`${oneLine(f.file)}${f.line ? `:${f.line}` : ''}\`)` : '';
    return `- **${oneLine(f.title)}**${where}: ${oneLine(f.detail)}`;
  };
  lines.push('', '### Findings for the writer', '');
  if (review.findings.length === 0) {
    lines.push('None.');
  } else {
    if (blocking.length) lines.push(`**Blocking (${blocking.length})**`, '', ...blocking.map(renderFinding), '');
    if (optional.length) lines.push(`**Optional (${optional.length})**`, '', ...optional.map(renderFinding), '');
  }
  const measured = metricsLine(meta.metrics);
  lines.push('', `<sub>Independent AI review of ${meta.headSha.slice(0, 7)} · [run](${meta.runUrl})${measured ? ` · ${measured}` : ''} · updates on every push</sub>`);
  return lines.join('\n').replace(/\n{3,}/g, '\n\n');
}

/**
 * @param {string} reason why no review ran
 * @param {{headSha: string, runUrl: string}} meta
 */
function renderFailure(reason, meta) {
  return [
    MARKER,
    '## For Rick: AI review did not finish',
    '',
    `${oneLine(reason)}. Re-run the "AI review" job; the PR is not ready for approval until it passes.`,
    '',
    `<sub>${meta.headSha.slice(0, 7)} · [run](${meta.runUrl})</sub>`,
  ].join('\n');
}

/**
 * The review's own comment: it carries the marker and was posted by the
 * workflow. Anyone can paste the marker into a comment; only the author
 * check keeps a stranger's comment from being taken over (#778).
 *
 * @param {{id: number, body: ?string, user?: ?{login: string}}[]} comments
 * @returns {?number} id of the existing sticky comment
 */
function stickyCommentId(comments) {
  const found = comments.find((c) => (c.body || '').startsWith(MARKER) && c.user?.login === AUTHOR);
  return found ? found.id : null;
}

module.exports = {
  MARKER, AUTHOR, parseReview, outcome, reviewPathChanges, guardReviewPath,
  parseMetrics, metricsLine, renderComment, renderFailure, stickyCommentId,
};
