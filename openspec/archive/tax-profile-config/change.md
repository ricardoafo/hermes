# Change: tax-profile-config

**SDD change**: `tax-profile-config`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/tax-profile-config/change`.

## Summary

Adds the data layer for ADR-016 tax estimates: a per-year savings scale in
`config/tax_scales.yaml` and a local taxpayer profile outside the repo. Both are
pydantic models with fail-closed loaders. No tax is computed here; #395 consumes
these models. The main risk is leaking profile values, so the models hide them
from repr and the loader never puts them in errors or logs.

## Scope

### In

- `config/tax_scales.yaml` (2025, 2026 savings bands, default personal minimum 5,550).
- `TaxScalesConfig` + `load_tax_scales`; missing year raises "scale missing for <year>".
- `TaxpayerProfile` + `load_taxpayer_profile` (`~/.config/hermes/taxpayer.yaml`,
  override `HERMES_TAXPAYER_PROFILE`); no file means zeros and a notice.
- `config/taxpayer.example.yaml` (zeros) and `.gitignore` guard for real profiles.

### Out

- Tax computation, netting, carry-forward rules (#395).
- Region, `general_deductions_eur`, `show_total_irpf`, general-base scales (#396).

## Decisions

### D-1: Profile never enters repr, logs or error text

- **Decision**: all profile value fields use `repr=False`; loader errors report
  field location and pydantic message only, never `input_value`.
- **Alternatives**: redacting wrapper type (heavier, no gain).
- **Rationale**: ADR-016 item 12.
- **Cost of reversal**: LOW.

### D-2: Scales and profile are separate from `HermesConfig`

- **Decision**: own loaders, not part of `load_config`.
- **Rationale**: the profile is optional and lives outside the repo; the scales
  are only needed by the tax module and must fail on the requested year, not at start.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Savings scale per year, fail closed

- **A-1.1**: `pytest tests/unit/infrastructure/config/test_tax_config.py` passes.

### R-2: Profile local, optional, private

- **A-2.1**: same test file covers missing file, bad values, and no leak in repr/logs/errors.

## Design

Module `hermes.infrastructure.config.tax` with frozen `extra="forbid"` models.
Profile YAML: `years.<year>.{general_income_eur, personal_minimum_eur,
other_savings_income_eur, other_capital_gains_eur, losses_carried_forward[]}`.
Issue 396 adds top-level `region`, `show_total_irpf` and per-year `general_deductions_eur`.

## Test plan

Unit tests plus ruff, mypy, lint-imports.

## Risks

- **Strict extra=forbid** rejects #396 keys until #396 adds them; intended.

## Dependencies on other changes

- ADR-016 (PR pending). Blocks #395.
