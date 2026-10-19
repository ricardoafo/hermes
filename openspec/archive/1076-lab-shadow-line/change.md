# Change: 1076-lab-shadow-line

**SDD change**: `1076-lab-shadow-line`
**Date**: 2026-10-18
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1076-lab-shadow-line/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The staging lab on hermes-01 (ADR-024) must run several shadows at once, each
on its own ledger (ADR-021 §2). Serve already runs one strategy per process on
one ledger, guarded by a per-ledger lock. This change lets one release run any
roster entry with `hermes serve --strategy <name>`, ships a systemd template
`hermes-shadow@.service` that gives each instance its own ledger directory, and
makes serve refuse a ledger that holds another strategy's capital. The main
risk is the shared Binance rate limit across instances, which the crash-loop
guard bounds.

## Scope

### In

- `serve.with_only_enabled()` and `hermes serve --strategy NAME`.
- `SqliteLedger.seeded_strategies()` and the boot refusal of another
  strategy's ledger.
- `deploy/systemd/hermes-shadow@.service`.
- The fake-market harness takes `strategy=`; a side-by-side scenario test.
- The staging guide's section on running several shadows.

### Out

- Missed-bar budget per shadow (ADR-021's 1%): reads the `BarMissed` rows of
  PR #1103, so it follows that merge (issue #1117).
- The champion twin compared daily with prod: prod has no champion until the
  cutover (ADR-024 §7a), so it moves to issue #1117.
- The Lab screen's budget display (issue #1056).
- Per-shadow Telegram chats: every instance reads `env.staging`, the lab chat.

## Decisions

### D-1: One serve process per shadow, chosen by `--strategy`

- **Decision**: each shadow is its own `hermes serve` with `--strategy` and
  `--db`; the flag rewrites which roster entry is enabled before any check.
- **Alternatives**: one serve running several strategies (rewrites the
  composition, the kill state and every ledger read); a config dir per shadow
  (breaks ADR-024 §7a's same-release rule).
- **Rationale**: every gate (certification, exit pricing, parity, day 0) keys
  on the one enabled entry and on `--db`, so all of them work per shadow
  unchanged. Process isolation plus the per-ledger lock already exist.
- **Cost of reversal**: LOW.

### D-2: A ledger seeded only for other strategies is refused at boot

- **Decision**: refuse when the ledger has seeds and none is this strategy's.
- **Alternatives**: refuse any ledger with a second strategy's seed (would stop
  an existing ledger that holds two seeds from booting at all).
- **Rationale**: stops a new shadow from joining another's ledger, the only
  way two shadows could share rows, without touching ledgers that run today.
- **Cost of reversal**: LOW.

### D-3: The template is shipped disabled under the staging user

- **Decision**: user `hermes-staging`, state
  `/var/lib/hermes-staging/shadows/%i`, `env.staging`, no paging; bootstrap
  copies it like every unit and enables nothing.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Shadows cannot read or change each other's ledger or capital

- **A-1.1**: `uv run pytest tests/integration/scenarios/test_shadow_line.py` passes.

### R-2: An instance runs exactly its named strategy

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_serve.py -k WithOnlyEnabled tests/unit/entrypoints/test_cli.py -k strategy` passes.

### R-3: The template is safe to ship

- **A-3.1**: `uv run pytest tests/unit/deploy/test_systemd_units.py -k shadow` passes.

## Design

```text
hermes-shadow@<name>.service
  ExecStart=hermes serve --config-dir /opt/hermes-staging/current/config
                         --strategy <name>
                         --db /var/lib/hermes-staging/shadows/<name>/hermes.sqlite3
commands/serve.py: config = with_only_enabled(config, --strategy) before check_startup
serve._wire_on_ledger: _refuse_another_strategys_ledger() before seed_capital_once()
```

## Test plan

1. Every acceptance criterion above passes.
2. The refusal test fails with the guard removed (checked by hand).
3. `bash scripts/dev/pr-preflight.sh` → all checks green.

Test tiers touched: unit, integration

## Risks

- **Shared rate limit**: each instance polls Binance from the box's one IP.
  Seven instances poll klines once per bar and fetch depth only on a signal;
  the crash-loop guard caps warm-up refetches.
- **A wrong `--db` with `--new-ledger`** creates a fresh ledger; the guide
  creates each ledger once by hand, and the unit never passes `--new-ledger`.

## Dependencies on other changes

- ADR-021 §2 (per-strategy ledger), ADR-024 (hermes-01 is the lab).
- PR #1103 for the missed-bar budget in issue #1117.
