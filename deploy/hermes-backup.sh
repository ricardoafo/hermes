#!/usr/bin/env bash
# Nightly backup: L2 Parquet older than 30 days + SQLite snapshots.
#
# Ships to Hetzner Storage Box via borg (encrypted, deduplicated).
# Retention: 7 daily / 4 weekly / 12 monthly. Fails loud on any error.
#
# Invoked by hermes-backup.timer at 03:00 UTC daily.
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
export BORG_RSH="ssh -p ${HERMES_BACKUP_SSH_PORT:-23} -o StrictHostKeyChecking=accept-new"

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
borg create \
    --stats \
    --compression zstd,6 \
    --exclude-caches \
    "::${ARCHIVE_NAME}" \
    "${SNAPSHOT_DIR}/sqlite" \
    "${DATA_DIR}/l2" \
    || fail "borg create failed"

log "Pruning old archives (7 daily / 4 weekly / 12 monthly)"
borg prune \
    --stats \
    --keep-daily 7 \
    --keep-weekly 4 \
    --keep-monthly 12 \
    --prefix "hermes-" \
    || fail "borg prune failed"

log "Compacting repo"
borg compact || fail "borg compact failed"

log "Backup OK"
ping ""
