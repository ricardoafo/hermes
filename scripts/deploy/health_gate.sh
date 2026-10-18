#!/usr/bin/env bash
# =====================================================================
# Health gate — decides whether a fresh hermes restart is good
# =====================================================================
#
# Usage: health_gate.sh <since_epoch>
#   Exit 0 = healthy, exit 1 = unhealthy.
#
# Runs on the VPS as root, from install_release.sh and rollback.sh. The
# unit is `Type=simple`, so `is-active` turns true at fork time and says
# nothing about whether `hermes serve` survived start-up. Three checks
# together prove the process is alive:
#   1. the unit stays active for GATE_HOLD_SECONDS (default 30),
#   2. MainPID never changes (a restart loop keeps flipping it),
#   3. the journal has `serve.run.started` since the restart.
# Do not drop any of the three. Limit: this proves start and a stable
# first 30 s, not that the strategy trades.
#
# HERMES_TARGET=staging gates hermes-staging.service instead (#192).
#
# Test seams: SYSTEMCTL, JOURNALCTL, GATE_HOLD_SECONDS.
#
# Reference: openspec/archive/179-cd-first-real-deploy/change.md (D-7),
# openspec/archive/192-staging-env/change.md (D-3).

set -euo pipefail

bad() {
    echo "::error::$*"
    exit 1
}

case "${HERMES_TARGET:-prod}" in
    prod) unit=hermes.service ;;
    staging) unit=hermes-staging.service ;;
    *) bad "unknown HERMES_TARGET '${HERMES_TARGET}': expected prod or staging" ;;
esac
since=${1:?usage: health_gate.sh <since_epoch>}
hold=${GATE_HOLD_SECONDS:-30}
SYSTEMCTL=${SYSTEMCTL:-systemctl}
JOURNALCTL=${JOURNALCTL:-journalctl}

pid=""
for ((t = 0; t < hold; t += 2)); do
    "$SYSTEMCTL" is-active --quiet "$unit" || bad "$unit not active at t=${t}s"
    now=$("$SYSTEMCTL" show -p MainPID --value "$unit")
    [ -z "$pid" ] || [ "$now" = "$pid" ] || bad "$unit restarted itself ($pid -> $now)"
    pid=$now
    sleep 2
done

# Capture first: `journalctl | grep -q` under pipefail fails on SIGPIPE
# even when grep matched.
logs=$("$JOURNALCTL" -u "$unit" --since "@$since" --no-pager -o cat)
grep -q 'serve\.run\.started' <<<"$logs" || bad "no serve.run.started since the restart"

echo "gate OK: $unit active ${hold}s, MainPID=$pid, serve.run.started seen"
