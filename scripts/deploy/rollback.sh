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
# repairs it.
#
# The SQLite ledger is shared across versions, and a release may migrate
# it at start. With HERMES_RESTORE_DB=<snapshot> (install_release.sh sets
# it to the pre-<sha12> snapshot it took), the unit is stopped and the
# snapshot copied over the ledger before the old release starts. Without
# it the ledger is left alone. By hand:
#
#   ssh hermes-01 HERMES_RESTORE_DB=/var/lib/hermes/backups/pre-<sha12>.sqlite3 \
#       /opt/hermes/bin/rollback.sh
#
# Test seams: HERMES_ROOT, SYSTEMCTL, HERMES_DB (health_gate.sh adds its own).
#
# Reference: openspec/changes/179-cd-first-real-deploy/change.md (D-6).

set -euo pipefail

ROOT=${HERMES_ROOT:-/opt/hermes}
SYSTEMCTL=${SYSTEMCTL:-systemctl}
UNIT=hermes.service
DB=${HERMES_DB:-/var/lib/hermes/hermes.sqlite3}
RESTORE=${HERMES_RESTORE_DB:-}
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
if [ -n "$RESTORE" ] && [ ! -f "$RESTORE" ]; then
    die "snapshot $RESTORE not found: not rolling back onto a ledger it cannot restore"
fi
# `-ef` compares device and inode, so two spellings of one path still match.
if [ "$ROOT/current" -ef "$ROOT/previous" ]; then
    die "current already equals previous ($cur): nothing to roll back to"
fi

echo "rolling back: current $cur -> $prev"
# Temp link plus `mv -T`: rename(2) swaps the link atomically, so the unit
# never sees a missing `current`.
ln -sfn "$prev" "$ROOT/current.tmp"
mv -T "$ROOT/current.tmp" "$ROOT/current"

if [ -n "$RESTORE" ]; then
    # Stop first: copying under a running writer would tear the file. The
    # -wal/-shm files belong to the replaced image and must go with it.
    "$SYSTEMCTL" stop "$UNIT"
    cp -f "$RESTORE" "$DB.restore"
    chown --reference="$RESTORE" "$DB.restore" 2>/dev/null || true
    mv -f "$DB.restore" "$DB"
    rm -f "$DB-wal" "$DB-shm"
    echo "restored ledger $DB from $RESTORE"
fi

since=$(date +%s)
"$SYSTEMCTL" daemon-reload
# A start limit tripped by an earlier crash loop would refuse this restart.
"$SYSTEMCTL" reset-failed "$UNIT"
"$SYSTEMCTL" restart "$UNIT"

if ! bash "$SCRIPT_DIR/health_gate.sh" "$since"; then
    die "rolled back to $prev but the health gate failed: inspect journalctl -u $UNIT"
fi
echo "rollback OK: current -> $prev"
