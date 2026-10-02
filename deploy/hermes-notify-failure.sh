#!/usr/bin/env bash
# Send ONE Telegram message saying a hermes unit entered the failed state.
#
# Started by hermes-notify-failure@.service, which hermes.service wires in
# with OnFailure=. Kept to bash + curl (+ journalctl for the log tail) on
# purpose: a broken hermes venv is a likely reason for the failure, so this
# must not need it.
#
# Credentials arrive as environment variables through the unit's
# EnvironmentFile=/etc/hermes/env.production (systemd reads it as root):
#   HERMES_TELEGRAM__BOT_TOKEN, HERMES_TELEGRAM__CHAT_ID
#
# Usage: hermes-notify-failure.sh [unit]      (default: hermes.service)
#
# Exit codes: 0 sent; 1 credentials missing; anything else is curl's own
# exit status (22 = Telegram answered with an HTTP error).

set -euo pipefail

# Never let secrets leak via `set -x` even if something above turned it on.
set +x

unit="${1:-hermes.service}"

# Test seam: points curl at a local fake. Never set it in env.production.
api_base="${HERMES_NOTIFY_API_BASE:-https://api.telegram.org}"
token="${HERMES_TELEGRAM__BOT_TOKEN:-}"
chat_id="${HERMES_TELEGRAM__CHAT_ID:-}"

journal_lines=5
max_line_chars=300

if [ -z "$token" ] || [ -z "$chat_id" ]; then
    echo "hermes-notify-failure: HERMES_TELEGRAM__BOT_TOKEN and HERMES_TELEGRAM__CHAT_ID must be set; no alert sent" >&2
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

# Replace the value of every secret-looking environment variable in $1.
# The environment holds all of env.production, so this also covers secrets
# the hermes process may have logged (Slack token, DSN, ping URL, ...).
redact() {
    local text="$1" name value
    for name in $(compgen -e); do
        [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        case "$name" in
            *TOKEN* | *SECRET* | *KEY* | *PASSWORD* | *PASSPHRASE* | *DSN* | *URL*) ;;
            *) continue ;;
        esac
        value="${!name}"
        # Skip short values ("no", "prod") that would shred ordinary text.
        [ "${#value}" -ge 8 ] || continue
        text="${text//"$value"/[redacted]}"
    done
    printf '%s' "$text"
}

# Last few journal lines of the unit; nothing when the journal is unreadable.
journal_tail() {
    command -v journalctl > /dev/null 2>&1 || return 0
    journalctl --unit="$unit" --lines="$journal_lines" --no-pager --output=cat 2> /dev/null || true
}

build_message() {
    local line
    local -a tail_lines=()
    # Redact before truncating so a cut can never leave half a secret behind.
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        line="$(redact "$line")"
        tail_lines+=("${line:0:max_line_chars}")
    done < <(journal_tail)

    local message="${unit} entered failed state on ${HOSTNAME:-unknown}"
    if [ "${#tail_lines[@]}" -gt 0 ]; then
        message+=$'\n\nLast journal lines:'
        for line in "${tail_lines[@]}"; do
            message+=$'\n'"${line}"
        done
    fi
    printf '%s' "$message"
}

# The bot token is part of the Telegram URL, so the whole request goes to curl
# as a config on stdin (printf is a builtin, so no argv either): nothing secret
# ends up in `ps`, which every local user can read.
# --retry can in rare cases deliver the message twice; losing the alert is the
# worse outcome.
send_message() {
    printf 'url = "%s"\ndata-urlencode = "chat_id=%s"\ndata-urlencode = "text=%s"\n' \
        "$(curl_quote "${api_base}/bot${token}/sendMessage")" \
        "$(curl_quote "$chat_id")" \
        "$(curl_quote "$1")" \
        | curl --silent --show-error --fail --max-time 15 --retry 2 --output /dev/null --config -
}

send_message "$(build_message)"
echo "hermes-notify-failure: alert sent for ${unit}"
