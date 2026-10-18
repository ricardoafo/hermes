# Change: 970-endpoint-tiers

**SDD change**: `970-endpoint-tiers`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/970-endpoint-tiers/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Part 2 of issue #970 (ADR-024 §4 and decision 2). The config loader
learns the `qa` tier and checks every endpoint against the tier it runs
in: dev, sandbox and qa may point at a loopback fake; staging and prod
may not; prod uses only the real vendors; qa and staging hold no trading
key. The market-data, Frankfurter and Kraken bases accept `http` on
loopback so the fake market service (#971) is reachable by config alone.
The main risk is refusing a tier that works today: the shipped config
and every current tier's env still load, and each refusal names the
field so a refused start says what to fix.

## Scope

### In

- `HermesConfig.env` gains `qa`; `LOOPBACK_TIERS = {dev, sandbox, qa}`.
- `HermesConfig._endpoints_fit_the_tier`: market data, Frankfurter,
  Kraken, healthchecks ping URL and Sentry DSN refuse loopback outside
  `LOOPBACK_TIERS`; Frankfurter and Kraken refuse a non-default base in
  prod.
- `env_tier_problems` in the loader, for env-only settings: trading keys
  refused in qa and staging; Telegram and Slack bases refuse loopback
  outside `LOOPBACK_TIERS` and a non-default base in prod.
- `FrankfurterConfig` and `KrakenPublicConfig` accept `http` on loopback;
  `MarketDataConfig` accepts a loopback fake beside the public host.
- `is_loopback` returns `False` for a URL that does not parse.

### Out

- How staging recognises prod's own notifier, ping and Sentry values:
  a Code Owner decision (fingerprints recommended), a later part of #970.
- The `install_release.sh` preflight running the same checks before a
  restart: a later part of #970.
- The read-only Binance key (`HERMES_BINANCE_READ__*`): its place
  waits on the Code Owner's call on #29, so it is not refused here.

## Decisions

### D-1: YAML-backed endpoints in the root model, env-only ones in the loader

- **Decision**: `HermesConfig` checks the fields it holds; the loader
  checks the env-only notifier bases and trading keys after the model
  validates.
- **Alternatives**: move the notifier bases into YAML (they live beside
  their credentials by part 1's D-1); check everything in the loader
  (a `HermesConfig` built in a test would skip the YAML rules).
- **Rationale**: each rule runs where its value lives, and every load
  path goes through both.
- **Cost of reversal**: LOW.

### D-2: Errors name the field, never the value

- **Decision**: messages read `<field> points at a loopback host ...`.
- **Rationale**: the ping URL, the DSN and the keys carry credentials
  (#794).
- **Cost of reversal**: LOW.

### D-3: Dev and sandbox keep their testnet keys

- **Decision**: only qa and staging refuse a trading key.
- **Rationale**: ADR-024 §4: qa runs on fakes and staging on public
  data; dev and sandbox trade on testnet. Prod's gate stays
  `HERMES_ALLOW_MAINNET`.
- **Cost of reversal**: LOW.

## Requirements

### R-1: qa is a tier and may use loopback fakes

- **A-1.1**: `uv run pytest tests/unit/infrastructure/config/test_endpoint_tiers.py -k "qa_is_a_tier or lower_tiers"` exits 0.

### R-2: staging and prod refuse loopback, prod refuses non-default vendors

- **A-2.1**: `uv run pytest tests/unit/infrastructure/config/test_endpoint_tiers.py -k "refuse"` exits 0.

### R-3: qa and staging refuse trading keys; refusals never echo values

- **A-3.1**: `uv run pytest tests/unit/infrastructure/config/test_endpoint_tiers.py -k "trading_key"` exits 0.

## Design

- `root.py`: `EnvName` adds `"qa"`; `LOOPBACK_TIERS`; model validator
  `_endpoints_fit_the_tier` raising `ValueError("HERMES_ENV=<tier>: ...")`.
- `loader.py`: `env_tier_problems(tier, env) -> list[str]`, called after
  `HermesConfig(...)`; a non-empty list raises `ConfigError`.
- `models.py`: `DEFAULT_FRANKFURTER_BASE_URL`,
  `DEFAULT_KRAKEN_PUBLIC_BASE_URL`, `_https_or_loopback`.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green.

Test tiers touched: unit, property

## Risks

- **A hand-written staging env file holds a testnet key today**: staging
  stops at start and names the variable. Intended by ADR-024 §4; the
  staging guide already says staging holds no key.
- **A test fixture used the retired Frankfurter host**: prod now refuses
  it, so the fixture moves to the current host the shipped config uses.

## Dependencies on other changes

- Part 1 of #970 (PR #1135, merged).
- Unblocks #971's later parts and #972 part 2 pointing at the fake market.
