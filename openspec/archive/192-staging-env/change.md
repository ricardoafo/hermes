# Change: 192-staging-env

**SDD change**: `192-staging-env`
**Date**: 2026-10-04
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#192)

Mirror of engram topic key `sdd/192-staging-env/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Adds a `staging` tier that runs on hermes-01 next to prod, fully isolated: its own system user, systemd unit, code
root, state directory, ledger and env file. The release scripts gain a staging target that can only ever touch staging
paths. Code and docs only: nothing runs on the VPS, no workflow changes, and the staging unit ships disabled. Main risk:
a script bug that lets a staging deploy touch prod paths; D-3 makes that structurally impossible and tests prove it.

## Scope

### In

- `HERMES_ENV` accepts `staging` (`EnvName`); staging logs JSON like prod; `check_startup` treats staging like prod
  for notifier presence; `require_env` callers that allow mainnet in prod also allow staging (none exist today
  besides docs; keep the gate rule: shadow=false still needs `HERMES_ALLOW_MAINNET`).
- `deploy/systemd/hermes-staging.service`: `User=hermes-staging`, `StateDirectory=hermes-staging`,
  `WorkingDirectory=/var/lib/hermes-staging`, `EnvironmentFile=/etc/hermes/env.staging`,
  `ExecStart=/opt/hermes-staging/current/.venv/bin/hermes serve --config-dir /opt/hermes-staging/current/config --db
  /var/lib/hermes-staging/hermes.sqlite3`, `SyslogIdentifier=hermes-staging`, same hardening as prod, no
  `OnFailure=`/`ExecStopPost=` alerts (staging failures must not page). `[Install]` present but the unit is not
  enabled by anything in this change.
- `scripts/deploy/install_release.sh`, `rollback.sh`, `health_gate.sh`: a target selected by `HERMES_TARGET`
  (`prod` default, `staging`), mapping every path, the unit, the run-as user and the env file (D-3).
- `docs/guides/staging-on-hermes-01.md`: runbook with a Mermaid diagram, including every manual step for the maintainer.

### Out

- A CD workflow for staging (CI writer's area; manual deploy documented instead).
- `vps-bootstrap` creating the staging user, directories and env file (documented as manual steps).
- Notification prefixes or separate bots (the staging env file simply points at its own Slack channel / Telegram
  chat, or none).
- Separate staging backups.

## Decisions

### D-1: Prod stays exactly where it is

- **Decision**: No prod rename (`hermes.service`, `/opt/hermes`, `/var/lib/hermes`, `/etc/hermes/env.production`
  unchanged). Staging lives under `hermes-staging` names.
- **Alternatives**: Rename prod to `hermes-prod.*` as the issue sketched (a risky migration of a live unit for no gain).
- **Cost of reversal**: LOW.

### D-2: Staging is a full copy of the layout, not a shared one

- **Decision**: Own user `hermes-staging` (cannot read prod's 0750 state dir), own `/opt/hermes-staging/{versions,
  current,previous,bin}`, own `/var/lib/hermes-staging` (ledger, backups), own `/etc/hermes/env.staging` (600 root).
- **Rationale**: Isolation by the OS, not by convention.

### D-3: One target variable maps every path; staging paths can never equal prod paths

- **Decision**: The scripts compute ROOT, UNIT, DB, BACKUP_DIR, ENV_FILE and RUN_AS from `HERMES_TARGET`. Explicit
  test seams (`HERMES_ROOT`, `HERMES_DB`, ...) still override for tests. For `staging`, preflight refuses to run if
  any computed path or the unit equals its prod value, or if the env file sets `HERMES_ENV` to anything but
  `staging`. For `prod`, behaviour is byte-for-byte what it is today. An unknown target is refused.
- **Rationale**: A staging deploy must not be able to touch prod even with a typo.

## Requirements

### R-1: Tier

- **A-1.1**: Config with `HERMES_ENV=staging` loads; logging renders JSON.

### R-2: Isolation

- **A-2.1**: `HERMES_TARGET=staging install_release.sh` writes only under the staging root and backup dir, restarts
  only `hermes-staging.service`, and runs the snapshot as `hermes-staging`.
- **A-2.2**: Preflight refuses a staging run whose env file says `HERMES_ENV="prod"`, and an unknown target.
- **A-2.3**: Default target keeps every existing deploy test green unchanged.

### R-3: Unit

- **A-3.1**: `hermes-staging.service` passes the unit-file tests and names no prod path.

## Design

```text
src/hermes/infrastructure/config/root.py        EnvName += "staging"
src/hermes/infrastructure/logging.py            JSON for staging too
src/hermes/entrypoints/serve.py                 notifier check: prod and staging
deploy/systemd/hermes-staging.service           new (not enabled)
scripts/deploy/{install_release,rollback,health_gate}.sh   HERMES_TARGET
docs/guides/staging-on-hermes-01.md             runbook + Mermaid
```

## Test plan

1. Every acceptance criterion above passes (deploy tests run with `HERMES_TEST_BASH` set on Windows).
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

## Risks

- **Bootstrap copies the new unit file** into `/etc/systemd/system` on its next run. It is inert: nothing enables
  it and its user does not exist until the maintainer creates it.
- **Shared box resources** (CPU, Binance rate limits from the same IP): documented in the runbook.

## Dependencies on other changes

- None open.
