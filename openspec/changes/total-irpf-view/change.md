# Change: total-irpf-view

**SDD change**: `total-irpf-view`
**Date**: 2026-10-04
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#396)

Mirror of engram topic key `sdd/total-irpf-view/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Adds the optional total IRPF view of ADR-016 item 11: the year's general-base tax (state half plus regional half) and
savings tax, with the hermes share and the total. `config/tax_scales.yaml` grows `general.state.<year>` and
`general.regions.<region>.<year>` band tables (state scale and Región de Murcia for 2025 and 2026, each band cited to the
official text). The profile adds `region`, `general_deductions_eur` and `show_total_irpf` (default `false`). The view is a
pure function of the figures #395 already computes; it never changes the hermes tax. A missing scale or region prints
`total IRPF unavailable: <reason>`, never a guess. The main risk is the legal reading of the minimum and of the general
liquidable base, so both are written down here and pinned by worked examples.

## Scope

### In

- `config/tax_scales.yaml`: `general.state.{2025,2026}`, `general.regions.murcia.{2025,2026}` with source comments.
- `infrastructure/config/tax.py`: `GeneralScalesConfig`, `TaxScalesConfig.general` and `general_scales_for(region, year)`
  (raises `ConfigError("no scale for <region> <year>")`); profile fields `region`, `show_total_irpf` (top level) and
  `general_deductions_eur` (per year). All profile values stay out of `repr`, logs and error messages.
- `domain/tax/netting.py`: `TaxpayerYear.general_deductions` and `general_base`.
- `domain/tax/marginal.py`: the personal-minimum remainder uses `general_base` (income minus deductions).
- `domain/tax/total.py` (new): `GeneralScales`, `general_tax`, `TotalIrpf`, `TotalIrpfUnavailable`, `total_irpf`.
- `application/tax/estimate.py`: `estimate_year(..., general_scales=None)`; `TaxYearEstimate.total_irpf`.
- `entrypoints/tax.py`: resolves the view from the profile and config, prints it in text and JSON when on.
- Tests (unit, hypothesis, worked examples); `changelog.d/396-added.md`.

### Out

- Regions other than Murcia (added as needed, one YAML block plus a cited source each).
- A regional personal minimum different from the state one (Murcia has none, see D-3). The view uses one minimum for
  both halves; adding a region with its own minimum needs a per-region minimum field first.
- Quota deductions (state or regional: housing, donations, the art. 81 bis family deductions), the work-income reduction
  of art. 20 and the art. 19.2.f 2,000 EUR expense: the operator folds base reductions into `general_deductions_eur`;
  quota deductions are not modelled, so the view is the *cuota íntegra*, labelled an estimate.
- Withholdings, payments on account and the final *cuota diferencial*.
- Per-year region (a move between regions): the region is one profile value.

## Decisions

All decisions below: DECIDED by implementer, Code Owner reviews in PR.

### D-1: General tax per Ley 35/2006 arts. 56, 63 and 74

- **Decision**: `general_base = max(0, general_income - general_deductions)`; `sheltered = min(personal_minimum,
  general_base)`; for each of the state and regional scales, `tax(general_base) - tax(sheltered)`; `general_tax` is the
  sum of both halves.
- **Legal reading**: art. 63.1 (state) and art. 74.1 (regional): the scale is applied to the whole general liquidable
  base, and the result is reduced by the same scale applied to the part of that base that corresponds to the personal and
  family minimum. Art. 56.2: the minimum forms part of the general base up to its amount; the rest goes to the savings
  base.
- **Alternatives**: subtract the minimum from the base and apply the scale to the rest (gives the same number when every
  band starts at 0 and is progressive, but not the text of the law; the chosen form is the AEAT's literal method, manual
  Renta 2025 §8.4.3.2).
- **Cost of reversal**: LOW — one pure function, pinned by worked examples.

### D-2: `general_deductions_eur` lowers the general base for the minimum remainder too

- **Decision**: the remainder of the minimum that shelters savings (#395 D-1 reading 4) is now
  `max(0, personal_minimum - general_base)` where `general_base = general_income - general_deductions` (floored at 0).
  With `general_deductions_eur` absent (default 0) the hermes figure is exactly the #395 figure.
- **Alternatives**: deductions only in the total view (then the view's savings tax and the hermes tax would read two
  different general bases for the same year).
- **Rationale**: art. 56.2 speaks of the general *liquidable* base. `show_total_irpf` never changes the hermes figure; a
  deduction the operator enters does, because it is a real input to the savings tax.
- **Cost of reversal**: LOW.

### D-3: One personal minimum for both halves; Murcia uses the state 5,550

- **Decision**: the view uses the year's resolved personal minimum (profile override or `personal_minimum_eur` from
  config) for the state and the regional half.
- **Evidence**: Murcia has not approved its own *mínimo del contribuyente* under art. 46.1.a Ley 22/2009: Decreto
  Legislativo 1/2010 (BOE consolidated text, last updated 24/07/2025) has no minimum article for IRPF, and the AEAT
  comparison table of state and regional minimums for 2025 lists Andalucía, Asturias, Illes Balears, Canarias, Galicia,
  Madrid and others but not Murcia. Ley 35/2006 art. 57.1 (5,550 EUR; consolidated text as of 30/09/2026, unchanged
  since Ley 26/2014) therefore applies to both halves.
- **Cost of reversal**: MEDIUM for a region with its own minimum (the remainder that shelters savings would differ per
  half).

### D-4: Off by default, fails closed, never feeds back

- **Decision**: `show_total_irpf: false` by default. When on: no `region` in the profile gives
  `total IRPF unavailable: no region in the taxpayer profile`; no state scale or no region scale for the year gives
  `total IRPF unavailable: no scale for <region> <year>` (the state is named `state`). `TotalIrpf` is built from the
  `MarginalTax` the estimate already computed; nothing in the hermes figures reads it.
- **Rationale**: ADR-016 items 1 and 11; issue #396.
- **Cost of reversal**: LOW.

### D-5: Scale data is cited legal data

- **Decision**: every band table in `tax_scales.yaml` carries a comment naming the article and the official URL it was
  read from on 2026-10-04. A test re-derives the published *cuota íntegra* column from the configured bands, so a typo
  in a limit or rate fails.
- **Sources**:
  - State, 2025 and 2026: Ley 35/2006 art. 63.1.1º, BOE consolidated text
    <https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764> (last updated 30/09/2026; art. 63.1 last changed by Ley
    11/2020 with effect from 01/01/2021). Bands 12,450 / 20,200 / 35,200 / 60,000 / 300,000 at 9.5 / 12 / 15 / 18.5 /
    22.5 / 24.5 %.
  - Murcia, 2025 and 2026: Decreto Legislativo 1/2010, art. 2 (*Tarifa autonómica*), BOE consolidated text
    <https://www.boe.es/buscar/act.php?id=BOE-A-2011-10542> (last updated 24/07/2025; art. 2 last changed by Ley 14/2018;
    the transitional scales of disposición adicional quinta cover 2019-2022 only). Cross-checked against the AEAT manual
    Modelo 100 Renta 2025 §8.4.3.2 *Cuota íntegra autonómica*, Murcia scale. Bands 12,450 / 20,200 / 34,000 / 60,000 at
    9.5 / 11.2 / 13.3 / 17.9 / 22.5 %.
  - 2026 for Murcia rests on the consolidated text in force today: the 2025 budget law was extended into 2026 and no 2026
    law amending art. 2 appears in the consolidated text. A later 2026 law with retroactive effect would need a config
    edit.
- **Cost of reversal**: LOW.

### D-6: Region key and privacy

- **Decision**: the region is a lowercase slug (`murcia`), validated by pattern; it is private like the other profile
  values (`repr=False`, never logged) but is printed in the unavailable message to the operator's own terminal, as the
  issue asks.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Scales

- **A-1.1**: the configured state and Murcia scales reproduce the published *cuota íntegra* at every band limit (state:
  1,182.75; 2,112.75; 4,362.75; 8,950.75; 62,950.75. Murcia: 1,182.75; 2,050.75; 3,886.15; 8,540.15).
- **A-1.2**: `general_scales_for("murcia", 2027)` raises `ConfigError("no scale for murcia 2027")`; an unknown region or
  a missing `general` section fails the same way.

### R-2: General tax (worked examples, Murcia 2025/2026, minimum 5,550)

- **A-2.1**: general base 20,000 -> 3,062.60; 35,000 -> 7,343.40; 60,000 -> 16,436.40; 100,000 -> 34,436.40; 3,000
  (below the minimum) -> 0.
- **A-2.2**: hypothesis: general tax is non-decreasing in the base, zero up to the minimum, never negative.

### R-3: TotalIrpf

- **A-3.1**: `total == general_tax + savings_tax`; `savings_tax` is the full savings tax with hermes; `hermes_share` is
  the #395 hermes tax.
- **A-3.2**: general base 35,000, hermes gain 10,000 -> savings 1,980.00, total 9,323.40; general base 3,000, hermes gain
  10,000 -> general 0, savings 1,444.50 (2,550 of the minimum shelters savings), total 1,444.50.
- **A-3.3**: the hermes tax (and every other #395 figure) is identical with the view on and off.

### R-4: Off by default, fails closed

- **A-4.1**: a profile without `show_total_irpf` prints no total IRPF line and no JSON key.
- **A-4.2**: on with a missing region or scale prints `total IRPF unavailable: ...`.

### R-5: Privacy and measurement only

- **A-5.1**: `repr` of the profile and of `TotalIrpf` shows no amount and no region; the CLI logs no profile value.
- **A-5.2**: `lint-imports` keeps `tax-is-measurement-only` (the new module sits under `hermes.domain.tax`).

## Design

```python
# domain/tax/total.py
@dataclass(frozen=True, slots=True)
class GeneralScales:
    state: SavingsScale      # same band arithmetic as the savings scale
    regional: SavingsScale


