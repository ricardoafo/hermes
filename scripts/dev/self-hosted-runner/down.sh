#!/usr/bin/env bash
# Bring down the runner cleanly. The entrypoint's trap handles deregistration
# on SIGTERM (docker compose down sends SIGTERM). This script also offers
# optional volume prune for a clean state next up.
set -euo pipefail

cd "$(dirname "$0")"

echo "Stopping runner (entrypoint trap will deregister)…"
docker compose down --remove-orphans

if [[ "${1:-}" == "--prune" ]]; then
    echo "Pruning runner volumes (fresh cache next up)…"
    docker volume rm self-hosted-runner_runner-cache self-hosted-runner_runner-work 2>/dev/null || true
fi

echo "Done."
