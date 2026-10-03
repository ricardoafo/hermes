#!/usr/bin/env bash
# =====================================================================
# Install one hermes release on the VPS, activate it, and gate it
# =====================================================================
#
# Usage: install_release.sh <sha12> <stage_dir>
#   <sha12>      first 12 hex chars of the git sha; names the release dir.
#   <stage_dir>  the untarred release bundle (see the `bundle` job in
#                .github/workflows/cd.yml): one hermes-*.whl,
#                requirements.txt, config/, deploy/, bin/.
#
# Runs on the VPS as root, over SSH from cd.yml. Layout it maintains:
#
#   /opt/hermes/versions/<sha12>/   .venv, config/, deploy/, .installed
#   /opt/hermes/current            -> versions/<sha12>   (what systemd runs)
#   /opt/hermes/previous           -> the release current replaced
#   /opt/hermes/bin/               health_gate.sh, rollback.sh
#   /opt/hermes/python/            uv-managed Python 3.14
#
# Steps, in order:
#   1. Preflight: nothing on disk changes until every check passes.
#   2. Install into versions/<sha12> unless its .installed marker exists.
#      The venv is built at its final path because a venv is not relocatable.
#   3. Flip previous, flip current, restart the unit.
#   4. Run the health gate. On failure roll back (or stop the unit on a
#      first deploy) and exit 1.
#   5. On success refresh /opt/hermes/bin and prune old releases.
#
# Test seams: HERMES_ROOT, HERMES_ENV_FILE, SYSTEMCTL. The script can be
# sourced to reach prune_versions on its own.
#
# Reference: openspec/changes/179-cd-first-real-deploy/change.md (D-1..D-7).

set -euo pipefail

ROOT=${HERMES_ROOT:-/opt/hermes}
ENV_FILE=${HERMES_ENV_FILE:-/etc/hermes/env.production}
SYSTEMCTL=${SYSTEMCTL:-systemctl}
UNIT=hermes.service
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# The 3 newest releases survive a prune, plus whatever `current` and
# `previous` point to.
KEEP_NEWEST=3

# uv runs as root (the service must not be able to rewrite its own code),
# so the managed Python lives where `hermes` can read it. Under /root it
# would be unreachable because hermes.service sets ProtectHome=true.
export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$ROOT/python}
export UV_PYTHON_DOWNLOADS=never
# Never fall back to a system interpreter: the venv python must link into $UV_PYTHON_INSTALL_DIR.
export UV_PYTHON_PREFERENCE=only-managed

# Set while a release directory is half-built; the EXIT trap removes it.
partial_dir=""
# Path of the single wheel in the stage, set by preflight.
WHEEL=""

log() {
    echo "[install_release] $*"
}

die() {
    echo "::error::$*"
    exit 1
}

cleanup_partial() {
    if [ -n "$partial_dir" ]; then
        rm -rf "$partial_dir"
    fi
}

preflight() {
    local sha=$1 stage=$2 mode
    [[ "$sha" =~ ^[0-9a-f]{12}$ ]] || die "version id '$sha' is not 12 lowercase hex chars"
    [ -d "$stage" ] || die "stage dir $stage missing"

    command -v uv >/dev/null 2>&1 || die "uv not on PATH: run vps-bootstrap"
    id hermes >/dev/null 2>&1 || die "user hermes missing: run vps-bootstrap"
    "$SYSTEMCTL" cat "$UNIT" >/dev/null 2>&1 || die "$UNIT is not installed: run vps-bootstrap"

    [ -f "$ENV_FILE" ] || die "$ENV_FILE missing: run vps-bootstrap"
    mode=$(stat -c '%a %U:%G' "$ENV_FILE")
    [ "$mode" = "600 root:root" ] || die "$ENV_FILE is '$mode', expected '600 root:root'"

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
    touch "$vdir/.installed"
    partial_dir=""
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
    local vdir="$ROOT/versions/$sha" cur="" prev_ok=false

    preflight "$sha" "$stage"
    trap cleanup_partial EXIT

    if [ -f "$vdir/.installed" ]; then
        log "release $sha already installed: skipping install"
    else
        log "installing release $sha"
        install_version "$vdir" "$stage"
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
        if [ "$prev_ok" = true ]; then
            bash "$SCRIPT_DIR/rollback.sh" || echo "::error::rollback failed too"
        else
            log "no previous release: stopping $UNIT"
            "$SYSTEMCTL" stop "$UNIT" || true
        fi
        exit 1
    fi

    # Copied only after the gate passes, so a bad release never replaces the
    # scripts a manual rollback depends on.
    install -d -m 0755 "$ROOT/bin"
    install -m 0755 "$SCRIPT_DIR/health_gate.sh" "$SCRIPT_DIR/rollback.sh" "$ROOT/bin/"
    prune_versions
    log "release $sha is live"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
