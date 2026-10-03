- **VPS bootstrap re-run** (`scripts/deploy/vps_bootstrap.sh`): step 3 skips the
  mount-point `install` when `/mnt/storage-box` is already mounted, so re-running
  the bootstrap no longer fails on the live sshfs mount. Closes #334.
