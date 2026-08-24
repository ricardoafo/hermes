#!/usr/bin/env bash
# Idempotent branch protection setup for ricardoafo/hermes main branch.
#
# Uses the RULESETS API (not classic branch protection). Rulesets are the
# newer mechanism GitHub is moving everyone toward — they support explicit
# rule types (deletion, non_fast_forward, require_extra_approval_for_
# unattributed_changes) that classic protection does not, and they layer
# cleanly with org rules if we ever add them.
#
# Discipline enforced (see docs/guides/dev-workflow.md + docs/decisions/):
#   - 1 approving review required (Ricardo, via CODEOWNERS)
#   - Code owner review required
#   - Dismiss stale reviews on new commits
#   - Require last push approval (safe under revalidate-then-merge design:
#     the queue never pushes to the PR branch, so only genuine author
#     iteration dismisses approvals — which is correct behavior)
#   - Require extra approval for commits authored by unknown identities
#     (guards against bot identity misconfiguration mid-PR)
#   - Require review-thread resolution before merge
#   - `CI success` aggregator must be green (single required check)
#   - `strict_required_status_checks_policy: false` — do NOT require branch
#     up-to-date. Freshness is enforced by merge-queue.yml, which builds
#     the merge tree at the resolved main SHA and asserts twice.
#     See docs/decisions/2026-08-21-revalidate-then-merge.md.
#   - Linear history (squash-merge only)
#   - Squash merge is the ONLY allowed method — no merge commits, no rebase
#   - Non-fast-forward pushes blocked (protects against history rewrites)
#   - Branch deletion blocked
#
# Idempotency: looks up an existing ruleset by name; if present, updates
# it; if absent, creates it. Safe to re-run.
#
# Required: gh CLI authenticated as ricardoafo (owner). Bot lacks admin scope.

set -euo pipefail

REPO="${REPO:-ricardoafo/hermes}"
BRANCH="${BRANCH:-main}"
RULESET_NAME="${RULESET_NAME:-main-protection}"

# ────────────────────────────────────────────────────────────────
# Ruleset body — single source of truth for what protection is applied
# ────────────────────────────────────────────────────────────────
RULESET_BODY=$(cat <<'JSON'
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "required_linear_history"},
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "require_last_push_approval": true,
        "required_review_thread_resolution": true,
        "require_extra_approval_for_unattributed_changes": true,
        "allowed_merge_methods": ["squash"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          {"context": "CI success"}
        ]
      }
    }
  ]
}
JSON
)

echo "Reconciling ruleset '$RULESET_NAME' on $REPO"

# ────────────────────────────────────────────────────────────────
# Look up existing ruleset by name
# ────────────────────────────────────────────────────────────────
existing_id=$(gh api "repos/$REPO/rulesets" --jq \
  ".[] | select(.name == \"$RULESET_NAME\") | .id" | head -1)

if [[ -n "$existing_id" ]]; then
    echo "  found existing ruleset id=$existing_id — updating"
    printf '%s' "$RULESET_BODY" | gh api -X PUT \
        "repos/$REPO/rulesets/$existing_id" \
        --input - >/dev/null
else
    echo "  no existing ruleset — creating"
    printf '%s' "$RULESET_BODY" | gh api -X POST \
        "repos/$REPO/rulesets" \
        --input - >/dev/null
fi

echo "  ✓ ruleset applied"

# ────────────────────────────────────────────────────────────────
# Optionally clean up classic branch protection (if present)
# ────────────────────────────────────────────────────────────────
if gh api "repos/$REPO/branches/$BRANCH/protection" >/dev/null 2>&1; then
    echo ""
    echo "Note: classic branch protection is ALSO active on $BRANCH."
    echo "  Rulesets and classic protection stack (both apply). To rely on"
    echo "  rulesets alone, delete classic protection with:"
    echo "    gh api -X DELETE repos/$REPO/branches/$BRANCH/protection"
    echo "  (This script does not delete it automatically — the user"
    echo "  should confirm the ruleset works first.)"
fi

echo ""
echo "Verifying rules on $BRANCH:"
gh api "repos/$REPO/rules/branches/$BRANCH" --jq \
  '[.[] | {type: .type, source: .ruleset_source}] | group_by(.type) | map({type: .[0].type, count: length}) | .[]'

echo ""
echo "Done. Ruleset is idempotent — safe to re-run."
