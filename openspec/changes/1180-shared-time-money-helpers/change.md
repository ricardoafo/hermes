# Change: 1180-shared-time-money-helpers

**SDD change**: `1180-shared-time-money-helpers`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1180-shared-time-money-helpers/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The Europe/Madrid zone was built in 11 modules, the cent in 9, the UTC
check in 3 and an exact Decimal context in 8 (at 60 and 100 digits, through
two mechanisms). The copies had drifted: two of the three UTC checks let a
Madrid-aware time through, and two console helpers and one reconcile helper
rounded cents half to even where every other screen rounds half up. Three
modules in `domain/value_objects` now hold one copy each, and a source scan
fails when a copy comes back. The main risk is a value that used to round
or pass one way and now goes the other; the Risks section names each one.

## Scope

### In

- `domain/value_objects/time.py`: `MADRID`, `require_utc`.
- `domain/value_objects/cents.py`: `CENT`, `to_cents`, `cents_text`.
- `domain/value_objects/precision.py`: `EXACT_PRECISION`, `EXACT_CONTEXT`,
  `exact()`; `domain/portfolio/precision.py` and `domain/tax/precision.py`
  deleted.
- `filing/pack.py` imports `csv_text` and `madrid_day` from
  `filing/render.py`; `tax/screen.py` imports `rate_percent` from
  `tax/estimate.py`; the messages `fmt_qty` becomes `fmt_asset_qty` so it no
  longer shares a name with the console `fmt_qty`.
- The source-scan guard in `tests/unit/domain/value_objects/test_home_modules.py`.

### Out

- The Spanish-format helpers in `renta_sheet.py` (`_eur`, `_day`), which
  write Spanish text and differ on purpose.
- The console's four-place EUR rounding (`_EUR_PRECISION` in `activity.py`
  and `serialize.py`): a different grid, not a cent.
- The repo-wide duplicate-block ratchet (issue #1201).

## Decisions

### D-1: The homes are domain value objects

- **Decision**: all three modules live in `hermes.domain.value_objects`.
- **Alternatives**: `domain/tax` (the `tax-is-measurement-only` contract
  bars `domain.strategy` from importing it, and `stop_mark` needs the UTC
  check); an application module (the domain needs Madrid and the context).
- **Rationale**: every layer may import the domain's value objects.
- **Cost of reversal**: LOW.

### D-2: One UTC check, the strict one

- **Decision**: `require_utc` refuses a naive time and any zone whose fixed
  offset is not zero, as the bar-schedule copy did.
- **Alternatives**: keep an aware-only check beside it (two rules again).
- **Rationale**: code-conventions §9 says UTC inside; a Madrid-aware time
  in a stop mark or the tax guard window is a caller bug.
- **Cost of reversal**: LOW.

### D-3: One width, 100 digits

- **Decision**: the portfolio context moves from 60 to 100 digits.
- **Alternatives**: keep two widths in one module (the audit finding stays).
- **Rationale**: a wider context only removes rounding; products of
  realistic fills were already exact at 60, so no figure changes.
- **Cost of reversal**: LOW.

### D-4: Cents round half up everywhere

- **Decision**: `to_cents` rounds half up; the console quote and seed
  helpers and `hermes reconcile`'s EUR column, which rounded half to even,
  use it.
- **Rationale**: one amount reads the same on every screen and file.
- **Cost of reversal**: LOW.

## Requirements

### R-1: One copy

No module under `src/hermes` other than the home builds
`ZoneInfo("Europe/Madrid")`, a `Decimal` whose text is a cent, defines a
`require_utc`, assigns `.prec` or builds `Context(prec=...)`. The two
exchange adapters' 1% stop-limit buffers are the named exceptions.

### R-2: The UTC check refuses every non-UTC time

Naive, Madrid, a fixed +01:00 and London (offset zero in winter) are refused
with the field's name; `UTC`, `timezone.utc`, `ZoneInfo("UTC")` and
`ZoneInfo("Etc/UTC")` pass.

### R-3: Half a cent rounds away from zero

`to_cents` gives two decimals within half a cent of the amount, and a tie
rounds away from zero.

## Design

Each copy's definition is deleted and the module imports the home. Private
call sites in the filing modules keep their short names through import
aliases (`cents_text as _eur`), so those diffs stay at the import lines.
`console/render.py`, `status/__init__.py` and `domain/tax/tax_calendar.py`
re-export `MADRID`, so their importers do not change.

## Test plan

- `test_home_modules.py`: the R-1 scan over `src/hermes`, a parametrised
  check that the scan sees every spelling (`zoneinfo.ZoneInfo`,
  `Decimal("1E-2")`, `Context(prec=...)`), and a check that the allow-list
  only names files that still hold the buffer.
- `test_time.py`: R-2 and the Madrid winter and summer offsets.
- `test_cents.py`: R-3 as examples and a Hypothesis property.
- `test_precision.py` (moved from `domain/tax`): the 100-digit context keeps
  a 70-digit product the old 60-digit context rounded.

## Risks

- A caller that passed a Madrid-aware time to a stop mark or the tax guard
  now gets a `ValueError`. The full suite found none.
- The error text is one wording for all three callers: "now must be
  UTC-aware (a timezone-aware UTC datetime), got ...". It keeps "must be
  UTC-aware" (the bar-schedule tests) and "timezone-aware" (the tax-guard
  tests), so no existing assertion changes; the tax guard loses its
  "tax_guard.compute_window:" prefix.
- A console USDT amount or a `hermes reconcile` EUR value that ends in
  exactly half a cent now rounds up instead of to even.
