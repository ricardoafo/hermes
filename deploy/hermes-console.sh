#!/usr/bin/env bash
# Plain `hermes` for the operator on the VPS (installed as /usr/local/bin/hermes).
#
# Runs the current release's read-only console commands as the `hermes` user
# with the service env loaded, in the ledger's directory, so `hermes status`
# works bare. Same recipe as `hermes_run` in docs/guides/vps-operations.md and
# as the remote script of `hermes --vps`.
#
# Only the console commands and --help go through; serve, migrate and anything
# else that can write are refused here, before anything runs (#610).

set -euo pipefail

RELEASE_BIN=/opt/hermes/current/.venv/bin/hermes
ENV_FILE=/etc/hermes/env.production
WORKDIR=/var/lib/hermes
DB_PATH=/var/lib/hermes/hermes.sqlite3
CONFIG_DIR=/opt/hermes/current/config
RUN_AS=hermes
# Keep in step with the commands that set wants_demo (a unit test checks).
CONSOLE_COMMANDS="activity money positions status tui"

args=("$@")
[ "${#args[@]}" -gt 0 ] || args=(--help)

# The command is the first argument that is not a leading `--demo SCENARIO`.
command_index=0
case "${args[0]}" in
    --demo) command_index=2 ;;
    --demo=*) command_index=1 ;;
esac
command=${args[$command_index]:-}

case "$command" in
    -h | --help) ;;
    "") ;; # `--demo SCENARIO` alone prints the overview
    *)
        case " $CONSOLE_COMMANDS " in
            *" $command "*) ;;
            *)
                echo "hermes: '$command' is not available here; only read-only console commands run on the VPS (${CONSOLE_COMMANDS// /, })." >&2
                exit 2
                ;;
        esac
        ;;
esac

# The env file is readable by root only.
if [ "$(id -u)" -ne 0 ]; then
    exec sudo -- "$0" "$@"
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
export HERMES_DB_PATH=${HERMES_DB_PATH:-$DB_PATH}
export HERMES_CONFIG_DIR=${HERMES_CONFIG_DIR:-$CONFIG_DIR}
cd "$WORKDIR"
exec runuser -u "$RUN_AS" -- "$RELEASE_BIN" "${args[@]}"
