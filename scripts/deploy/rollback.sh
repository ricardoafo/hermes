#!/usr/bin/env bash
# =====================================================================
# Rollback — flip `current` back to `previous` and re-gate
# =====================================================================
#
# Usage: rollback.sh
#   Exit 0 = rolled back and the gate passed, exit 1 = refused or gate failed.
#
# Runs on the VPS as root: automatically from install_release.sh when
# the health gate fails, and by hand over SSH:
#
#   ssh hermes-01 /opt/hermes/bin/rollback.sh
#
# It leaves `previous` alone, so a second run refuses (current already
# equals previous) instead of toggling between two releases. After an
# automatic rollback `previous` equals `current`; the next good deploy
# repairs it. It never touches /var/lib/hermes: the SQLite ledger is
# shared across versions, so a release that migrated the schema needs a
# backup-before-activate step first (follow-up, see the change spec).
#
# Test seams: HERMES_ROOT, SYSTEMCTL (health_gate.sh adds its own).
#
# Reference: openspec/changes/179-cd-first-real-deploy/change.md (D-6).

set -euo pipefail

ROOT=${HERMES_ROOT:-/opt/hermes}
SYSTEMCTL=${SYSTEMCTL:-systemctl}
UNIT=hermes.service
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

die() {
    echo "::error::$*"
    exit 1
}

# `readlink -f` exits 0 on a missing link, so presence needs `-L`.
[ -L "$ROOT/current" ] || die "no current release at $ROOT/current"
[ -L "$ROOT/previous" ] || die "no previous release at $ROOT/previous: nothing to roll back to"

cur=$(readlink -f "$ROOT/current")
prev=$(readlink -f "$ROOT/previous")

[ -f "$prev/.installed" ] || die "previous ($prev) has no .installed marker: it was pruned or is partial"
# `-ef` compares device and inode, so two spellings of one path still match.
if [ "$ROOT/current" -ef "$ROOT/previous" ]; then
    die "current already equals previous ($cur): nothing to roll back to"
fi

echo "rolling back: current $cur -> $prev"
# Temp link plus `mv -T`: rename(2) swaps the link atomically, so the unit
# never sees a missing `current`.
ln -sfn "$prev" "$ROOT/current.tmp"
mv -T "$ROOT/current.tmp" "$ROOT/current"

since=$(date +%s)
"$SYSTEMCTL" daemon-reload
# A start limit tripped by an earlier crash loop would refuse this restart.
"$SYSTEMCTL" reset-failed "$UNIT"
"$SYSTEMCTL" restart "$UNIT"

if ! bash "$SCRIPT_DIR/health_gate.sh" "$since"; then
    die "rolled back to $prev but the health gate failed: inspect journalctl -u $UNIT"
fi
echo "rollback OK: current -> $prev"
