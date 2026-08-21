#!/usr/bin/env bash
# Idempotent branch protection setup for ricardoafo/hermes main branch.
# Enforces the discipline documented in docs/guides/dev-workflow.md:
#   - 1 approving review required (Ricardo, via CODEOWNERS)
#   - dismiss stale reviews on new commits
#   - require last push approval
#   - linear history only (squash-merge only, no merge-commits)
#   - no force push, no deletions, no admin bypass
#   - resolve all conversations before merge
#
# Required: gh CLI authenticated as ricardoafo (owner). Bot lacks admin scope.

set -euo pipefail

REPO="${REPO:-ricardoafo/hermes}"
BRANCH="${BRANCH:-main}"

echo "Applying branch protection to $REPO:$BRANCH"

gh api -X PUT "repos/$REPO/branches/$BRANCH/protection" --input - <<'JSON' >/dev/null
{
  "required_status_checks": null,
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1,
    "require_last_push_approval": true
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": false,
  "block_creations": false
}
JSON

echo "  ✓ protection applied"
echo ""
echo "Verifying:"
gh api "repos/$REPO/branches/$BRANCH/protection" \
  --jq '{
    reviews_required: .required_pull_request_reviews.required_approving_review_count,
    code_owner_review: .required_pull_request_reviews.require_code_owner_reviews,
    dismiss_stale: .required_pull_request_reviews.dismiss_stale_reviews,
    linear_history: .required_linear_history.enabled,
    enforce_admins: .enforce_admins.enabled,
    force_push_blocked: (.allow_force_pushes.enabled | not),
    deletion_blocked: (.allow_deletions.enabled | not),
    conversation_resolution: .required_conversation_resolution.enabled
  }'

echo ""
echo "Done. Protection is idempotent — safe to re-run."
