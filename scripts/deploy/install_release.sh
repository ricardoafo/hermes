#!/usr/bin/env bash
# =====================================================================
# Install one hermes release on the VPS, activate it, and gate it
# =====================================================================
#
# Usage: install_release.sh <sha12> <stage_dir>
#   <sha12>      first 12 hex chars of the git sha; names the release dir.
#   <stage_dir>  the untarred release bundle (see the `bundle` job in
#                .github/workflows/cd.yml): one hermes-*.whl,
#                requirements.txt, config/, deploy/, docs/, bin/.
#
# Runs on the VPS as root, over SSH from cd.yml. Layout it maintains:
#
#   /opt/hermes/versions/<sha12>/   .venv, config/, deploy/, docs/, .installed
#   /opt/hermes/current            -> versions/<sha12>   (what systemd runs)
#   /opt/hermes/previous           -> the release current replaced
#   /opt/hermes/bin/               health_gate.sh, rollback.sh,
#                                  hermes-notify-failure.sh,
#                                  hermes-notify-crash.sh
#   /opt/hermes/python/            uv-managed Python 3.14
#   /var/lib/hermes/backups/       pre-<sha12>.sqlite3 ledger snapshots
#
# Steps, in order:
#   1. Preflight: nothing on disk changes until every check passes.
#   2. Install into versions/<sha12> unless its .installed marker exists.
#      The venv is built at its final path because a venv is not relocatable.
#   2b. (prod) Wait out the 4h bar close: no restart from QUIET_BEFORE
#      seconds before a close until QUIET_AFTER seconds after it (#626).
#      The wait sits here, right before the steps that stop or restart
#      the unit, because the CD gate's own hold runs minutes earlier.
#   3. Snapshot the ledger (SQLite online backup, as `hermes`) and keep
#      the newest BACKUPS_KEPT. The new release may migrate the schema at
#      start; the snapshot is the way back. A failed snapshot stops the
#      deploy before anything goes live.
#   3b. If the new release has a destructive migration pending (#447), stop
#      the unit and run it through `hermes migrate` with the new release's
#      binary. Serve never applies those at start. A failed migration rolls
#      back inside its transaction; the old release restarts and the deploy
#      stops.
#   3c. (prod) Install the release's systemd units and timers whose content
#      differs from /etc/systemd/system, as vps-bootstrap does (#626). The
#      replaced files are kept, so a failed gate puts them back.
#   4. Flip previous, flip current, restart the unit.
#   5. Run the health gate. On failure roll back, restoring the snapshot
#      (or stop the unit on a first deploy), and exit 1.
#   6. On success refresh /opt/hermes/bin and prune old releases.
#
# Target (#192): HERMES_TARGET picks the install, `prod` (default, the
# layout above) or `staging` (/opt/hermes-staging, /var/lib/hermes-staging,
# /etc/hermes/env.staging, hermes-staging.service, user hermes-staging).
# A staging run refuses to start when any path it would write, its unit or
# its user is prod's, or when its env file does not say HERMES_ENV=staging.
# By hand:
#
#   HERMES_TARGET=staging bash <stage>/bin/install_release.sh <sha12> <stage>
#
# Test seams: HERMES_ROOT, HERMES_ENV_FILE, SYSTEMCTL, HERMES_DB,
# HERMES_BACKUP_DIR, HERMES_BACKUP_PYTHON, HERMES_MIGRATE_BIN, RUNUSER,
# HERMES_UNIT_DIR, HERMES_NOW_EPOCH. The
# seams override the target's paths, and the staging guard still checks them.
# The script can be sourced to reach prune_versions on its own.
#
# Reference: openspec/changes/179-cd-first-real-deploy/change.md (D-1..D-7),
# openspec/changes/192-staging-env/change.md (D-3).

set -euo pipefail

PROD_ROOT=/opt/hermes
PROD_STATE=/var/lib/hermes
PROD_ENV_FILE=/etc/hermes/env.production
PROD_UNIT=hermes.service
PROD_RUN_AS=hermes

