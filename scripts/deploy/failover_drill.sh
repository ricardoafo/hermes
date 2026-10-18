#!/usr/bin/env bash
# =====================================================================
# Failover drill — timed rehearsal of the Binance to Kraken cutover
# =====================================================================
#
# Usage: failover_drill.sh [--execute] [--report-dir DIR]
#
#   Without --execute (the default, a rehearsal): checks the bar window,
#   runs `hermes failover-check` and records open positions. Changes
#   nothing.
#   With --execute: also snapshots the ledger, stops hermes.service,
#   starts it again, runs the health gate and checks that open positions
#   are the same before and after.
#
# Exit codes:
#   0 drill passed           1 a step failed (the report says which)
#   2 usage                  4 too close to a 4h bar close, try later
#   5 refused: real-capital mode (HERMES_ALLOW_MAINNET is yes)
#
# Runs on hermes-01 as root, by hand, once a month (issue #31). Every step
# is timed into $REPORT_DIR/report.md so the total can be compared with
# the 90-minute target in ADR-004 Decision 6. Steps the script cannot do
# (the venue switch, moving capital) are listed in the report for the
# operator to time by hand: see docs/runbook/failover-to-kraken.md.
#
# Test seams: SYSTEMCTL, HEALTH_GATE, HERMES_BIN, HERMES_PYTHON, HERMES_DB,
# HERMES_CONFIG_DIR, HERMES_ENV_FILE, HERMES_RUN (runs a command as the hermes user),
# NOW_EPOCH (fixes the clock for the bar-window guard).
#
# Reference: docs/runbook/failover-to-kraken.md; openspec/archive/31-failover-drill.

set -euo pipefail

SYSTEMCTL=${SYSTEMCTL:-systemctl}
UNIT=hermes.service
HEALTH_GATE=${HEALTH_GATE:-/opt/hermes/bin/health_gate.sh}
HERMES_BIN=${HERMES_BIN:-/opt/hermes/current/.venv/bin/hermes}
HERMES_PYTHON=${HERMES_PYTHON:-/opt/hermes/current/.venv/bin/python}
HERMES_DB=${HERMES_DB:-/var/lib/hermes/hermes.sqlite3}
# Absolute: with HERMES_ENV=prod the loader refuses a relative config dir, and
# the drill runs from /var/lib/hermes, which has no config/ (#950).
HERMES_CONFIG_DIR=${HERMES_CONFIG_DIR:-/opt/hermes/current/config}
HERMES_ENV_FILE=${HERMES_ENV_FILE:-/etc/hermes/env.production}
# 4h bars close at 00/04/08/12/16/20 UTC. Stay clear of the close, the
# settle delay and the bar's processing so the restart never costs a bar.
BAR_SECONDS=14400
GUARD_BEFORE=1200
GUARD_AFTER=600

execute=false
report_dir=""
while [ $# -gt 0 ]; do
    case "$1" in
        --execute) execute=true ;;
        --report-dir)
            [ $# -ge 2 ] || { echo "usage: failover_drill.sh [--execute] [--report-dir DIR]" >&2; exit 2; }
            report_dir=$2
            shift
            ;;
        *)
            echo "usage: failover_drill.sh [--execute] [--report-dir DIR]" >&2
            exit 2
            ;;
    esac
    shift
done

now() { echo "${NOW_EPOCH:-$(date +%s)}"; }
stamp=$(date -u -d "@$(now)" +%Y%m%dT%H%M%SZ)
REPORT_DIR=${report_dir:-/var/lib/hermes/backups/drills/$stamp}
mkdir -p "$REPORT_DIR"
REPORT=$REPORT_DIR/report.md
drill_start=$(now)

# Runs a hermes command as the hermes user with the production env, the
# same way vps-diagnose.yml does.
run_as_hermes() {
    if [ -n "${HERMES_RUN:-}" ]; then
        "$HERMES_RUN" "$@"
        return
    fi
    bash -c 'set -a; . "$1"; set +a; cd /var/lib/hermes && exec runuser -u hermes -- "${@:2}"' \
        _ "$HERMES_ENV_FILE" "$@"
}

record() {
    # record <step> <status> <seconds> <note>
    printf '| %s | %s | %s s | %s |\n' "$1" "$2" "$3" "$4" >> "$REPORT"
}

finish() {
    local verdict=$1 code=$2
    {
        echo ""
        echo "Scripted steps took $(( $(now) - drill_start )) s. Verdict: **$verdict**."
        echo ""
        echo "Timed by hand (see the runbook): venue switch, credential check,"
        echo "capital transfer, reconciliation. Target for the whole cutover: 90 min."
    } >> "$REPORT"
    echo "drill $verdict: report at $REPORT"
    exit "$code"
}

fail() {
    # fail <step> <seconds> <why>
    record "$1" FAIL "$2" "$3"
    echo "::error::$1: $3"
    finish FAILED 1
}

