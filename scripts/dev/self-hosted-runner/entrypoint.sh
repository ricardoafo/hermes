#!/usr/bin/env bash
# In-container entrypoint: fetch registration token → register → run job → deregister.
# Trap cleanup ensures deregistration even on SIGINT / SIGTERM / container kill.
set -euo pipefail

: "${RUNNER_REPOSITORY:?RUNNER_REPOSITORY is required (format: owner/repo)}"
: "${RUNNER_REGISTRATION_PAT:?RUNNER_REGISTRATION_PAT is required (fine-grained PAT with repo admin scope)}"
: "${RUNNER_NAME:=hermes-runner-$(hostname)}"
: "${RUNNER_LABELS:=self-hosted,linux,x64,hermes}"

REPO_API="https://api.github.com/repos/${RUNNER_REPOSITORY}"
REPO_URL="https://github.com/${RUNNER_REPOSITORY}"

echo "Fetching fresh registration token (1-hour TTL)…"
REG_TOKEN=$(curl -fsSL \
    -X POST \
    -H "Authorization: Bearer ${RUNNER_REGISTRATION_PAT}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "${REPO_API}/actions/runners/registration-token" | jq -r .token)

if [[ -z "${REG_TOKEN}" || "${REG_TOKEN}" == "null" ]]; then
    echo "ERROR: could not fetch registration token — check PAT scopes (needs repo admin)"
    exit 1
fi

cleanup() {
    echo ""
    echo "Deregistering runner…"
    REMOVE_TOKEN=$(curl -fsSL \
        -X POST \
        -H "Authorization: Bearer ${RUNNER_REGISTRATION_PAT}" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "${REPO_API}/actions/runners/remove-token" | jq -r .token 2>/dev/null || echo "")
    if [[ -n "${REMOVE_TOKEN}" && "${REMOVE_TOKEN}" != "null" ]]; then
        ./config.sh remove --token "${REMOVE_TOKEN}" || true
    fi
    echo "Runner deregistered. Bye."
}
trap cleanup EXIT INT TERM

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
