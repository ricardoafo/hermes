# Change: 970-staging-env-check

**SDD change**: `970-staging-env-check`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/970-staging-env-check/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Part 3 of issue #970 (ADR-024 §4). A staging deploy checks
`/etc/hermes/env.staging` against the new release's config before it
restarts anything. It uses the same tier rules and startup checks the
release will apply when it starts. Today a staging env file with a
trading key or a loopback endpoint is only caught when the new release
fails to start. The health gate then rolls back after the unit has
already restarted. With this change, the deploy stops first and names
each setting at fault. The main risk is a false refusal, which would
block a staging deploy. The check is the release's own loader, so it
refuses exactly what the release would refuse at start.

## Scope

### In

- `hermes config check --env-file PATH|-`: checks that file alone in
  place of the process environment, as systemd gives it to a unit with
  `EnvironmentFile=` and no `Environment=` line. `-` reads stdin.
  `${VAR}` is not expanded. A missing file exits 3.
- Tier problems become `tier` findings, one per setting, named and
  never valued. `TierError`, a `ConfigError`, carries the list.
- When a config endpoint and an env-only setting both break the tier,
  both are reported in one error. Before, the config endpoint's problem
  hid the other.
- `install_release.sh`, staging target only: after the install and
  before the bar wait, snapshot, migration and restart, it runs
  `hermes config check --config-dir <release>/config --env-file -` as
  `hermes-staging`, with the env file on stdin. Exit 1 prints the
  findings and stops; any other exit stops as a failed check.

### Out

- Prod's env file. Prod's unit adds `Environment=HERMES_ENV=prod` on top
  of its file, and changing prod deploys is outside this part.
- How staging recognises prod's own notifier, ping and Sentry values.
  This waits on a Code Owner decision.

## Decisions

### D-1: The installer runs the release's own check, not a shell copy of the rules

- **Decision**: call the new release's `hermes config check`.
- **Alternatives**: grep the env file for keys and loopback hosts in
  bash, like the mainnet check does.
- **Rationale**: a second copy of the rules in shell would drift from
  the loader. The release's own check refuses exactly what the release
  would refuse at start, startup checks included.
- **Cost of reversal**: LOW.

### D-2: The env file goes in on stdin, read by the service user

- **Decision**: `runuser -u hermes-staging -- hermes config check --env-file - < env.staging`.
- **Rationale**: the file is root 600. The service user gets its
  contents from systemd anyway, so passing them on stdin exposes
  nothing new. Running as root would run the release's code as root.
- **Cost of reversal**: LOW.

### D-3: The check runs after the install and before anything live changes

- **Decision**: the check runs after `install_version`, because it
  needs the release's binary. It runs before the ledger snapshot,
  migration, link flip and restart.
- **Rationale**: a refused deploy leaves the release installed but not
  current. A later deploy of the same sha skips the install and checks
  again.
- **Cost of reversal**: LOW.

## Requirements

### R-1: tier problems are named findings

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_config_check.py -k "Tier"` exits 0.
- **A-1.2**: `uv run pytest tests/unit/infrastructure/config/test_endpoint_tiers.py -k "tier_error"` exits 0.

### R-2: `--env-file` checks one file alone

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_config_check.py -k "EnvFile"` exits 0.

### R-3: a staging deploy stops on a finding before any restart

- **A-3.1**: `uv run pytest tests/integration/deploy/test_release_scripts.py -k "config_check"` exits 0.

## Design

- `errors.py`: `TierError(ConfigError)` with `problems: tuple[str, ...]`.
- `root.py`: `EndpointTierError(ValueError)` carrying `env` and
  `problems`, raised by `_endpoints_fit_the_tier`.
- `loader.py`: when the only validation error is an `EndpointTierError`,
  its problems and `env_tier_problems` are raised together as
  `TierError`. Env-only problems alone also raise `TierError`.
- `config_check.py`: `--env-file` via `dotenv_values(stream=...,
  interpolate=False)`. `TierError` problems become `Finding("tier",
  <setting>, <detail>)`.
- `install_release.sh`: `check_staging_env`, seam
  `HERMES_CONFIG_CHECK_BIN`.

## Test plan

1. Every acceptance criterion above passes.
2. The integration tests run the real CLI and the shipped config through
   the installer for a trading key, a loopback market-data base and a
   loopback Telegram base. They also run a clean file.
3. `bash scripts/dev/pr-preflight.sh` → all checks green.

Test tiers touched: unit, integration

## Risks

- **python-dotenv and systemd parse an edge-case line differently**:
  the check could pass or refuse a line systemd reads another way. The
  shipped env files use plain `KEY="value"` lines, which both read the
  same.
- **A staging env file holds a key today**: the next staging deploy
  stops and names it. ADR-024 §4 and the environments guide already
  forbid that key.

## Dependencies on other changes

- Part 2 of #970 (PR #1212, merged).
