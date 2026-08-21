#!/usr/bin/env bash
# Start the runner in the background (detached). Meant to be run ONCE
# after setup — the runner then auto-restarts via `restart: unless-stopped`
# through Docker Desktop restarts, PC reboots, and --ephemeral job exits.
#
# For ad-hoc / troubleshooting foreground runs, use:
#   docker compose up            # attached to terminal
#   docker compose up --scale runner=N  # multiple concurrent runners
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found. Copy .env.example to .env and fill in RUNNER_REGISTRATION_PAT."
    exit 1
fi

# Build if missing or Dockerfile changed
if ! docker image inspect gh-runner:latest >/dev/null 2>&1; then
    echo "Building gh-runner:latest (first time, ~3-5 min)…"
    docker compose build
elif [[ Dockerfile -nt "$(docker image inspect --format '{{.Metadata.LastTagTime}}' gh-runner:latest 2>/dev/null || echo 0)" ]]; then
    echo "Dockerfile changed — rebuilding gh-runner:latest…"
    docker compose build
fi

# Load .env into shell so ${VAR:?} substitutions in compose.yml resolve
set -a; source .env; set +a

echo "Starting runner (detached, restart: unless-stopped)…"
docker compose up -d

echo ""
echo "Runner is up. Tail logs with:  docker compose logs -f runner"
echo "Stop with:                     ./down.sh"
echo "Runner will auto-restart on:   PC boot, Docker Desktop restart, --ephemeral job exit"