TARGET=${HERMES_TARGET:-prod}
case "$TARGET" in
    prod)
        target_root=$PROD_ROOT
        target_state=$PROD_STATE
        target_env_file=$PROD_ENV_FILE
        UNIT=$PROD_UNIT
        RUN_AS=$PROD_RUN_AS
        ;;
    staging)
        target_root=/opt/hermes-staging
        target_state=/var/lib/hermes-staging
        target_env_file=/etc/hermes/env.staging
        UNIT=hermes-staging.service
        RUN_AS=hermes-staging
        ;;
    *)
        echo "::error::unknown HERMES_TARGET '$TARGET': expected prod or staging"
        exit 1
        ;;
esac

ROOT=${HERMES_ROOT:-$target_root}
ENV_FILE=${HERMES_ENV_FILE:-$target_env_file}
SYSTEMCTL=${SYSTEMCTL:-systemctl}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DB=${HERMES_DB:-$target_state/hermes.sqlite3}
BACKUP_DIR=${HERMES_BACKUP_DIR:-$target_state/backups}
RUNUSER=${RUNUSER:-runuser}
UNIT_DIR=${HERMES_UNIT_DIR:-/etc/systemd/system}

# The 3 newest releases survive a prune, plus whatever `current` and
# `previous` point to.
KEEP_NEWEST=3
# Ledger snapshots kept; one per deploy, newest first.
BACKUPS_KEPT=5
# Bars close every 4h at 00/04/08/12/16/20 UTC. serve handles a bar seconds
# after its close, and a bar handled more than 5 minutes late fills against
# a synthetic book, where the stale-book rule skips new entries. So the
# unit is never stopped from 5 minutes before a close until 5 after it.
BAR_SECONDS=14400
QUIET_BEFORE=300
QUIET_AFTER=300

# uv runs as root (the service must not be able to rewrite its own code),
# so the managed Python lives where `hermes` can read it. Under /root it
# would be unreachable because hermes.service sets ProtectHome=true.
# Staging builds its venvs on prod's interpreter: vps-bootstrap installs only
# that one, it is root-owned 0755, and nothing here writes to it.
if [ "$TARGET" = staging ]; then
    export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$PROD_ROOT/python}
else
    export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$ROOT/python}
fi
export UV_PYTHON_DOWNLOADS=never
# Never fall back to a system interpreter: the venv python must link into $UV_PYTHON_INSTALL_DIR.
export UV_PYTHON_PREFERENCE=only-managed

# Set while a release directory is half-built; the EXIT trap removes it.
partial_dir=""
# Path of the single wheel in the stage, set by preflight.
WHEEL=""
# Set while unit files are replaced: the old copies, and the units that are new.
unit_backup=""
units_added=()

log() {
    echo "[install_release] $*"
}

die() {
    echo "::error::$*"
    exit 1
}

# True when $1, symlinks resolved, is $2 or lies under it.
is_within() {
    local path base
    path=$(realpath -m -- "$1")
    base=$(realpath -m -- "$2")
    [ "$path" = "$base" ] || [ "${path#"$base"/}" != "$path" ]
}

# The value of the last HERMES_ENV= line in $1 (what systemd ends up
# setting), unquoted; empty when there is none.
env_file_tier() {
    local line value=""
    local assign="^[[:space:]]*HERMES_ENV[[:space:]]*=[[:space:]]*[\"']?([^\"'[:space:]]*)"
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" =~ $assign ]]; then
            value=${BASH_REMATCH[1]}
        fi
    done <"$1"
    echo "$value"
}

# Staging must never reach prod, whatever a seam or a typo says: no write
# path inside prod's code or state tree, not prod's env file, unit or user.
guard_staging() {
    local path
    [ "$TARGET" = staging ] || return 0
    for path in "$ROOT" "$DB" "$BACKUP_DIR"; do
        if is_within "$path" "$PROD_ROOT" || is_within "$path" "$PROD_STATE"; then
            die "staging target resolves $path inside prod's $PROD_ROOT or $PROD_STATE: refusing"
        fi
    done
    if is_within "$ENV_FILE" "$PROD_ENV_FILE"; then
        die "staging target reads prod's env file $ENV_FILE: refusing"
    fi
    [ "$UNIT" != "$PROD_UNIT" ] || die "staging target would restart prod's $PROD_UNIT: refusing"
    [ "$RUN_AS" != "$PROD_RUN_AS" ] || die "staging target would run as prod's user $PROD_RUN_AS: refusing"
}

