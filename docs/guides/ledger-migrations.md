# Ledger migrations — applying, snapshotting and rolling back

**Type**: Guide (living document — PR any change)
**Owners**: Code Owner (sole operator)
**Applies to**: the SQLite ledger (`/var/lib/hermes/hermes.sqlite3` on `hermes-01`, `./hermes.sqlite3` locally).
**Description**: How ledger schema migrations run, which ones need `hermes migrate`, and how to roll one back from its snapshot.

## The rule

Migrations are numbered SQL files in `src/hermes/adapters/ledger/migrations/`, immutable once merged and
forward-only ([ADR-006](../architecture/adr-006-versioning-strategy.md) §2). There are no down-migrations.
Rolling back means restoring the snapshot taken just before the migration, then fixing forward with a new
migration.

Each migration runs in one transaction: if any statement fails, none of it stays and it is not recorded.

## Two kinds of migration

| Kind | First line of the file | When it runs |
| --- | --- | --- |
| Additive (new table, new column, new index) | anything | Automatically when the ledger opens (`hermes serve` start) |
| Destructive (drop, rename, table rebuild) | `-- hermes: destructive` | Only through `hermes migrate`, which snapshots first |

A brand-new ledger (no migration applied yet) has no data to protect, so it gets every migration at
first open, destructive ones included. On an existing ledger, if a destructive migration is pending,
the ledger refuses to open and names the file. `hermes serve` stops at
start with that message until the migration has been run by hand. Additive migrations before it in the sequence
still apply.

A migration author marks a file destructive whenever restoring the snapshot would be the only way back: anything
that drops or rewrites existing rows, columns, tables, defaults or constraints.

## How a deploy handles it

CD runs this for you. After the pre-deploy ledger snapshot, `scripts/deploy/install_release.sh` asks the new
release for `hermes migrate --status`. If a destructive migration is pending, it stops `hermes.service`, runs
`hermes migrate` with the new release's binary, then restarts the unit and runs the health gate. If the migration
fails, it rolls back in its transaction, the old release starts again and the deploy stops. If the health gate
fails afterwards, the rollback restores the pre-deploy snapshot, which is from before the migration.

The manual steps below are for running a migration outside a deploy.

## Trying a migration first

`hermes migrate --dry-run` applies every pending migration, destructive ones included, to a temporary copy of the
ledger and prints what ran, whether the copy passes `PRAGMA integrity_check`, and the row count of every table
before and after. It only reads the real ledger, so `hermes.service` can keep running:

```bash
sudo -u hermes /opt/hermes/current/.venv/bin/hermes migrate --db /var/lib/hermes/hermes.sqlite3 --dry-run
```

Exit 0 means everything applied and the copy is intact. Exit 1 means a migration failed on the copy, and the line
starting `dry run: FAILED` names it.

## Running a destructive migration on hermes-01

1. Check what is pending:

   ```bash
   sudo -u hermes /opt/hermes/current/.venv/bin/hermes migrate --db /var/lib/hermes/hermes.sqlite3 --status
   ```

2. Stop the engine so the ledger has no other writer:

   ```bash
   sudo systemctl stop hermes.service
   ```

3. Snapshot and migrate:

   ```bash
   sudo -u hermes /opt/hermes/current/.venv/bin/hermes migrate --db /var/lib/hermes/hermes.sqlite3
   ```

   The command writes `/var/lib/hermes/migration-snapshots/hermes-pre-<first migration>-<UTC time>.sqlite3`,
   checks it with `PRAGMA integrity_check`, prints its path, then applies every pending migration. Exit code 1
   means a migration failed and was rolled back; the ledger is as it was and the snapshot is kept.

4. Start the engine and watch it come up:

   ```bash
   sudo systemctl start hermes.service
   journalctl -u hermes.service -n 50 --no-pager
   ```

## Rolling back

Use the snapshot path printed in step 3.

```bash
sudo systemctl stop hermes.service
sudo -u hermes /opt/hermes/current/.venv/bin/hermes migrate --db /var/lib/hermes/hermes.sqlite3 \
  --restore /var/lib/hermes/migration-snapshots/<snapshot>.sqlite3
```

The restore checks the snapshot's integrity first and overwrites the ledger through SQLite's backup API, so the
file keeps its owner and permissions. Anything written after the snapshot is lost, which is why the engine must
stay stopped between migrating and deciding to roll back.

After a restore the migration is pending again. Deploy the release that drops or fixes it before starting
`hermes.service`; otherwise the ledger refuses to open again.

## Keeping snapshots

Snapshots are not rotated automatically. Keep the latest one until the migrated release has run cleanly for a
few days, then delete older ones. The nightly backup (`hermes-backup.timer`) still covers the ledger itself.
