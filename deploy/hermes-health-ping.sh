#!/usr/bin/env bash
# Liveness ping for the healthchecks.io dead-man's switch.
#
# Run every 5 minutes by hermes-health.timer. The ping is sent ONLY while
# hermes.service is active, so a missed ping on healthchecks.io means the
# engine is down or crash-looping (the check's Telegram integration then
# alerts the operator; see deploy/README.md). While the service is down the
# script logs and exits 0: the missing ping is the signal, and the failed
# hermes.service already shows in `systemctl`.
#
# The ping URL arrives as HERMES_HEALTHCHECKS__PING_URL through the unit's
# EnvironmentFile=/etc/hermes/env.production (systemd reads it as root).
#
# Exit codes: 0 pinged or skipped (service down); 1 URL missing; anything
# else is curl's own exit status. A failed ping is NOT swallowed: it fails the
# oneshot, which stays visible in `systemctl --failed` until the next tick
# succeeds. The timer keeps firing, curl's --retry absorbs short network
# blips, and the check's grace period covers a single missed ping, so this
# does not page anybody by itself.

set -euo pipefail

# Never let secrets leak via `set -x` even if something above turned it on.
set +x

echo "hermes-health tick: $(date -Iseconds) build=${HERMES_BUILD_SHA:-unknown}"

url="${HERMES_HEALTHCHECKS__PING_URL:-}"
if [ -z "$url" ]; then
    echo "hermes-health: HERMES_HEALTHCHECKS__PING_URL is not set; nothing to ping" >&2
    exit 1
fi

if ! systemctl is-active --quiet hermes.service; then
    echo "hermes-health: hermes.service is not active; ping skipped"
    exit 0
fi

# The ping URL carries the check's secret key, so it goes to curl as a config
# on stdin (printf is a builtin, so no argv either): `ps` shows argv to every
# local user. Escape for a double-quoted config value first.
url="${url//\\/\\\\}"
url="${url//\"/\\\"}"
printf 'url = "%s"\n' "$url" \
    | curl --silent --show-error --fail --max-time 10 --retry 3 --output /dev/null --config -
echo "hermes-health: ping sent"
