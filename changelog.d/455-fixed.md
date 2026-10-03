- **Deploys snapshot the ledger first** (`scripts/deploy/install_release.sh`):
  before a release starts, CD takes an SQLite online backup of
  `/var/lib/hermes/hermes.sqlite3` into `backups/pre-<sha12>.sqlite3`
  (newest 5 kept), and a release that fails the health gate is rolled
  back onto that snapshot, so a schema migration never meets the old
  code (#455).
