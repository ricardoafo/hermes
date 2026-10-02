# Hetzner Storage Box — Setup Runbook

Nightly backup destination for `hermes-01`. Storage Box `hermes-storage-box-1`
in Falkenstein (same DC as VPS, ~1ms latency), user `uXXXXXX`, SSH on port 23.

Encrypted, deduplicated backups via `borg`. Retention: 7 daily / 4 weekly / 12
monthly. Fires at 03:00 UTC via `hermes-backup.timer`.

## 1. Order + configure Storage Box (Hetzner Robot)

One-time, done by operator at [robot.hetzner.com](https://robot.hetzner.com):

1. Storage Boxes → order **BX11** (1 TB, ~€3.81/mo).
2. Location: **FSN1** (Falkenstein) to match `hermes-01`.
3. In the box's settings enable:
   - **SSH support** (needed for borg over SSH)
   - **ZFS snapshots** (defence-in-depth against a ransomed VPS pushing empty repos)
4. SSH tab → upload `hermes-01`'s deploy user pubkey:

   ```bash
   # On hermes-01:
   sudo -u hermes-deploy cat /home/hermes-deploy/.ssh/id_ed25519.pub
   ```

   Paste into the Storage Box authorized-keys form.

## 2. Install borg on the VPS

```bash
# On hermes-01, as root:
apt update && apt install -y borgbackup sqlite3
borg --version    # expect >= 1.2
```

`bootstrap-vps.sh` should be updated in a follow-up to include this — for now
it's a manual step.

## 3. Populate `/etc/hermes/env.production`

Add these lines (see `deploy/systemd/hermes.env.example` for template):

```bash
HERMES_BACKUP_BORG_REPO=ssh://uXXXXXX@uXXXXXX.your-storagebox.de:23/./hermes-backup
HERMES_BACKUP_SSH_PORT=23
BORG_PASSPHRASE=<generated in step 4>
# Optional dead-man's-switch:
# HERMES_BACKUP_HEALTHCHECK_URL=https://hc-ping.com/<uuid>
```

## 4. Initialise the borg repo (one-time)

```bash
# On hermes-01, as the hermes user, with env loaded:
sudo -u hermes -E bash
export $(grep -v '^#' /etc/hermes/env.production | xargs)
# Generate a strong passphrase FIRST, save to password manager:
export BORG_PASSPHRASE="$(openssl rand -base64 32)"
echo "PASSPHRASE (SAVE THIS): ${BORG_PASSPHRASE}"

# Then run init:
/opt/hermes/current/deploy/hermes-backup.sh --init
```

After init:

1. Add `BORG_PASSPHRASE=<value>` to `/etc/hermes/env.production` (root-owned, 0600).
2. Save the passphrase to your password manager. **Losing it = unrecoverable backups.**
3. Optional: export the borg key material to a second location:

   ```bash
   borg key export "${BORG_REPO}" borg-key-backup.txt
   # Store borg-key-backup.txt offline (not in git, not on the VPS).
   ```

## 5. Install the systemd units

```bash
# On hermes-01, as root:
cp /opt/hermes/current/deploy/systemd/hermes-backup.service /etc/systemd/system/
cp /opt/hermes/current/deploy/systemd/hermes-backup.timer   /etc/systemd/system/
chmod +x /opt/hermes/current/deploy/hermes-backup.sh
systemctl daemon-reload
systemctl enable --now hermes-backup.timer
```

Verify:

```bash
systemctl list-timers hermes-backup.timer
# Should show next fire at the upcoming 03:00 UTC.
```

## 6. Manual first backup (smoke test)

Don't wait until 03:00 for the first backup — run it now to validate the wiring:

```bash
sudo systemctl start hermes-backup.service
journalctl -u hermes-backup.service -f
# Expect "Backup OK" at the end. Then verify on the Storage Box side:
BORG_PASSPHRASE="..." borg list ssh://uXXXXXX@uXXXXXX.your-storagebox.de:23/./hermes-backup
```

## 7. Restore procedure (drill this once)

```bash
# List archives:
borg list "${HERMES_BACKUP_BORG_REPO}"

# Extract a specific archive to /tmp/restore:
mkdir -p /tmp/restore && cd /tmp/restore
borg extract "${HERMES_BACKUP_BORG_REPO}::hermes-2026-08-27T03:00:00"

# Verify SQLite integrity of a restored DB:
sqlite3 /tmp/restore/sqlite/events.db "PRAGMA integrity_check;"
```

**Run a restore drill within the first week after setup.** A backup you have
never restored is not a backup.

## 8. Disk-usage monitoring

`hermes-01` is 40 GB. At SA-3's L2 growth rate (~20 GB/month for BTC+ETH+SOL
diffs), the VPS fills up in ~2 months even with 30-day rotation to Storage Box.
Add a healthchecks.io check for VPS disk usage that alerts at 75% (30 GB).

Command to add to `hermes-health.service` (or a new `hermes-disk-check` timer):

```bash
DF_PCT=$(df --output=pcent /var/lib/hermes | tail -1 | tr -dc '0-9')
if [ "${DF_PCT}" -gt 75 ]; then
    curl -fsS "https://hc-ping.com/DISK_ALERT_UUID/fail" -d "hermes-01 disk ${DF_PCT}%"
fi
```

Follow-up: upsize hermes-01 to 80 GB before shadow phase (Week 8 per
`docs/experiments/swing-mvp1/success-criteria.md` G7). Hetzner disk expansion
is a click in Cloud Console, ~1 min downtime.

## 9. Cost baseline

| Resource | Monthly cost | Notes |
| ---------- | -------------- | ------- |
| Storage Box BX11 (1 TB, Falkenstein) | ~€3.81 | Pre-paid annually gets ~5% off |
| Estimated egress | €0 | Same-DC as VPS, no bandwidth charge |
| **Total backup infra** | **~€4/mo** | |

## Related

- `deploy/hermes-backup.sh` — the backup script itself
- `deploy/systemd/hermes-backup.{service,timer}` — systemd units
- `deploy/systemd/hermes.env.example` — env var shape
- `docs/decisions/2026-08-23-credential-storage-layered-by-env.md` — why secrets live in `/etc/hermes/env.production`
