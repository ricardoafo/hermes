- **CD can deploy hermes for the first time** (`.github/workflows/cd.yml`,
  `scripts/deploy/`): a `bundle` job ships the CI-built wheel, the
  hash-pinned requirements, `config/`, `deploy/*.sh` and the deploy scripts as
  one tarball.
  The VPS installs it into `/opt/hermes/versions/<sha12>/`, flips `current`
  atomically, restarts `hermes.service` and gates the result (unit active
  30 s, same PID, `serve.run.started` in the journal); a failed gate rolls
  back to `previous`. `vps_bootstrap.sh` is now the only bootstrap script
  (user, dirs, uv, pinned and verified Python 3.14.7, units, env file written
  once, optional deploy sudoers rule), each step guarded and tested, safe to
  re-run over a live storage-box mount, and `deploy/bootstrap-vps.sh` is
  gone. The smoke guard and the `confirm` aggregator now fail when they
  should. Closes #179.
