#!/usr/bin/env bash
# Progress ping for the staging lab's own healthchecks.io check (issue #1079).
#
# Run every 5 minutes by hermes-lab-health.timer. The lab is every enabled
# hermes-shadow@ instance (ADR-024 §6). Each tick asks `hermes status --json`
# about each one, on its own unit and ledger, and reports the lab as a whole:
#
#   every shadow active and its status exit 0  -> success ping
#   any shadow not active, or its status not 0 -> /fail ping
#   no shadow enabled                          -> no ping
#
# The body names each shadow and carries its status output, so the check's
# log shows which shadow is late or down. With no shadow enabled the lab is
# off: the missed ping is the signal, as prod's ping does while hermes.service
# is stopped.
#
# The ping URL arrives as HERMES_HEALTHCHECKS__PING_URL through the unit's
# EnvironmentFile=/etc/hermes/env.staging. It is the lab's own check: the
# config loader and the install preflight refuse prod's URL in staging
# (#970), and `hermes status` loads that config first, so a copied prod URL
# fails every shadow's status and the ping goes to /fail.
#
# Exit codes: 0 pinged (success or /fail) or skipped (no shadow enabled); 1
# URL missing or a shadow name that is not plain; anything else is curl's own
# exit status, which fails the oneshot until the next tick succeeds.

set -euo pipefail

# Never let secrets leak via `set -x` even if something above turned it on.
set +x

echo "hermes-lab-health tick: $(date -Iseconds)"

url="${HERMES_HEALTHCHECKS__PING_URL:-}"
if [ -z "$url" ]; then
    echo "hermes-lab-health: HERMES_HEALTHCHECKS__PING_URL is not set; nothing to ping" >&2
    exit 1
fi

# Test seams; production uses the staging release layout.
hermes_bin="${HERMES_HEALTH_HERMES_BIN:-/opt/hermes-staging/current/.venv/bin/hermes}"
config_dir="${HERMES_HEALTH_CONFIG_DIR:-/opt/hermes-staging/current/config}"
shadows_dir="${HERMES_HEALTH_SHADOWS_DIR:-/var/lib/hermes-staging/shadows}"
unit_dir="${HERMES_HEALTH_UNIT_DIR:-/etc/systemd/system}"

# Enabled instances, found the way install_release.sh finds them: the
# symlinks `systemctl enable` makes in a target's .wants directory.
shadows=()
while IFS= read -r name; do
    [ -n "$name" ] || continue
    name=${name#hermes-shadow@}
    name=${name%.service}
    if ! [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
        echo "hermes-lab-health: '$name' is not a plain shadow name" >&2
        exit 1
    fi
    shadows+=("$name")
done < <(find "$unit_dir" -mindepth 2 -maxdepth 2 -path '*.wants/hermes-shadow@*.service' -printf '%f\n' \
    | LC_ALL=C sort -u)

if [ "${#shadows[@]}" -eq 0 ]; then
    echo "hermes-lab-health: no lab shadow is enabled; ping skipped"
    exit 0
fi

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
failed=()
for name in "${shadows[@]}"; do
    unit="hermes-shadow@${name}.service"
    printf '== %s\n' "$name" >> "$body"
    if ! systemctl is-active --quiet "$unit"; then
        printf '%s is not active\n' "$unit" >> "$body"
        failed+=("$name")
        continue
    fi
    set +e
    "$hermes_bin" status --config-dir "$config_dir" --unit "$unit" \
        --db "${shadows_dir}/${name}/hermes.sqlite3" --json >> "$body" 2>&1
    status_rc=$?
    set -e
    printf '\n' >> "$body"
    [ "$status_rc" -eq 0 ] || failed+=("$name")
done

if [ "${#failed[@]}" -eq 0 ]; then
    endpoint="$url" ; outcome="ok (${#shadows[@]} shadows)"
else
    endpoint="${url%/}/fail" ; outcome="fail (${failed[*]})"
fi

# The ping URL carries the check's secret key, so it goes to curl as a config
# on stdin (printf is a builtin, so no argv either): `ps` shows argv to every
# local user. Escape for a double-quoted config value first.
endpoint="${endpoint//\\/\\\\}"
endpoint="${endpoint//\"/\\\"}"
printf 'url = "%s"\n' "$endpoint" \
    | curl --silent --show-error --fail --max-time 10 --retry 3 --output /dev/null \
        --data-binary "@$body" --config -
echo "hermes-lab-health: ping sent: $outcome"
