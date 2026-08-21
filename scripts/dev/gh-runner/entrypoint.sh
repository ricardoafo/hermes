#!/usr/bin/env bash
# In-container entrypoint: register runner → run job → deregister.
# Trap cleanup ensures deregistration even on SIGINT / SIGTERM / container kill.
#
# Two ways to authenticate:
#
#   1. RUNNER_TOKEN — pre-fetched registration token (1-hour TTL). Simpler,
#      no PAT storage needed. Best for on-demand / short sessions where the
#      launcher fetches a fresh token via `gh api` right before `docker
#      compose up`. Deregistration uses the same short-lived flow.
#
#   2. RUNNER_REGISTRATION_PAT — fine-grained PAT with Administration:
#      Read+write. Auto-fetches a fresh registration token per registration
#      + a fresh remove-token per deregistration. Best for 24/7 loops that
#      outlive the 1-hour TTL of a pre-fetched token.
#
# Exactly one of the two must be set. If both are set, RUNNER_TOKEN wins.
set -euo pipefail

: "${RUNNER_REPOSITORY:?RUNNER_REPOSITORY is required (format: owner/repo)}"
: "${RUNNER_NAME:=hermes-runner-$(hostname)}"
: "${RUNNER_LABELS:=self-hosted,linux,x64,hermes}"

REPO_API="https://api.github.com/repos/${RUNNER_REPOSITORY}"
REPO_URL="https://github.com/${RUNNER_REPOSITORY}"

# ────────────────────────────────────────────────────────────────
# Resolve registration token
# ────────────────────────────────────────────────────────────────
if [[ -n "${RUNNER_TOKEN:-}" ]]; then
    echo "Using pre-fetched RUNNER_TOKEN (1-hour TTL)."
    REG_TOKEN="${RUNNER_TOKEN}"
    AUTH_MODE="pre-fetched"
elif [[ -n "${RUNNER_REGISTRATION_PAT:-}" ]]; then
    echo "Fetching fresh registration token via PAT…"
    REG_TOKEN=$(curl -fsSL \
        -X POST \
        -H "Authorization: Bearer ${RUNNER_REGISTRATION_PAT}" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "${REPO_API}/actions/runners/registration-token" | jq -r .token)
    AUTH_MODE="pat"
else
    echo "ERROR: need either RUNNER_TOKEN (pre-fetched) or RUNNER_REGISTRATION_PAT"
    echo "For on-demand: fetch via 'gh api POST /repos/${RUNNER_REPOSITORY}/actions/runners/registration-token'"
    exit 1
fi

if [[ -z "${REG_TOKEN}" || "${REG_TOKEN}" == "null" ]]; then
    echo "ERROR: registration token is empty or null"
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Cleanup: deregister on shutdown. Deregistration needs a remove-token
# fetched via PAT — if we only have a pre-fetched RUNNER_TOKEN, we skip
# API deregistration and rely on --ephemeral (runner auto-removes after
# one job).
# ────────────────────────────────────────────────────────────────
cleanup() {
    echo ""
    if [[ "${AUTH_MODE}" == "pat" ]]; then
        echo "Deregistering runner via PAT…"
        REMOVE_TOKEN=$(curl -fsSL \
            -X POST \
            -H "Authorization: Bearer ${RUNNER_REGISTRATION_PAT}" \
            -H "Accept: application/vnd.github+json" \
            -H "X-GitHub-Api-Version: 2022-11-28" \
            "${REPO_API}/actions/runners/remove-token" | jq -r .token 2>/dev/null || echo "")
        if [[ -n "${REMOVE_TOKEN}" && "${REMOVE_TOKEN}" != "null" ]]; then
            ./config.sh remove --token "${REMOVE_TOKEN}" || true
        fi
    else
        echo "Pre-fetched token mode — --ephemeral flag will auto-remove the runner after job."
    fi
    echo "Runner shutdown complete."
}
trap cleanup EXIT INT TERM

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
