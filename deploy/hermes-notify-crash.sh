#!/usr/bin/env bash
# Post ONE Slack line when hermes.service exits with an error (#380).
#
# Wired as hermes.service's ExecStopPost=, so it runs after every stop.
# systemd sets $SERVICE_RESULT, $EXIT_CODE and $EXIT_STATUS for it:
#
#   SERVICE_RESULT=success   clean stop (deploy, SIGTERM, exit 0): nothing sent
#   anything else            crash, signal, timeout, watchdog: one Slack line
#
# A single crash restarts in 30 s (Restart=on-failure) and never reaches
# OnFailure=, which fires only once the unit lands in `failed`. This line
# covers that gap on Slack, the same-day channel; Telegram stays for "down".
#
# Kept to bash + curl (+ journalctl for the last serve.run.failed line), and
# installed to /opt/hermes/bin rather than run from the release, for the
# same reason as hermes-notify-failure.sh: a broken release is a likely
# cause of the crash (#364).
#
# Credentials come from hermes.service's EnvironmentFile:
#   HERMES_SLACK__BOT_TOKEN, HERMES_SLACK__DEFAULT_CHANNEL
#
# Always exits 0: a notice that cannot be sent must never change how
# systemd handles the stop or the restart. Problems go to stderr (journal).

set -uo pipefail

# Never let secrets leak via `set -x` even if something above turned it on.
set +x

unit="${1:-hermes.service}"
result="${SERVICE_RESULT:-}"

if [ -z "$result" ] || [ "$result" = "success" ]; then
    exit 0
fi

# The same variable the Python notifier reads (#970): a lower environment
# points it at a fake; unset, the real API. Never set it in env.production.
api_base="${HERMES_SLACK__API_BASE:-https://slack.com}"
token="${HERMES_SLACK__BOT_TOKEN:-}"
channel="${HERMES_SLACK__DEFAULT_CHANNEL:-}"
max_line_chars=300

if [ -z "$token" ] || [ -z "$channel" ]; then
    echo "hermes-notify-crash: HERMES_SLACK__BOT_TOKEN and HERMES_SLACK__DEFAULT_CHANNEL must be set; no notice sent" >&2
    exit 0
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

# The newest serve.run.failed line of this run; nothing when unreadable.
last_failure() {
    command -v journalctl > /dev/null 2>&1 || return 0
    journalctl --unit="$unit" --lines=200 --no-pager --output=cat 2> /dev/null \
        | grep -F 'serve.run.failed' | tail -n 1 || true
}

build_message() {
    local message failure
    message="${unit} on ${HOSTNAME:-unknown} exited with an error"
    message+=" (result=${result}, code=${EXIT_CODE:-?}, status=${EXIT_STATUS:-?})."
    message+=" systemd restarts it in 30s; Telegram alerts if it stays down."
    failure="$(last_failure)"
    if [ -n "$failure" ]; then
        # Redact before truncating so a cut can never leave half a secret behind.
        failure="$(redact "$failure")"
        message+=$'\n'"Last error: ${failure:0:max_line_chars}"
    fi
    printf '%s' "$message"
}

# The bot token goes in a header, so the whole request reaches curl as a
# config on stdin (printf is a builtin, so no argv either): nothing secret
# ends up in `ps`. Slack answers HTTP 200 with "ok":false on API errors, so
# the body is checked too.
send_message() {
    local response
    response="$(
        printf 'url = "%s"\nheader = "Authorization: Bearer %s"\ndata-urlencode = "channel=%s"\ndata-urlencode = "text=%s"\n' \
            "$(curl_quote "${api_base}/api/chat.postMessage")" \
            "$(curl_quote "$token")" \
            "$(curl_quote "$channel")" \
            "$(curl_quote "$1")" \
            | curl --silent --show-error --fail --max-time 15 --retry 2 --config -
    )" || {
        echo "hermes-notify-crash: Slack request failed" >&2
        return 1
    }
    case "$response" in
        *'"ok":true'* | *'"ok": true'*) return 0 ;;
        *)
            echo "hermes-notify-crash: Slack refused the notice: $(redact "${response:0:200}")" >&2
            return 1
            ;;
    esac
}

if send_message "$(build_message)"; then
    echo "hermes-notify-crash: notice sent for ${unit} (result=${result})"
fi
exit 0
