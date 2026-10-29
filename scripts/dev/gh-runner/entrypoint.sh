#!/usr/bin/env bash
# In-container entrypoint: register runner → run one job. The runner
# registers with --ephemeral, so GitHub removes it after its one job.
#
# Two ways to authenticate:
#
#   1. RUNNER_TOKEN — pre-fetched registration token (1-hour TTL). Simpler,
#      no PAT storage needed. Best for on-demand / short sessions where the
#      launcher fetches a fresh token via `gh api` right before `docker
#      compose up`. Deregistration uses the same short-lived flow.
#
#   2. HERMES_RUNNER_REGISTRATION_PAT — fine-grained PAT with Administration:
#      Read+write. Fetches a fresh registration token per registration.
#      Best for 24/7 loops that outlive the 1-hour TTL of a pre-fetched
#      token.
#
# Exactly one of the two must be set. If both are set, RUNNER_TOKEN wins.
# Both leave the environment before the runner starts: a job runs as the
# same user and could otherwise read the PAT, which can administer the
# repository.
set -euo pipefail

: "${RUNNER_REPOSITORY:?RUNNER_REPOSITORY is required (format: owner/repo)}"
: "${RUNNER_NAME:=gh-runner-hermes-$(hostname)}"
: "${RUNNER_LABELS:=self-hosted,linux,x64,hermes}"

# Docker creates named cache volumes as root by default. Fix ownership
# so the runner user can write. Idempotent.
# The image pre-creates every cache dir as the runner, so a new volume
# is already the runner's. Only the ops image has sudo; elsewhere an
# old root-owned volume is reported, not fixed.
for cache in /home/runner/.cache/*/; do
    if [[ "$(stat -c '%U' "$cache")" != "runner" ]]; then
        sudo -n chown -R runner:runner "$cache" 2>/dev/null \
            || echo "WARNING: $cache is not owned by the runner; recreate its volume."
    fi
done

# ────────────────────────────────────────────────────────────────
# Wipe _work between jobs. `restart: unless-stopped` restarts the
# SAME container in place, so the container's writable layer (which
# is where /home/runner/_work lives — it's not a mount) persists
# between --ephemeral job runs. That leaked prior git state into
# subsequent jobs — sparse-checkout config from one workflow file
# survived to the next, leaving files missing from the working tree
# that the checkout thought were still present.
#
# Nuking _work at entrypoint start makes every job land on a truly
# empty workspace, matching what the "each job in a fresh container"
# design intended. Cache volumes stay intact (they're separate mounts
# under /home/runner/.cache).
# ────────────────────────────────────────────────────────────────
if [[ -d /home/runner/_work ]]; then
    echo "Wiping /home/runner/_work — every job starts on a clean workspace."
    rm -rf /home/runner/_work/* /home/runner/_work/.[!.]* 2>/dev/null || true
fi

# A deploy job removes its SSH key in its last step; a job killed before
# that step leaves it behind, so the next start removes it too.
rm -rf /home/runner/.ssh

REPO_API="https://api.github.com/repos/${RUNNER_REPOSITORY}"
REPO_URL="https://github.com/${RUNNER_REPOSITORY}"

# ────────────────────────────────────────────────────────────────
# Resolve registration token
# ────────────────────────────────────────────────────────────────
if [[ -n "${RUNNER_TOKEN:-}" ]]; then
    echo "Using pre-fetched RUNNER_TOKEN (1-hour TTL)."
    REG_TOKEN="${RUNNER_TOKEN}"
elif [[ -n "${HERMES_RUNNER_REGISTRATION_PAT:-}" ]]; then
    echo "Fetching fresh registration token via PAT…"
    REG_TOKEN=$(curl -fsSL \
        -X POST \
        -H "Authorization: Bearer ${HERMES_RUNNER_REGISTRATION_PAT}" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "${REPO_API}/actions/runners/registration-token" | jq -r .token)
else
    echo "ERROR: need either RUNNER_TOKEN (pre-fetched) or HERMES_RUNNER_REGISTRATION_PAT"
    echo "For on-demand: fetch via 'gh api POST /repos/${RUNNER_REPOSITORY}/actions/runners/registration-token'"
    exit 1
fi

if [[ -z "${REG_TOKEN}" || "${REG_TOKEN}" == "null" ]]; then
    echo "ERROR: registration token is empty or null"
    exit 1
fi
unset HERMES_RUNNER_REGISTRATION_PAT RUNNER_TOKEN

# ────────────────────────────────────────────────────────────────
# Register + run
# ────────────────────────────────────────────────────────────────
echo "Registering runner ${RUNNER_NAME} with labels: ${RUNNER_LABELS}"
./config.sh \
    --url "${REPO_URL}" \
    --token "${REG_TOKEN}" \
    --name "${RUNNER_NAME}" \
    --labels "${RUNNER_LABELS}" \
    --work "_work" \
    --ephemeral \
    --unattended \
    --replace

echo "Runner ready. Polling for jobs…"
exec ./run.sh
