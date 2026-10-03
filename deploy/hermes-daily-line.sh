#!/usr/bin/env bash
# Send the daily "hermes is alive" line to Slack (#373).
#
# Run once a day by hermes-daily.timer (07:00 Europe/Madrid). Builds the line
# with `hermes status --summary`, feeding it the last 24 h of hermes.service's
# journal (error and warning counts) and the number of restarts since the
# previous run, then posts it to Slack. With no signal hermes is otherwise
# silent for days; this line says it is alive and processing.
#
# Restart count: systemd's NRestarts for hermes.service is cumulative since
# the unit was loaded, so the script keeps yesterday's value in
# $STATE_DIRECTORY/nrestarts (StateDirectory=hermes-daily) and reports the
# difference. A counter that went down (reboot, daemon-reload) counts from 0.
#
# Credentials come from the unit's EnvironmentFile=/etc/hermes/env.production:
#   HERMES_SLACK__BOT_TOKEN, and the channel from HERMES_DAILY_LINE__CHANNEL,
#   falling back to HERMES_SLACK__DEFAULT_CHANNEL.
#
# Exit codes: 0 sent; 1 Slack credentials missing; 2 Slack refused the line;
# anything else is curl's own exit status. A failure fails the oneshot, so it
# shows in `systemctl --failed` until tomorrow's run succeeds.

set -euo pipefail

# Never let secrets leak via `set -x` even if something above turned it on.
set +x

# Test seams; production uses the release layout and the real Slack API.
hermes_bin="${HERMES_DAILY_HERMES_BIN:-/opt/hermes/current/.venv/bin/hermes}"
config_dir="${HERMES_DAILY_CONFIG_DIR:-/opt/hermes/current/config}"
db="${HERMES_DAILY_DB:-/var/lib/hermes/hermes.sqlite3}"
api_base="${HERMES_NOTIFY_SLACK_API_BASE:-https://slack.com}"
state_dir="${STATE_DIRECTORY:-/var/lib/hermes-daily}"

token="${HERMES_SLACK__BOT_TOKEN:-}"
channel="${HERMES_DAILY_LINE__CHANNEL:-${HERMES_SLACK__DEFAULT_CHANNEL:-}}"
if [ -z "$token" ] || [ -z "$channel" ]; then
    echo "hermes-daily-line: HERMES_SLACK__BOT_TOKEN and a channel must be set; no line sent" >&2
    exit 1
fi

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

restarts_since_last_run() {
    local now_count previous=0 file="$state_dir/nrestarts"
    now_count="$(systemctl show hermes.service --property=NRestarts --value 2> /dev/null || true)"
    [[ "$now_count" =~ ^[0-9]+$ ]] || now_count=0
    if [ -f "$file" ]; then
        previous="$(cat "$file")"
        [[ "$previous" =~ ^[0-9]+$ ]] || previous=0
    fi
    mkdir -p "$state_dir"
    printf '%s\n' "$now_count" > "$file"
    if [ "$now_count" -ge "$previous" ]; then
        echo $((now_count - previous))
    else
        echo "$now_count"
    fi
}

restarts="$(restarts_since_last_run)"

# `hermes status` exits 1 when late or down and 3 without a ledger; the line
# says so itself, so any exit code with output is sent.
set +e
line="$(
    journalctl --unit=hermes.service --since=-24h --no-pager --output=cat 2> /dev/null \
        | "$hermes_bin" status --config-dir "$config_dir" --db "$db" --summary \
            --journal - --restarts "$restarts"
)"
set -e
if [ -z "$line" ]; then
    line="hermes daily line: \`hermes status --summary\` printed nothing on ${HOSTNAME:-unknown}"
fi

# The bot token goes in a header, so the request reaches curl as a config on
# stdin (printf is a builtin, so no argv either): nothing secret ends up in
# `ps`. Slack answers HTTP 200 with "ok":false on API errors.
response="$(
    printf 'url = "%s"\nheader = "Authorization: Bearer %s"\ndata-urlencode = "channel=%s"\ndata-urlencode = "text=%s"\n' \
        "$(curl_quote "${api_base}/api/chat.postMessage")" \
        "$(curl_quote "$token")" \
        "$(curl_quote "$channel")" \
        "$(curl_quote "$line")" \
        | curl --silent --show-error --fail --max-time 15 --retry 2 --config -
)"
case "$response" in
    *'"ok":true'* | *'"ok": true'*) ;;
    *)
        echo "hermes-daily-line: Slack refused the line: ${response:0:200}" >&2
        exit 2
        ;;
esac
echo "hermes-daily-line: sent: $line"
