#!/usr/bin/env bash
# Progress ping for the healthchecks.io dead-man's switch.
#
# Run every 5 minutes by hermes-health.timer. While hermes.service is
# active, the script asks `hermes status --json` whether the loop is
# actually processing bars (#372) and reports that, not just "the process
# exists":
#
#   status exit 0 (all good)       -> success ping, the status JSON as body
#   status exit 1 (late or down)   -> /fail ping, the status JSON as body
#   status exit 2 (no `status` command: a release older than #370)
#                                  -> success ping, as before #372
#   status exit 2 while `hermes status --help` works (bad arguments)
#                                  -> /fail ping with the command's output
#   anything else (no ledger, config error, binary missing)
#                                  -> /fail ping with the command's output
#
# A hung poller or a stale feed therefore turns the check red within one
# tick instead of staying green. healthchecks.io shows the body in the
# check's log, so the alert carries the verdict and the late streams.
#
# While hermes.service is not active the script logs and exits 0: the
# missing ping is the signal, and the failed unit already shows in
# `systemctl`.
#
# The ping URL arrives as HERMES_HEALTHCHECKS__PING_URL through the unit's
# EnvironmentFile=/etc/hermes/env.production (systemd reads it as root).
#
# Exit codes: 0 pinged (success or /fail) or skipped (service down); 1 URL
# missing; anything else is curl's own exit status. A failed ping is NOT
# swallowed: it fails the oneshot, which stays visible in `systemctl
# --failed` until the next tick succeeds. curl's --retry absorbs short
# network blips, and the check's grace period covers a single missed ping.

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

# Test seams; production uses the release layout.
hermes_bin="${HERMES_HEALTH_HERMES_BIN:-/opt/hermes/current/.venv/bin/hermes}"
config_dir="${HERMES_HEALTH_CONFIG_DIR:-/opt/hermes/current/config}"
db="${HERMES_HEALTH_DB:-/var/lib/hermes/hermes.sqlite3}"

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
set +e
"$hermes_bin" status --config-dir "$config_dir" --db "$db" --json > "$body" 2>&1
status_rc=$?
set -e

case "$status_rc" in
    0) endpoint="$url" ; outcome="ok" ;;
    2)
        # Exit 2 is also argparse's code for bad arguments: a renamed flag must
        # not keep the dead-man's switch green (#856).
        if "$hermes_bin" status --help > /dev/null 2>&1; then
            endpoint="${url%/}/fail" ; outcome="fail (hermes status exit 2)"
        else
            endpoint="$url" ; outcome="ok (release has no hermes status)" ; : > "$body"
        fi
        ;;
    *) endpoint="${url%/}/fail" ; outcome="fail (hermes status exit $status_rc)" ;;
esac

# The ping URL carries the check's secret key, so it goes to curl as a config
# on stdin (printf is a builtin, so no argv either): `ps` shows argv to every
# local user. Escape for a double-quoted config value first.
endpoint="${endpoint//\\/\\\\}"
endpoint="${endpoint//\"/\\\"}"
printf 'url = "%s"\n' "$endpoint" \
    | curl --silent --show-error --fail --max-time 10 --retry 3 --output /dev/null \
        --data-binary "@$body" --config -
echo "hermes-health: ping sent: $outcome"
