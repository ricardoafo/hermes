#!/usr/bin/env bash
# Bring up the self-hosted runner. On-demand — run before working with Claude.
# Runs one job then exits (--ephemeral). Loop if you want multi-job sessions.
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found — copy .env.example to .env and fill in your PAT"
    exit 1
fi

# Build image if missing or Dockerfile changed
if ! docker image inspect hermes-runner:latest >/dev/null 2>&1 \
    || [[ Dockerfile -nt "$(docker image inspect --format '{{.Metadata.LastTagTime}}' hermes-runner:latest 2>/dev/null || echo 0)" ]]; then
    echo "Building runner image…"
    docker compose build
fi

# Loop mode: process jobs until user hits Ctrl-C
if [[ "${1:-}" == "--loop" ]]; then
    echo "Loop mode: runner will restart after each job. Ctrl-C to stop."
    while true; do
        docker compose up --force-recreate --abort-on-container-exit || {
            echo "Runner exited. Restart in 5s…"
            sleep 5
        }
    done
else
    echo "One-shot mode: runner processes ONE job then exits."
    echo "Use \`./up.sh --loop\` to keep polling."
    docker compose up --force-recreate --abort-on-container-exit
fi
