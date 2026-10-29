# Hetzner Storage Box — Setup Runbook

Nightly backup destination for `hermes-01`. Storage Box `hermes-storage-box-1`
in Falkenstein (same DC as VPS, ~1ms latency), user `u656997`, SSH on port 23.

Encrypted, deduplicated backups via `borg`. Fires at 03:00 UTC via
`hermes-backup.timer`.

hermes-01 can only add archives: its key on the Storage Box runs
`borg serve --append-only`, so a compromised server cannot delete old backups
(#775). Pruning (7 daily / 4 weekly / 12 monthly) runs from a trusted machine
with a separate full-access key, never from the VPS.

## 1. Order + configure Storage Box (Hetzner Robot)

One-time, done by operator at [robot.hetzner.com](https://robot.hetzner.com):

1. Storage Boxes → order **BX11** (1 TB, ~€3.81/mo).
2. Location: **FSN1** (Falkenstein) to match `hermes-01`.
3. In the box's settings enable:
   - **SSH support** (needed for borg over SSH)
   - **ZFS snapshots** (defence-in-depth against a ransomed VPS pushing empty repos)
4. Give the backup its own key. The backup runs as the `hermes` user, whose
   home is `/var/lib/hermes`, so the key lives there:

   ```bash
   # On hermes-01, as root:
   install -d -o hermes -g hermes -m 0700 /var/lib/hermes/.ssh
   sudo -u hermes ssh-keygen -t ed25519 -N '' -C hermes-01-backup \
       -f /var/lib/hermes/.ssh/hermes-backup_ed25519
   cat /var/lib/hermes/.ssh/hermes-backup_ed25519.pub
   ```

5. Put that key in the box's `.ssh/authorized_keys` behind an append-only
   `borg serve`, as one line (replace the last part with the `.pub` output):

   ```text
   command="borg serve --append-only --restrict-to-repository /home/hermes-backup",restrict ssh-ed25519 AAAA... hermes-01-backup
   ```

   `/home/hermes-backup` is how the box sees the repo URL's `./hermes-backup`.
   The Robot upload form and `scp` replace the whole file, so keep the
   trusted machine's full-access key (section 8) in the same file. Section 9
   checks that the box honours the restriction.

## 2. Install borg on the VPS

```bash
# On hermes-01, as root:
apt update && apt install -y borgbackup sqlite3
borg --version    # expect >= 1.2
```

The `vps-bootstrap` workflow does not install borg yet, so this stays a manual
step.

## 3. Populate `/etc/hermes/env.production`

Add these lines (see `deploy/systemd/hermes.env.example` for template):

```bash
HERMES_BACKUP_BORG_REPO=ssh://u656997@u656997.your-storagebox.de:23/./hermes-backup
HERMES_BACKUP_SSH_PORT=23
BORG_PASSPHRASE=<generated in section 4>
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
BORG_PASSPHRASE="..." borg list ssh://u656997@u656997.your-storagebox.de:23/./hermes-backup
```

## 7. Restore drill (run once after the first backup)

```bash
# On hermes-01, as the hermes user, with env loaded (as in section 4):
/opt/hermes/current/deploy/hermes-backup.sh --restore-drill
```

It restores the newest archive's SQLite snapshots (`sqlite/<name>` in every
archive) to a scratch dir, runs `PRAGMA integrity_check` on each, and prints
every table's row count beside the live database's. It fails on a bad
integrity check, an empty archive, or a database or table the live side no
longer has. Counts that differ are printed, not failed: rows written since
03:00 UTC are expected. Record the output in the runbook entry for #775.

To restore by hand, `borg extract "::<archive>" sqlite` in an empty dir gives
`sqlite/hermes.sqlite3` and the other snapshots; the L2 Parquet sits under
`var/lib/hermes/l2`.

**A backup you have never restored is not a backup.**

## 8. Prune from a trusted machine

The VPS never prunes. From a machine you trust (not hermes-01), with a second
key that has **no** `command=` restriction in the box's `authorized_keys`:

```bash
export BORG_REPO=ssh://u656997@u656997.your-storagebox.de:23/./hermes-backup
export BORG_PASSPHRASE=...   # from the password manager
borg list                    # first: every archive you expect is still there
borg prune --stats --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --glob-archives 'hermes-*'
borg compact
```

Check `borg list` before pruning. A delete sent through the append-only key
only hides an archive, and this full-access write is what makes it final; if
an archive is missing that should not be, stop and recover it from the
transaction log first (borg's "append-only mode" documentation). Monthly is
enough at this repo's size.

## 9. Check the key is append-only

Once, before enabling the timer, prove the box forces `borg serve` on the
backup key instead of running what the client asks:

```bash
# On hermes-01, as root:
sudo -u hermes ssh -p 23 -i /var/lib/hermes/.ssh/hermes-backup_ed25519 \
    -o IdentitiesOnly=yes u656997@u656997.your-storagebox.de ls < /dev/null
```

No file listing means the forced command won, and with it `--append-only`.
If it lists `hermes-backup`, the box ignored `command=`: stop, and keep the
timer off until the key is restricted another way.

## 10. Disk-usage monitoring

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

## 11. Cost baseline

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
