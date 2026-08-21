#!/usr/bin/env bash
# Stop the runner. Because compose.yml sets `restart: unless-stopped`,
# a plain `docker stop` would trigger a restart — use `docker compose
# down` (or `docker compose stop`) to actually stop.
#
# The entrypoint's SIGTERM trap deregisters the runner from GH cleanly.
set -euo pipefail

cd "$(dirname "$0")"

echo "Stopping runner (SIGTERM trap will deregister from GH)…"
docker compose down --remove-orphans

if [[ "${1:-}" == "--prune" ]]; then
    echo "Pruning all runner cache + work volumes (cold start next up)…"
    for vol in uv-cache npm-cache pip-cache pytest-cache mypy-cache ruff-cache hypothesis-db; do
        docker volume rm "gh-runner_${vol}" 2>/dev/null || true
    done
fi

echo "Done. Restart with ./up.sh."