cleanup_partial() {
    if [ -n "$partial_dir" ]; then
        rm -rf "$partial_dir"
    fi
}

preflight() {
    local sha=$1 stage=$2 mode
    guard_staging
    [[ "$sha" =~ ^[0-9a-f]{12}$ ]] || die "version id '$sha' is not 12 lowercase hex chars"
    [ -d "$stage" ] || die "stage dir $stage missing"

    command -v uv >/dev/null 2>&1 || die "uv not on PATH: run vps-bootstrap"
    id "$RUN_AS" >/dev/null 2>&1 || die "user $RUN_AS missing: run vps-bootstrap"
    "$SYSTEMCTL" cat "$UNIT" >/dev/null 2>&1 || die "$UNIT is not installed: run vps-bootstrap"

    [ -f "$ENV_FILE" ] || die "$ENV_FILE missing: run vps-bootstrap"
    mode=$(stat -c '%a %U:%G' "$ENV_FILE")
    [ "$mode" = "600 root:root" ] || die "$ENV_FILE is '$mode', expected '600 root:root'"
    if [ "$TARGET" = staging ]; then
        local tier
        tier=$(env_file_tier "$ENV_FILE")
        [ "$tier" = staging ] || die "$ENV_FILE sets HERMES_ENV='$tier', expected 'staging': refusing"
    fi

    # Every spelling the config loader reads as true (loader.py _coerce_scalar,
    # plus 1, which pydantic coerces to True), quoted or not.
    local mainnet_on="^[[:space:]]*HERMES_ALLOW_MAINNET[[:space:]]*=[[:space:]]*[\"']?(yes|true|on|1)[\"']?[[:space:]]*$"
    if grep -Eiq "$mainnet_on" "$ENV_FILE"; then
        die "$ENV_FILE enables HERMES_ALLOW_MAINNET: refusing to deploy"
    fi

    shopt -s nullglob
    local wheels=("$stage"/hermes-*.whl)
    shopt -u nullglob
    [ "${#wheels[@]}" -eq 1 ] || die "expected exactly one hermes-*.whl in $stage, found ${#wheels[@]}"
    [ -f "${wheels[0]}" ] || die "${wheels[0]} is not a file"
    WHEEL=${wheels[0]}
    [ -f "$stage/requirements.txt" ] || die "$stage/requirements.txt missing"
    [ -d "$stage/config" ] || die "$stage/config missing"
    [ -d "$stage/deploy" ] || die "$stage/deploy missing"
    # hermes serve reads both from the release root; without them it exits 3.
    [ -f "$stage/docs/experiments/swing-mvp1/pre-registration-v1.md" ] \
        || die "$stage/docs/experiments/swing-mvp1/pre-registration-v1.md missing"
    [ -f "$stage/docs/experiments/swing-mvp1/fee-history.csv" ] \
        || die "$stage/docs/experiments/swing-mvp1/fee-history.csv missing"
    [ -f "$stage/deploy/hermes-notify-failure.sh" ] || die "$stage/deploy/hermes-notify-failure.sh missing"
    [ -f "$stage/deploy/hermes-notify-crash.sh" ] || die "$stage/deploy/hermes-notify-crash.sh missing"
    [ -f "$SCRIPT_DIR/health_gate.sh" ] || die "$SCRIPT_DIR/health_gate.sh missing"
    [ -f "$SCRIPT_DIR/rollback.sh" ] || die "$SCRIPT_DIR/rollback.sh missing"
}

