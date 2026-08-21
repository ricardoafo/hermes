#!/usr/bin/env bash
# Bring down the runner cleanly. The entrypoint's trap handles deregistration
# on SIGTERM (docker compose down sends SIGTERM). This script also offers
# optional volume prune for a clean state next up.
set -euo pipefail

cd "$(dirname "$0")"

echo "Stopping runner (entrypoint trap will deregister)…"
docker compose down --remove-orphans

if [[ "${1:-}" == "--prune" ]]; then
    echo "Pruning all runner cache + work volumes (fresh state next up)…"
    for vol in uv-cache npm-cache pip-cache pytest-cache mypy-cache ruff-cache hypothesis-db actions-cache runner-work; do
        docker volume rm "gh-runner_${vol}" 2>/dev/null || true
    done
fi

echo "Done."