def general_tax(scales: GeneralScales, general_base: Decimal, personal_minimum: Decimal) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class TotalIrpf:              # values repr=False
    general_tax: Decimal
    savings_tax: Decimal
    hermes_share: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class TotalIrpfUnavailable:
    reason: str
    @property
    def label(self) -> str: ...   # "total IRPF unavailable: <reason>"


def total_irpf(scales: GeneralScales, taxpayer: TaxpayerYear, marginal: MarginalTax) -> TotalIrpf: ...
```

`estimate_year` takes `general_scales: GeneralScales | TotalIrpfUnavailable | None = None` and sets
`TaxYearEstimate.total_irpf` to `total_irpf(...)`, the unavailable value, or `None` (off). The entry point resolves it:
off -> `None`; no region -> unavailable; `scales_config.general_scales_for(region, year)` raising `ConfigError` ->
unavailable with the error text; else the mapped domain scales.

Text output, when on, appends a `total IRPF (estimate)` heading with indented `general tax`, `savings tax`, `hermes share` and
`total` rows, or the unavailable line. JSON adds `total_irpf: {status: "estimate", general_tax_eur, savings_tax_eur, hermes_share_eur,
total_eur}` or `{status: "unavailable", reason}`; the key is absent when the view is off.

### Commands run against the current tree (2026-10-04)

- `uv run lint-imports` -> 6 contracts kept before the change (tax-is-measurement-only included).
- The worked examples in R-2 and R-3 were computed by hand from the cited tables (arithmetic in the test docstrings).

## Test plan

1. `tests/unit/domain/tax/test_total.py`: worked examples, hypothesis, `TotalIrpf` sums, repr.
2. `tests/unit/infrastructure/config/test_tax_config.py`: repo scales reproduce the published quotas, fail closed, new
   profile fields and their privacy.
3. `tests/unit/application/tax/test_estimate.py`: view on/off leaves every hermes figure identical; unavailable passes
   through.
4. `tests/unit/entrypoints/test_tax.py`: text and JSON when on, absent when off, unavailable lines.
5. `uv run pytest tests/unit tests/integration -q`, `uv run ruff check`, `uv run ruff format --check src tests`,
   `uv run mypy src`, `uv run lint-imports`.

## Risks

- **Law changes**: a 2026 Murcia law amending art. 2 retroactively would make the 2026 table stale; the source comment
  and D-5 record what was read and when.
- **Quota deductions not modelled**: the total is the *cuota íntegra*; the real liability is lower by any deductions.
- **Base reductions**: the operator must enter them in `general_deductions_eur`; with none entered the general base is
  gross income and the general tax is high (safe side).

## Dependencies on other changes

- #395 (`tax-estimates`, PR #528): this branch sits on it.
- #394 (`tax-profile-config`). ADR-016 item 11.
