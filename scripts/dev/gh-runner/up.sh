#!/usr/bin/env bash
# Start N gh-runner containers in the background (detached). Meant to
# be run ONCE per session or after a config change — runners then
# auto-restart via `restart: unless-stopped` across Docker Desktop
# restarts, PC reboots, and each --ephemeral job exit.
#
# Usage:
#   ./up.sh          # 4 runners (default — matches compose.yml's `scale: 4`)
#   ./up.sh 1        # 1 runner (troubleshooting — isolate flaky jobs)
#   ./up.sh N        # N runners
#
# The default is 8 because a code-changing PR fans out to 12 parallel
# jobs post-`changeset`. 8 runners → 1.5 waves (~30s); 4 → 3 waves
# (~60s). Rule: replicas = max_parallel_fan_out.
#
# Re-running with a different N scales the pool without stopping:
#   ./up.sh 4  ->  ./up.sh 8  adds 4 more runners
#   ./up.sh 8  ->  ./up.sh 4  stops 4 runners cleanly
set -euo pipefail

cd "$(dirname "$0")"

SCALE="${1:-4}"

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found. Copy .env.example to .env and fill in HERMES_RUNNER_REGISTRATION_PAT."
    exit 1
fi

# Build if missing or Dockerfile changed
if ! docker image inspect gh-runner:latest >/dev/null 2>&1 \
    || ! docker image inspect gh-runner:ops >/dev/null 2>&1; then
    echo "Building gh-runner:latest and gh-runner:ops (first time, ~3-5 min)…"
    docker compose build
elif [[ Dockerfile -nt "$(docker image inspect --format '{{.Metadata.LastTagTime}}' gh-runner:latest 2>/dev/null || echo 0)" ]]; then
    echo "Dockerfile changed — rebuilding gh-runner:latest…"
    docker compose build
fi

# Load .env into shell so ${VAR:?} substitutions in compose.yml resolve
set -a; source .env; set +a

echo "Starting ${SCALE} runner(s) (detached, restart: unless-stopped)…"
# The ops and Dependabot runners are single services; --scale only
# applies to the CI pool.
docker compose up -d --scale "runner=${SCALE}" --no-recreate

echo ""
echo "Runners are up. Verify at:"
echo "  docker compose ps"
echo "  https://github.com/ricardoafo/hermes/settings/actions/runners"
echo ""
echo "Tail logs:                  docker compose logs -f runner"
echo "Stop all:                   ./down.sh"
echo "Scale to different N:       ./up.sh <N>  (re-invoke, add/remove replicas cleanly)"
echo "Runners auto-restart on:    PC boot, Docker Desktop restart, --ephemeral job exit"