{
    echo "# Failover drill $stamp"
    echo ""
    echo "Mode: $([ "$execute" = true ] && echo execute || echo rehearsal). Host: $(hostname)."
    echo ""
    echo "| Step | Result | Took | Note |"
    echo "| --- | --- | --- | --- |"
} > "$REPORT"

# Real capital: stopping the unit with live orders open is the full
# runbook, by hand, with the engine paused first. Not this script.
if [ -f "$HERMES_ENV_FILE" ] && grep -Eq '^HERMES_ALLOW_MAINNET="?(yes|true|1)"?$' "$HERMES_ENV_FILE"; then
    record "mode guard" REFUSED 0 "HERMES_ALLOW_MAINNET is on: follow the runbook by hand"
    echo "::error::real-capital mode: this script only drills shadow mode"
    exit 5
fi

offset=$(( $(now) % BAR_SECONDS ))
if [ "$offset" -lt "$GUARD_AFTER" ] || [ "$offset" -gt $(( BAR_SECONDS - GUARD_BEFORE )) ]; then
    record "bar window" WAIT 0 "within 20 min before or 10 min after a 4h close"
    echo "too close to a 4h bar close (00/04/08/12/16/20 UTC): run again later"
    exit 4
fi
record "bar window" OK 0 "$(( (BAR_SECONDS - offset) / 60 )) min to the next close"

# 1. Can Kraken stand in? Read-only.
t=$(now)
if ! run_as_hermes "$HERMES_BIN" failover-check --config-dir "$HERMES_CONFIG_DIR" > "$REPORT_DIR/failover-check.txt" 2>&1; then
    fail "failover-check" $(( $(now) - t )) "Kraken not ready, see failover-check.txt"
fi
record "failover-check" OK $(( $(now) - t )) "$(tail -n 1 "$REPORT_DIR/failover-check.txt")"

# 2. Open positions before. Read-only.
t=$(now)
run_as_hermes "$HERMES_BIN" positions --json --utc > "$REPORT_DIR/positions-before.json" \
    || fail "positions before" $(( $(now) - t )) "hermes positions failed"
record "positions before" OK $(( $(now) - t )) "positions-before.json"

if [ "$execute" != true ]; then
    record "stop, swap, start" SKIPPED 0 "rehearsal: pass --execute to restart the unit"
    finish PASSED 0
fi

# 3. Ledger snapshot, outside install_release.sh's pre-*.sqlite3 rotation.
t=$(now)
"$HERMES_PYTHON" - "$HERMES_DB" "$REPORT_DIR/ledger-before.sqlite3" <<'PY' \
    || fail "ledger snapshot" $(( $(now) - t )) "could not copy $HERMES_DB"
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()
PY
record "ledger snapshot" OK $(( $(now) - t )) "ledger-before.sqlite3"

# 4. Stop.
t=$(now)
"$SYSTEMCTL" stop "$UNIT" || fail "stop" $(( $(now) - t )) "systemctl stop $UNIT failed"
record "stop" OK $(( $(now) - t )) "$UNIT stopped"

# 5. Venue switch. `hermes serve` reads Binance only today, so there is
# nothing to swap; the step stays in the report so the gap stays visible.
record "venue switch" "NOT AVAILABLE" 0 "serve has no venue setting yet, see runbook Gaps"

# 6. Start and gate.
t=$(now)
"$SYSTEMCTL" start "$UNIT" || fail "start" $(( $(now) - t )) "systemctl start $UNIT failed"
bash "$HEALTH_GATE" "$t" > "$REPORT_DIR/health-gate.txt" 2>&1 \
    || fail "start and gate" $(( $(now) - t )) "health gate failed, see health-gate.txt"
record "start and gate" OK $(( $(now) - t )) "$(tail -n 1 "$REPORT_DIR/health-gate.txt")"

# 7. Positions after must match: same coin, side, size, entry and open time.
t=$(now)
run_as_hermes "$HERMES_BIN" positions --json --utc > "$REPORT_DIR/positions-after.json" \
    || fail "positions match" $(( $(now) - t )) "hermes positions failed"
"$HERMES_PYTHON" - "$REPORT_DIR/positions-before.json" "$REPORT_DIR/positions-after.json" \
    > "$REPORT_DIR/positions-diff.txt" <<'PY' \
    || fail "positions match" $(( $(now) - t )) "positions changed, see positions-diff.txt"
import json, sys
KEYS = ("coin", "timeframe", "side", "size", "opened_at", "entry_price")
def load(path):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)["positions"]
    return sorted(tuple(row.get(k) for k in KEYS) for row in rows)
before, after = load(sys.argv[1]), load(sys.argv[2])
for row in sorted(set(before) ^ set(after)):
    print(("before only: " if row in before else "after only: ") + " ".join(map(str, row)))
sys.exit(0 if before == after else 1)
PY
record "positions match" OK $(( $(now) - t )) "same open positions before and after"

finish PASSED 0
