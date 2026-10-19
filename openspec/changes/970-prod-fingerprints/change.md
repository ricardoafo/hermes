# Change: 970-prod-fingerprints

**SDD change**: `970-prod-fingerprints`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/970-prod-fingerprints/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Staging shares hermes-01 with prod, and `/etc/hermes/env.staging` is written
by hand. Nothing checked the values in it, so prod's Telegram token copied
into it would send lab alerts to prod's chat, lab pings would count as
prod's heartbeat and lab errors would land in prod's Sentry. A committed
`config/prod_fingerprints.yaml` now holds the SHA-256 of each prod value,
and every tier but prod refuses a matching value at load, naming the
variable. The install preflight gets the refusal for free through
`hermes config check`. This is part 2 of issue #970 (ADR-024 decision 2).

## Scope

### In

- `infrastructure/config/fingerprints.py`: the file model, `fingerprint`,
  the loader, the refusal and the renderer.
- `load_config` adds the refusal to its tier problems.
- `hermes config fingerprint --env-file PATH|-` prints the file for prod's
  env file.
- `config/prod_fingerprints.yaml`, shipped empty.
- The operator steps in `docs/guides/environments.md`.

### Out

- Filling the file: the operator runs the command on prod's machine.
- Exchange keys: staging already refuses any trading key by name.
- The read-only Binance key (#29), held until the cutover (#1085).

## Decisions

### D-1: Fingerprints, not a direct comparison

- **Decision**: compare against committed SHA-256 digests (Code Owner, 18 Oct, call 1 A).
- **Alternatives**: trust the GitHub environment split (nothing stops a
  hand-copied value); read prod's env file from staging (ADR-024 §4 keeps
  the tiers' secrets apart, and it breaks at the cutover).
- **Rationale**: catches the copy on the shared box and keeps working once
  the tiers move apart; neither tier reads the other's secrets.
- **Cost of reversal**: LOW.

### D-2: Match against every digest, not only the variable's own

- **Decision**: a watched value is refused if its digest is anywhere in the file.
- **Alternatives**: per-variable matching (misses prod's daily ping URL
  pasted into staging's main ping URL).
- **Cost of reversal**: LOW.

### D-3: No env override, and a missing file lists nothing

- **Decision**: the file is read directly, never through `HERMES_*`
  overrides; a missing file means no digests.
- **Rationale**: an override would let the env file under check switch its
  own check off. A missing file keeps hand-built test config dirs loading;
  the release always ships the file.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Refuse outside prod

Every tier but prod refuses a watched variable whose stripped value hashes
to a listed digest, as a `TierError` naming the variable.

### R-2: Prod is unaffected

Prod loads with its own values.

### R-3: Never echo a value

Refusals, malformed-file errors and the command's output carry no value.

### R-4: An empty render never replaces the file

`hermes config fingerprint` exits 3 when the env file sets no watched value.

## Design

`fingerprints.py` holds `WATCHED_VARS` (the two bot tokens, three ping
URLs and the DSN), `ProdFingerprints` (frozen, `extra="forbid"`, keys
limited to the watched names, digests 64 lowercase hex), `fingerprint`
(SHA-256 of the stripped UTF-8 value), `load_prod_fingerprints(config_dir)`,
`prod_value_problems(tier, env, fingerprints)` and
`render_prod_fingerprints(env)`. `load_config` reads the file once and
appends `prod_value_problems` to the env-only tier problems on both paths
(clean root, and root refused for an endpoint). `config_check.read_env_file`
becomes public so the new command parses env files the way the check does.

## Test plan

- Unit: `tests/unit/infrastructure/config/test_fingerprints.py` (every
  non-prod tier refuses by name, prod loads, cross-variable match, no echo,
  no override, malformed files, shipped file parses, round-trip property).
- Unit: `tests/unit/entrypoints/test_config_fingerprint.py` (output, stdin,
  no interpolation, empty refusal, usage errors) and the dispatcher test.
- Unit: `test_config_check.py` shows the preflight's `tier` finding.

## Risks

- A rotated prod value is no longer refused until the operator re-runs the
  command; the guide says to run it after each rotation.
- The file stays empty until the operator fills it, so the refusal is
  inert until then.
