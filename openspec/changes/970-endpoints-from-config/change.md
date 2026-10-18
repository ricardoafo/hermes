# Change: 970-endpoints-from-config

**SDD change**: `970-endpoints-from-config`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/970-endpoints-from-config/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Part 1 of issue #970 (ADR-024 decision 2). Telegram, Slack and the
`hermes bot` client stop hard-coding their API base and read it from an
env var next to their credentials, so an installed release reaches a
loopback fake purely by config. One shared module validates the value:
`https` anywhere, `http` only on loopback, no user info, query or
fragment. The main risk is a mistyped value silently muting a channel; a
bad value therefore raises its own error, which no caller swallows.

## Scope

### In

- `src/hermes/adapters/endpoint.py`: defaults, env names, validation,
  `is_loopback` (reused by part 2).
- `TelegramConfig.api_base`, `SlackConfig.api_base`, read in `from_env`;
  `create` builds its client on it.
- `TelegramBotClient.api_base`; `hermes bot` reads it and exits 3 on a
  bad value.
- The three systemd alert scripts read the same variable names in place
  of their old test-only names.
- `.env.local.example` documents both variables, commented out.

### Out

- Per-tier refusals (staging refuses prod's values and trading keys,
  prod refuses loopback), the `qa` tier and the `install_release.sh`
  preflight: part 2 of #970. How staging recognises prod's values is a
  Code Owner decision still open.
- Healthchecks ping URL and Sentry DSN: already read from env; their
  tier rules land with part 2.

## Decisions

### D-1: The API base lives in env, beside the credentials

- **Decision**: `HERMES_TELEGRAM__API_BASE` and `HERMES_SLACK__API_BASE`,
  read by `from_env`.
- **Alternatives**: a YAML section in `config/` (the notifiers have none;
  credentials are env-only per ADR-003 §04, and splitting one channel's
  settings across two sources invites a half-configured channel).
- **Rationale**: ADR-024's naming rule: one name in every GitHub
  environment, only values differ. The shell alert scripts already read
  the same env file.
- **Cost of reversal**: LOW.

### D-2: `http` only on loopback

- **Decision**: refuse plain `http` unless the host is `localhost`,
  `127.0.0.0/8` or `::1`.
- **Alternatives**: accept any URL (a typo could send a bot token in
  clear); require `https` always (a loopback fake would need a cert).
- **Rationale**: a fake runs on the same machine; anything else must be
  TLS.
- **Cost of reversal**: LOW.

### D-3: A bad value is its own error

- **Decision**: `EndpointConfigError(ValueError)`, not the adapter's
  `*NotifierError`.
- **Alternatives**: reuse the notifier error (serve, reconcile and
  archive-check treat it as "no credentials" and silently disable the
  channel).
- **Rationale**: a mistyped endpoint must stop startup, not mute alerts.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Each endpoint reaches a loopback fake by config alone

- **A-1.1**: `uv run pytest tests/integration/adapters/test_notifier_endpoints.py`
  passes: Telegram, Slack and `hermes bot --once` each hit a real
  127.0.0.1 server with no client injected.

### R-2: Defaults are unchanged

- **A-2.1**: unset or blank, the clients target `https://api.telegram.org`
  and `https://slack.com` (unit tests in each adapter's test file).

### R-3: Unsafe values are refused, naming the variable

- **A-3.1**: `uv run pytest tests/unit/adapters/test_endpoint.py` passes,
  including a property that no accepted `http` base is off-machine.
- **A-3.2**: a bad base raises `EndpointConfigError` from `from_env`;
  `hermes bot` exits 3; serve's `_telegram_config` raises instead of
  returning `None`.

## Design

`hermes.adapters.endpoint.api_base_from_env(env, name, default) -> str`
returns `default` for an unset or blank value, else
`parse_api_base(value, name=name)`, which checks, in order: parseable
(port numeric), no user info, no query or fragment (both refused without
echoing the value), a host, port not 0, `http` only on loopback, scheme
`http` or `https`; and drops one trailing `/`.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

## Risks

- An operator who set the old test-only shell names
  (`HERMES_NOTIFY_API_BASE`, `HERMES_NOTIFY_SLACK_API_BASE`) loses the
  override. They were documented as never set outside tests.
