#!/usr/bin/env bash
# Bring up the self-hosted runner on-demand.
#
# Two auth flows supported:
#
#   1. Default (recommended for on-demand): fetch a fresh RUNNER_TOKEN via
#      `gh api` right before compose-up. Token is short-lived (1h), never
#      touches disk. Requires `gh auth` as a user with Administration:
#      Read+write on the repo.
#
#   2. PAT flow: export RUNNER_REGISTRATION_PAT in your shell (or via a
#      .env file loaded before this script). For 24/7 loops needing
#      repeated re-registration beyond the 1h token TTL.
#
# Modes:
#   ./up.sh              — one-shot: processes ONE job, then exits
#   ./up.sh --loop       — polling loop: restarts container per job
#                          (Ctrl-C to stop). Each iteration fetches a
#                          fresh token if RUNNER_TOKEN mode is used.
set -euo pipefail

cd "$(dirname "$0")"

REPO="${RUNNER_REPOSITORY:-ricardoafo/hermes}"

fetch_token() {
    if [[ -n "${RUNNER_REGISTRATION_PAT:-}" ]]; then
        return 0  # PAT flow — entrypoint fetches its own token
    fi
    echo "Fetching fresh RUNNER_TOKEN via gh api…"
    RUNNER_TOKEN=$(gh api -X POST "/repos/${REPO}/actions/runners/registration-token" --jq .token)
    if [[ -z "${RUNNER_TOKEN}" || "${RUNNER_TOKEN}" == "null" ]]; then
        echo "ERROR: could not fetch registration token via gh api"
        echo "Check: gh auth status (needs user with Administration: Read+write on ${REPO})"
        exit 1
    fi
    export RUNNER_TOKEN
}

# Build image if missing or Dockerfile changed
if ! docker image inspect gh-runner:latest >/dev/null 2>&1; then
    echo "Building runner image (first time — ~3-5 min)…"
    docker compose build
elif [[ Dockerfile -nt "$(docker image inspect --format '{{.Metadata.LastTagTime}}' gh-runner:latest 2>/dev/null || echo 0)" ]]; then
    echo "Dockerfile changed — rebuilding runner image…"
    docker compose build
fi

if [[ "${1:-}" == "--loop" ]]; then
    echo "Loop mode: runner will restart after each job. Ctrl-C to stop."
    while true; do
        fetch_token
        docker compose up --force-recreate --abort-on-container-exit || {
            echo "Runner exited. Restart in 5s…"
            sleep 5
        }
    done
else
    echo "One-shot mode: runner processes ONE job then exits."
    echo "Use \`./up.sh --loop\` to keep polling."
    fetch_token
    docker compose up --force-recreate --abort-on-container-exit
fi