# Build versions/<sha> from the stage. `.installed` is written last, so an
# interrupted build is never mistaken for a finished one.
install_version() {
    local vdir=$1 stage=$2
    partial_dir=$vdir
    rm -rf "$vdir"
    mkdir -p "$vdir"
    uv venv --python 3.14 "$vdir/.venv"
    uv pip install --python "$vdir/.venv/bin/python" --require-hashes --compile-bytecode \
        -r "$stage/requirements.txt"
    uv pip install --python "$vdir/.venv/bin/python" --no-deps --no-cache "$WHEEL"
    cp -r "$stage/config" "$vdir/config"
    cp -r "$stage/deploy" "$vdir/deploy"
    cp -r "$stage/docs" "$vdir/docs"
    touch "$vdir/.installed"
    partial_dir=""
}

# Print how many seconds to wait so a restart starting now stays clear of
# the bar close: QUIET_BEFORE before it through QUIET_AFTER after it.
quiet_bar_wait() {
    local now=$1 into
    into=$((now % BAR_SECONDS))
    if [ "$into" -ge $((BAR_SECONDS - QUIET_BEFORE)) ]; then
        echo $((BAR_SECONDS - into + QUIET_AFTER))
    elif [ "$into" -lt "$QUIET_AFTER" ]; then
        echo $((QUIET_AFTER - into))
    else
        echo 0
    fi
}

# Hold the deploy until the bar close is clear. Staging runs its own unit and
# trades nothing, so only prod waits.
wait_for_quiet_bar() {
    local wait
    [ "$TARGET" = prod ] || return 0
    wait=$(quiet_bar_wait "${HERMES_NOW_EPOCH:-$(date -u +%s)}")
    if [ "$wait" -gt 0 ]; then
        log "holding ${wait}s for the 4h bar close"
        sleep "$wait"
    fi
}

# Snapshot the ledger before the new release can touch it. SQLite's online
# backup API copies a consistent image while hermes.service keeps writing,
# unlike `cp`, which can catch a half-written page or miss the WAL. It runs
# as `hermes`, which owns the database, through the new release's own
# Python (no sqlite3 CLI on the box). Prints the snapshot path, or nothing
# when there is no ledger yet (first deploy).
backup_ledger() {
    local sha=$1 py=$2 out tmp
    if [ ! -f "$DB" ]; then
        log "no ledger at $DB yet: nothing to back up" >&2
        return 0
    fi
    install -d -m 0750 -o "$RUN_AS" -g "$RUN_AS" "$BACKUP_DIR" 2>/dev/null || install -d -m 0750 "$BACKUP_DIR"
    out="$BACKUP_DIR/pre-$sha.sqlite3"
    tmp="$out.partial"
    "$RUNUSER" -u "$RUN_AS" -- "$py" -c '
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close()
src.close()
' "$DB" "$tmp" >&2 || { rm -f "$tmp"; return 1; }
    mv -f "$tmp" "$out"
    log "ledger snapshot: $out" >&2
    prune_backups
    echo "$out"
}

# Keep the BACKUPS_KEPT newest snapshots by mtime.
prune_backups() {
    local file n=0
    while IFS= read -r file; do
        n=$((n + 1))
        if [ "$n" -gt "$BACKUPS_KEPT" ]; then
            log "pruning snapshot $file" >&2
            rm -f "$file"
        fi
    done < <(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type f -name 'pre-*.sqlite3' -printf '%T@ %p\n' \
        | LC_ALL=C sort -rn | cut -d' ' -f2-)
}

# Run pending destructive migrations through `hermes migrate` (#447). They
# never run at serve start, so without this step the new release would fail
# its health gate on an existing ledger. Non-destructive migrations still run
# at start as before. The unit is stopped first because `hermes migrate`
# needs the ledger to itself; it restarts in activate_and_gate.
migrate_ledger() {
    local vdir=$1 bin status
    if [ ! -f "$DB" ]; then
        return 0
    fi
    bin=${HERMES_MIGRATE_BIN:-$vdir/.venv/bin/hermes}
    status=$("$RUNUSER" -u "$RUN_AS" -- "$bin" migrate --db "$DB" --status) || return 1
    case "$status" in
        *"(destructive)"*) ;;
        *) return 0 ;;
    esac
    log "destructive migration pending: stopping $UNIT and running hermes migrate"
    "$SYSTEMCTL" stop "$UNIT" || return 1
    "$RUNUSER" -u "$RUN_AS" -- "$bin" migrate --db "$DB" --snapshot-dir "$BACKUP_DIR/migrations"
}

