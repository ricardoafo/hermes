#!/usr/bin/env bash
# Watch the staging lab's memory and send ONE Telegram message when available
# memory drops below 1 GiB, and ONE when it recovers (ADR-024 §6, issue #1151).
#
# Started every minute by hermes-lab-memory.timer. Each run logs one line with
# MemAvailable and every running hermes-shadow@ instance's MemoryCurrent, so
# the journal holds the per-instance and total figures over a day:
#   journalctl -u hermes-lab-memory.service --since=-1d --output=cat
#
# Kept to bash + curl + systemctl on purpose, like hermes-notify-failure.sh:
# it must still alert when the release's venv is broken.
#
# Credentials arrive through the unit's EnvironmentFile=/etc/hermes/env.staging:
#   HERMES_TELEGRAM__BOT_TOKEN, HERMES_TELEGRAM__CHAT_ID
# The alert state lives in $STATE_DIRECTORY (systemd's StateDirectory=), so a
# low-memory spell alerts once, not every minute.
#
# Exit codes: 0 checked (and alerted if due); 1 meminfo unreadable or
# credentials missing when an alert is due; anything else is curl's own exit
# status (the state is left as it was, so the next run retries the alert).

set -euo pipefail

# Never let secrets leak via `set -x` even if something above turned it on.
set +x

# Test seams: never set these in env.staging.
api_base="${HERMES_NOTIFY_API_BASE:-https://api.telegram.org}"
meminfo="${HERMES_MEMINFO:-/proc/meminfo}"
state_dir="${STATE_DIRECTORY:-/var/lib/hermes-lab-memory}"

token="${HERMES_TELEGRAM__BOT_TOKEN:-}"
chat_id="${HERMES_TELEGRAM__CHAT_ID:-}"

floor_kib=$((1024 * 1024))
low_flag="${state_dir}/low"

available_kib="$(awk '$1 == "MemAvailable:" { print $2 }' "$meminfo" 2> /dev/null || true)"
if ! [[ "$available_kib" =~ ^[0-9]+$ ]]; then
    echo "hermes-lab-memory: no MemAvailable in ${meminfo}; nothing checked" >&2
    exit 1
fi

# One "name=NNN MiB" per running shadow instance, and their sum.
shadows=""
total_bytes=0
while read -r unit _; do
    [ -n "$unit" ] || continue
    bytes="$(systemctl show --property=MemoryCurrent --value "$unit" 2> /dev/null || true)"
    # systemd prints [not set] or an all-ones value when accounting is off.
    [[ "$bytes" =~ ^[0-9]+$ ]] && [ "${#bytes}" -lt 19 ] || continue
    name="${unit#hermes-shadow@}"
    shadows+=" ${name%.service}=$((bytes / 1048576))MiB"
    total_bytes=$((total_bytes + bytes))
done < <(systemctl list-units --type=service --state=running --no-legend --plain 'hermes-shadow@*' 2> /dev/null || true)

available_mib=$((available_kib / 1024))
figures="available=${available_mib}MiB shadows_total=$((total_bytes / 1048576))MiB${shadows}"
echo "hermes-lab-memory: ${figures}"

# Escape $1 for a double-quoted value in a curl config file (curl -K).
curl_quote() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\n'/\\n}"
    value="${value//$'\r'/\\r}"
    value="${value//$'\t'/\\t}"
    printf '%s' "$value"
}

# The bot token is part of the Telegram URL, so the request goes to curl as a
# config on stdin: nothing secret ends up in `ps`.
send_message() {
    if [ -z "$token" ] || [ -z "$chat_id" ]; then
        echo "hermes-lab-memory: HERMES_TELEGRAM__BOT_TOKEN and HERMES_TELEGRAM__CHAT_ID must be set; no alert sent" >&2
        exit 1
    fi
    printf 'url = "%s"\ndata-urlencode = "chat_id=%s"\ndata-urlencode = "text=%s"\n' \
        "$(curl_quote "${api_base}/bot${token}/sendMessage")" \
        "$(curl_quote "$chat_id")" \
        "$(curl_quote "$1")" \
        | curl --silent --show-error --fail --max-time 15 --retry 2 --output /dev/null --config -
}

host="${HOSTNAME:-unknown}"
if [ "$available_kib" -lt "$floor_kib" ]; then
    if [ ! -e "$low_flag" ]; then
        send_message "Lab memory low on ${host}: ${available_mib} MiB available, under 1024 MiB."$'\n'"${figures}"
        touch "$low_flag"
        echo "hermes-lab-memory: low-memory alert sent"
    fi
elif [ -e "$low_flag" ]; then
    send_message "Lab memory recovered on ${host}: ${available_mib} MiB available."$'\n'"${figures}"
    rm -f "$low_flag"
    echo "hermes-lab-memory: recovery sent"
fi
