#!/usr/bin/env bash
# Nightly backup: L2 Parquet older than 30 days + SQLite snapshots.
#
# Ships to Hetzner Storage Box via borg (encrypted, deduplicated). Fails loud
# on any error.
#
# The VPS only ever adds archives: its Storage Box key is append-only (#775),
# so a compromised server cannot delete old backups. That is also why this
# script never prunes: pruning through an append-only key only marks archives
# deleted, and the next write from a full-access client makes it real. Prune
# from a trusted machine instead (deploy/hetzner-storagebox-setup.md,
# "Prune from a trusted machine").
#
#   hermes-backup.sh                  nightly backup (hermes-backup.timer, 03:00 UTC)
#   hermes-backup.sh --init           create the borg repo, once
#   hermes-backup.sh --restore-drill  restore the newest archive's SQLite
#                                     snapshots to a scratch dir and compare
#                                     them with the live databases
# Env vars sourced from /etc/hermes/env.production (loaded by systemd EnvironmentFile).
#
# Prerequisites (see deploy/hetzner-storagebox-setup.md):
#   1. borgbackup >= 1.2 installed on VPS (apt install borgbackup)
#   2. Storage Box SSH pubkey uploaded, port 23 reachable
#   3. Borg repo initialised once: hermes-backup.sh --init
#   4. /etc/hermes/env.production populated with HERMES_BACKUP_* + BORG_PASSPHRASE

set -euo pipefail

: "${HERMES_BACKUP_BORG_REPO:?must be set in /etc/hermes/env.production}"
: "${BORG_PASSPHRASE:?must be set in /etc/hermes/env.production}"
: "${HERMES_BACKUP_HEALTHCHECK_URL:=}"

export BORG_REPO="${HERMES_BACKUP_BORG_REPO}"
# A key of its own, so the Storage Box can tie it to `borg serve --append-only`
# without touching any other key the hermes user may hold.
SSH_KEY="${HERMES_BACKUP_SSH_KEY:-/var/lib/hermes/.ssh/hermes-backup_ed25519}"
export BORG_RSH="ssh -p ${HERMES_BACKUP_SSH_PORT:-23} -i ${SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

DATA_DIR="${HERMES_DATA_DIR:-/var/lib/hermes}"
SNAPSHOT_DIR="$(mktemp -d -t hermes-backup-XXXXXX)"
ARCHIVE_NAME="hermes-{now:%Y-%m-%dT%H:%M:%S}"

cleanup() { rm -rf "${SNAPSHOT_DIR}"; }
trap cleanup EXIT

log()  { echo "[hermes-backup $(date -Iseconds)] $*"; }
fail() { log "FAIL: $*" >&2; exit 1; }
# The ping URL carries the check's secret key, so it goes to curl as a config
# on stdin (printf is a builtin, so no argv either): `ps` shows argv to every
# local user. Escape for a double-quoted config value first.
ping() {
    local url="${HERMES_BACKUP_HEALTHCHECK_URL}$1"
    [ -n "${HERMES_BACKUP_HEALTHCHECK_URL}" ] || return 0
    url="${url//\\/\\\\}"
    url="${url//\"/\\\"}"
    printf 'url = "%s"\n' "$url" \
        | curl -fsS -m 10 --retry 3 -o /dev/null --config - || true
}

if [ "${1:-}" = "--init" ]; then
    log "Initialising borg repo at ${BORG_REPO}"
    borg init --encryption=repokey-blake2 || fail "borg init failed"
    log "Repo initialised. Save the passphrase to a password manager."
    exit 0
fi

# Archives hold the snapshots as sqlite/<name>, the same path every night, so
# a restore can name them without knowing the run's temp dir.
restore_drill() {
    local archive
    archive="$(borg list --last 1 --format '{archive}{NL}' --glob-archives 'hermes-*')" \
        || fail "borg list failed"
    [ -n "${archive}" ] || fail "no hermes- archive in ${BORG_REPO}"
    log "Restoring sqlite/ from ${archive} into ${SNAPSHOT_DIR}"
    (cd "${SNAPSHOT_DIR}" && borg extract "::${archive}" sqlite) \
        || fail "borg extract failed"
    python3 -I - "${SNAPSHOT_DIR}/sqlite" "${DATA_DIR}" <<'PY' || fail "restore drill found a problem"
import sqlite3
import sys
from pathlib import Path

restored_dir, live_dir = Path(sys.argv[1]), Path(sys.argv[2])
problems = 0
restored = sorted(restored_dir.iterdir()) if restored_dir.is_dir() else []
if not restored:
    print("FAIL: the archive holds no SQLite snapshot")
    problems += 1
for copy in restored:
    db = sqlite3.connect(f"{copy.as_uri()}?mode=ro", uri=True)
    check = db.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"{copy.name}: integrity {check}")
    if check != "ok":
        problems += 1
    live_path = live_dir / copy.name
    if not live_path.is_file():
        print(f"  FAIL: no live {copy.name} in {live_dir}")
        problems += 1
        continue
    live = sqlite3.connect(f"{live_path.as_uri()}?mode=ro", uri=True)
    tables = [row[0] for row in db.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'table'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        restored_rows = db.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
        try:
            live_rows = live.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
        except sqlite3.OperationalError:
            print(f"  FAIL: {table} is in the backup but not in the live database")
            problems += 1
            continue
        verdict = "same" if restored_rows == live_rows else "differs (rows written since the backup?)"
        print(f"  {table}: restored {restored_rows}, live {live_rows}, {verdict}")
sys.exit(1 if problems else 0)
PY
    log "Restore drill OK"
}

if [ "${1:-}" = "--restore-drill" ]; then
    restore_drill
    exit 0
fi

ping "/start"

log "Snapshotting SQLite databases via sqlite3 .backup"
mkdir -p "${SNAPSHOT_DIR}/sqlite"
for db in "${DATA_DIR}"/*.db "${DATA_DIR}"/*.sqlite3; do
    [ -f "${db}" ] || continue
    dbname="$(basename "${db}")"
    log "  → ${dbname}"
    sqlite3 "${db}" ".backup '${SNAPSHOT_DIR}/sqlite/${dbname}'" \
        || fail "sqlite backup failed for ${db}"
done

log "Creating borg archive ${ARCHIVE_NAME}"
(
    cd "${SNAPSHOT_DIR}" && borg create \
        --stats \
        --compression zstd,6 \
        --exclude-caches \
        "::${ARCHIVE_NAME}" \
        sqlite \
        "${DATA_DIR}/l2"
) || fail "borg create failed"

log "Backup OK"
ping ""