# Install every *.service and *.timer from the release's deploy/systemd whose
# content differs from the copy in UNIT_DIR, 0644, as vps_bootstrap.sh step 4
# does. Without this a unit change merged to main waits for a manual bootstrap
# (#626). The replaced files go to a backup dir so restore_units can put them
# back. Enabling a new unit stays with vps-bootstrap: whether a timer runs
# depends on the env file, which a deploy never touches. Staging shares prod's
# unit dir, so only a prod deploy syncs. activate_and_gate reloads systemd.
sync_units() {
    local src_dir=$1/deploy/systemd src dst name
    [ "$TARGET" = prod ] || return 0
    if [ ! -d "$src_dir" ]; then
        log "release carries no deploy/systemd: units left as they are"
        return 0
    fi
    unit_backup="$ROOT/units-before-deploy"
    rm -rf "$unit_backup"
    install -d -m 0700 "$unit_backup" || return 1
    for src in "$src_dir"/*.service "$src_dir"/*.timer; do
        [ -f "$src" ] || continue
        name=$(basename "$src")
        dst="$UNIT_DIR/$name"
        if cmp -s "$src" "$dst"; then
            continue
        fi
        if [ -f "$dst" ]; then
            cp -p "$dst" "$unit_backup/$name" || return 1
        else
            units_added+=("$name")
            echo "::warning::new unit $name installed but not enabled: run vps-bootstrap to enable it"
        fi
        install -m 0644 "$src" "$dst" || return 1
        log "unit $name: installed"
    done
}

# Put back the unit files sync_units replaced and remove the ones it added.
# The caller reloads systemd (rollback.sh does, before it restarts the unit).
restore_units() {
    local saved name
    [ -n "$unit_backup" ] || return 0
    for saved in "$unit_backup"/*; do
        [ -f "$saved" ] || continue
        install -m 0644 "$saved" "$UNIT_DIR/$(basename "$saved")"
        log "unit $(basename "$saved"): restored"
    done
    for name in ${units_added[@]+"${units_added[@]}"}; do
        rm -f "$UNIT_DIR/$name"
        log "unit $name: removed"
    done
    rm -rf "$unit_backup"
    unit_backup=""
}

# Atomic symlink swap: write a temp link, then rename(2) it over the real one.
flip_link() {
    local target=$1 link=$2
    ln -sfn "$target" "$link.tmp"
    mv -T "$link.tmp" "$link"
}

# Restart the unit and gate it. Returns 1 instead of exiting so the caller
# can roll back; `|| return 1` because `set -e` is off inside an `if`.
activate_and_gate() {
    local since
    since=$(date +%s)
    "$SYSTEMCTL" daemon-reload || return 1
    # reset-failed first: the start limit in hermes.service (5 starts per
    # 10 min) would refuse this restart after an earlier crash loop.
    "$SYSTEMCTL" reset-failed "$UNIT" || return 1
    "$SYSTEMCTL" restart "$UNIT" || return 1
    bash "$SCRIPT_DIR/health_gate.sh" "$since"
}

# Delete every release directory except the KEEP_NEWEST newest by mtime and
# the two that `current` and `previous` point to. Sorting by mtime, not by
# name, because sha prefixes carry no order. LC_ALL=C pins sort's decimal point.
prune_versions() {
    local keep_cur="" keep_prev="" dir name n=0
    if [ -L "$ROOT/current" ]; then
        keep_cur=$(basename "$(readlink -f "$ROOT/current")")
    fi
    if [ -L "$ROOT/previous" ]; then
        keep_prev=$(basename "$(readlink -f "$ROOT/previous")")
    fi
    while IFS= read -r dir; do
        n=$((n + 1))
        name=$(basename "$dir")
        if [ "$n" -le "$KEEP_NEWEST" ] || [ "$name" = "$keep_cur" ] || [ "$name" = "$keep_prev" ]; then
            continue
        fi
        log "pruning $dir"
        rm -rf "$dir"
    done < <(find "$ROOT/versions" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
        | LC_ALL=C sort -rn | cut -d' ' -f2-)
}

main() {
    local sha=${1:?usage: install_release.sh <sha12> <stage_dir>}
    local stage=${2:?usage: install_release.sh <sha12> <stage_dir>}
    local vdir="$ROOT/versions/$sha" cur="" prev_ok=false snapshot=""

    preflight "$sha" "$stage"
    trap cleanup_partial EXIT

    if [ -f "$vdir/.installed" ]; then
        log "release $sha already installed: skipping install"
    else
        log "installing release $sha"
        install_version "$vdir" "$stage"
    fi

    wait_for_quiet_bar

    snapshot=$(backup_ledger "$sha" "${HERMES_BACKUP_PYTHON:-$vdir/.venv/bin/python}") \
        || die "ledger snapshot of $DB failed: not deploying $sha"

    if ! migrate_ledger "$vdir"; then
        # current still points at the old release and the failed migration
        # rolled back in its transaction, so the old release can run as before.
        "$SYSTEMCTL" start "$UNIT" || true
        die "hermes migrate failed on $DB: not deploying $sha"
    fi

    if ! sync_units "$stage"; then
        # Nothing reloaded systemd yet, so the old units still run the old release.
        restore_units
        "$SYSTEMCTL" start "$UNIT" || true
        die "installing the release's systemd units failed: not deploying $sha"
    fi

    # `readlink -f` exits 0 on a missing link, so `current` needs `-L`.
    # Without it the first deploy would write `previous -> current`.
    if [ -L "$ROOT/current" ]; then
        cur=$(readlink -f "$ROOT/current")
    fi
    # Refresh mtime so a redeployed old release counts as newest for pruning.
    touch "$vdir"
    # `-ef` compares device and inode, so two spellings of one path still match.
    if [ -n "$cur" ] && ! [ "$ROOT/current" -ef "$vdir" ]; then
        flip_link "$cur" "$ROOT/previous"
    fi
    flip_link "$vdir" "$ROOT/current"
    log "current -> $(readlink "$ROOT/current")"

    if ! activate_and_gate; then
        echo "::error::release $sha failed the health gate"
        if [ -L "$ROOT/previous" ] && ! [ "$ROOT/previous" -ef "$ROOT/current" ]; then
            prev_ok=true
        fi
        restore_units
        if [ "$prev_ok" = true ]; then
            # The failed release may have migrated the ledger; the
            # snapshot puts it back to what `previous` expects.
            HERMES_RESTORE_DB=$snapshot bash "$SCRIPT_DIR/rollback.sh" || echo "::error::rollback failed too"
        else
            log "no previous release: stopping $UNIT"
            "$SYSTEMCTL" daemon-reload || true
            "$SYSTEMCTL" stop "$UNIT" || true
        fi
        exit 1
    fi

    # Copied only after the gate passes, so a bad release never replaces the
    # scripts a manual rollback depends on, nor the failure alert, which runs
    # from here so it survives a broken release (#364).
    install -d -m 0755 "$ROOT/bin"
    install -m 0755 "$SCRIPT_DIR/health_gate.sh" "$SCRIPT_DIR/rollback.sh" \
        "$stage/deploy/hermes-notify-failure.sh" "$stage/deploy/hermes-notify-crash.sh" \
        "$ROOT/bin/"
    rm -rf "$unit_backup"
    prune_versions
    # The read-only bot runs from the same release; pick up the new code.
    # try-restart leaves a stopped or uninstalled bot alone (ADR-015). The bot
    # runs from prod's release, so a staging deploy must not touch it (#192).
    if [ "$TARGET" = prod ]; then
        "$SYSTEMCTL" try-restart hermes-bot.service || true
    fi
    log "release $sha is live"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
