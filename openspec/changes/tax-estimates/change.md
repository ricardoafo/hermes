# Change: tax-estimates

**SDD change**: `tax-estimates`
**Date**: 2026-10-03
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#395)

Mirror of engram topic key `sdd/tax-estimates/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Adds the yearly tax estimate for hermes gains, following ADR-016: a pure `domain/tax/` package (savings scale, gains by
Madrid tax year, netting with the 25% cross-offset and the 4-year carry-forward, marginal tax over the taxpayer profile,
two-month flags), an `application/tax/` service that replays the ledger's EUR trades and returns a `TaxYearEstimate` or a
console `TaxPending`, a read port on the SQLite ledger, `hermes tax [--year YYYY] [--json] [--price ASSET=EUR]`, and an
import-linter contract that keeps strategy, sizing, risk, fee and kill-rule code from reading any of it. The main pick is
that the hermes figure is marginal (tax with hermes minus tax without) and that any EUR-pending trade in the year turns the
whole estimate into `TaxPending` rather than a partial number. The main risk is a wrong legal reading (personal-minimum
remainder, shared 25% limit), so every reading is written down here and pinned by a worked example.

## Scope

### In

- `domain/tax/scale.py`: `SavingsBand`, `SavingsScale.tax(base)`, `SavingsScale.band_at(base)`.
- `domain/tax/year_position.py`: `tax_year_of(at)` (Europe/Madrid), `Disposal`, `YearPosition`.
- `domain/tax/netting.py`: `CarriedLoss`, `TaxpayerYear` (values hidden from repr), `NettingResult`, `net_savings_base`.
- `domain/tax/marginal.py`: `minimum_remainder`, `savings_tax`, `MarginalTax`, `marginal_tax`.
- `domain/tax/two_month.py`: `Acquisition`, `TwoMonthFlag`, `two_month_flags`.
- `ports/ledger.py`: `EurTradeRow` and `TaxTradeSourcePort.list_eur_trades()`; `SqliteLedger` implements it.
- `application/tax/`: `TaxYearEstimate`, `PositionTax`, `estimate_year` (pure), `TaxService` (reads the port).
- `entrypoints/tax.py` + `cli.py`: `hermes tax [--year YYYY] [--json] [--price ASSET=EUR]... [--db PATH] [--config-dir DIR]`.
- `pyproject.toml`: import-linter contract `tax-is-measurement-only`.
- Unit and property tests; `changelog.d/395-added.md`.

### Out

- Wiring the console read model to the service (#375, #377, #379): the views already accept the figures this change
  produces (`TaxEstimate`, `TaxPending`, `TaxBand`).
- Total IRPF view (#396, D-6).
- Fees to close in "tax if sold now": prices are taken as net of selling costs; the console's fee estimate (#389) can be
  folded into the price it passes.
- RCM (interest, dividend) losses: the profile allows only non-negative other savings income, so the reverse 25% offset
  (negative RCM against gains) is not modelled. Carried losses are treated as gain/loss-group losses.
- Hermes' own prior-year losses are not auto-carried from the ledger: carried losses come from the profile (the filed
  figures are authoritative). `carry_forward_left` shows what the operator should enter for the next year.
- USDT as its own FIFO asset (#386 D-4 follow-up): its EUR/USD drift is not yet a gain or loss here either.

## Decisions

All decisions below: DECIDED by implementer, Code Owner reviews in PR.

### D-1: Hermes tax is marginal over the profile

- **Decision**: `hermes_tax = savings_tax(with hermes) - savings_tax(without hermes)`, each computed from the full savings
  base of the year: hermes net + `other_capital_gains_eur` (signed) in the gains group, `other_savings_income_eur` in the
  RCM group, current-year cross-offset, then carried losses, then the personal-minimum remainder, then the scale.
- **Legal readings** (IRPF arts. 49, 56, 66):
  1. Gains-group net loss offsets up to 25% of the positive RCM balance (art. 49.1.b).
  2. Carried losses (origin year `Y-4 .. Y-1`, oldest first) offset the positive gains balance without limit, then the
     positive RCM balance within the same 25% room left by step 1 (art. 49.2: "same order and limits").
  3. A carried loss from before `Y-4` is expired and ignored; one from `Y-4` not used in `Y` expires at the end of `Y`
     (flagged). The year's own leftover net loss carries forward with origin `Y`.
  4. Personal minimum: `remainder = max(0, personal_minimum - general_income)` (art. 56.2: the minimum goes to the
     general base first, the rest to the savings base); `sheltered = min(remainder, savings_base)`;
     `savings_tax = max(0, scale(savings_base) - scale(sheltered))`. Art. 66.1.2º: the scale is applied to the whole
     savings base and the result is reduced by the same scale applied to the part of the base that corresponds to the
     minimum (Ley 35/2006, BOE consolidated text <https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764>, read
     2026-10-04). The relief is at the scale's bottom rates; subtracting the remainder from the base before the scale
     (the first reading of this change) understated the tax whenever the base crossed 6,000.
     The profile's `personal_minimum_eur` overrides the config default; `general_income_eur` is read as the general
     liquidable base.
- **Alternatives**: flat 19% (understates tax above the first band, ADR-016 item 7); hermes alone on the scale
  (ignores other income and carried losses).
- **Rationale**: ADR-016 items 6 and 7.
- **Cost of reversal**: LOW — one pure function; the worked examples pin the readings.

### D-2: Tax year is the Madrid calendar year

- **Decision**: a disposal belongs to `tax_year_of(at) = at.astimezone(Europe/Madrid).year`. "Today" is the Madrid date
  of `as_of`. 23:30 UTC on 31 December counts in the next year.
- **Rationale**: ADR-016 item 2; matches `fill_date_madrid` in #386.
- **Cost of reversal**: LOW.

### D-3: Tax if sold now is marginal over the realised year, split pro rata

- **Decision**: unrealised gain of an asset = `price_eur * open_qty - remaining acquisition value` (remaining share of
  each open lot's fee-inclusive EUR acquisition value). `tax_if_sold_now = marginal(realised + unrealised) -
  marginal(realised)`. Each asset gets `tax_if_sold_now * u_i / sum(u)`, the last asset takes the remainder (exact sum);
  all zero when `sum(u) == 0`. Shares may be negative (a losing position lowers the total). Only for the current tax
  year; a past year returns `TaxPending("not the current tax year")`. A missing price, or an open lot whose EUR value is
  pending, makes the whole figure (and each position) `TaxPending`.
- **Alternatives**: per-position marginal tax in isolation (shares would not add up to the total; order-dependent).
- **Rationale**: issue scope; the console needs per-position figures that add up.
- **Cost of reversal**: LOW.

### D-4: Two-month rule flagged, not applied

- **Decision**: a sale in the year whose summed gain is negative is flagged when the same asset was bought within two
  calendar months before or after the sale date (Madrid), excluding the lots that sale consumed. The loss still counts in
  full.
- **Rationale**: ADR-016 item 8 (art. 33.5.f is written for listed securities).
- **Cost of reversal**: MEDIUM — a defer switch needs a new ADR and a netting change.

### D-5: `TaxPending` instead of guessing

- **Decision**: the whole year estimate is `TaxPending(label="tax pending: <reason>")` when the scale for the year is
  missing ("scale missing for 2027"), when any trade executed in the year is EUR-pending, or when any disposal in the year
  consumed a lot whose EUR value is pending. No partial figure.
- **Alternatives**: estimate from priced trades only (a silently low number).
- **Rationale**: ADR-016 item 1 (fail closed); #386 D-2 (pending rows are backfilled later).
- **Cost of reversal**: LOW.

### D-6: Total IRPF stays a separate opt-in view

- **Decision**: nothing here computes the general-base tax; #396 adds it as its own view, off by default, and it never
  changes the hermes figure.
- **Rationale**: ADR-016 item 11.
- **Cost of reversal**: n/a.

### D-7: Measurement only — never read by trading code

- **Decision**: import-linter contract `tax-is-measurement-only` forbids `hermes.domain.strategy`, `hermes.domain.risk`,
  `hermes.domain.fees`, `hermes.application.order_router` (router and sizing), `hermes.application.strategy_protocol`
  (kill rules) and `hermes.application.tax_guard` (it can pause trading) from importing `hermes.domain.tax` or
  `hermes.application.tax`.
- **Rationale**: ADR-016 item 9; Code Owner 2026-10-03.
- **Cost of reversal**: LOW.

### D-8: Reuse the console view objects for figures; one new bundle type

- **Decision**: every figure is a console `TaxEstimate(amount)` or `TaxPending(label)`, and the band is a console
  `TaxBand`, so the read model plugs them into `WaterfallColumn`, `PortfolioSummary` and `PositionDetail` unchanged. The
  service's year result is a new `TaxYearEstimate` (application/tax) bundling those figures with the year, realised gain,
  carry-forward, flags, notices and the labels. `views.py` is not changed.
- **Alternatives**: widen the console `TaxEstimate` with the issue's year fields (it is a per-figure value used in four
  views; a year bundle inside a waterfall column makes no sense); parallel `TaxFigure` types (two names for one thing).
- **Rationale**: the issue's `TaxEstimate(year, ...)` is the bundle; the console's `TaxEstimate` is the figure. Keeping
  both names would collide, so the bundle is `TaxYearEstimate`.
- **Cost of reversal**: LOW.

### D-9: Domain types for scale and profile; config mapped at the entry point

- **Decision**: `domain/tax` defines its own frozen dataclasses for the scale and the year profile; `entrypoints/tax.py`
  maps the #394 pydantic models onto them. `other_capital_gains` is signed in the domain (ready for #438).
- **Alternatives**: application imports `infrastructure.config.tax` (breaks `application-imports-only-domain-and-ports`);
  domain uses pydantic (breaks `domain-no-pydantic`).
- **Cost of reversal**: LOW.

## Requirements

### R-1: Scale by year

- **A-1.1**: worked examples at each band edge (6k, 50k, 200k, 300k) on the 2026 scale give 1,140; 10,380; 44,880;
  71,880; and above 300k add 30%.
- **A-1.2**: a year with no scale returns `TaxPending` labelled `tax pending: scale missing for <year>`.
- **A-1.3**: hypothesis: `scale.tax` is non-decreasing in the base and zero for a non-positive base.

### R-2: Netting and carry-forward

- **A-2.1**: a loss year returns negative realised gain, hermes tax `<= 0` (a credit only when other savings income or
  gains absorb it) and the leftover loss in `carry_forward_left` with origin `Y`.
- **A-2.2**: a carried loss from `Y-4` that is not used is flagged as expiring and absent from `carry_forward_left`; one
  older than `Y-4` is ignored.
- **A-2.3**: hypothesis: every carried loss used has origin `>= Y-4`; every loss left has origin `>= Y-3`.
- **A-2.4**: hypothesis: marginal tax is never negative when hermes net is positive.
- **A-2.5**: personal-minimum edge case: general income 0, minimum 5,550, hermes gain 10,000 -> tax 925.50
  (1,980.00 - 1,054.50, art. 66.1.2º).
- **A-2.6**: hypothesis: the savings tax is never below `scale(max(0, base - remainder))`, and is 0 while the savings
  base is at most the minimum remainder.

### R-3: Today's tax

- **A-3.1**: today's tax = year tax through the end of today minus through the start of today (Madrid); negative is a
  credit.
- **A-3.2**: hypothesis: today's tax summed over every day of the year equals the year's hermes tax, exactly.

### R-4: Tax if sold now

- **A-4.1**: with prices passed in, the total equals marginal(realised + unrealised) minus marginal(realised) and the
  per-position figures add up to it exactly.
- **A-4.2**: a missing price or a pending lot gives `TaxPending`; no I/O in `application/tax` (prices are a parameter).

### R-5: Labels and pending

- **A-5.1**: every `TaxYearEstimate` has `is_estimate is True`; `label` is `estimate` or
  `estimate · shadow (no real tax due)`.
- **A-5.2**: `hermes tax` text and JSON output contain `estimate`, and `shadow` while hermes runs shadow.
- **A-5.3**: any EUR-pending trade in the year gives `TaxPending`.

### R-6: Profile privacy

- **A-6.1**: `repr(TaxpayerYear(...))` and `repr(CarriedLoss(...))` show no amounts.
- **A-6.2**: running `hermes tax` with a profile logs no profile value (caplog check).

### R-7: Measurement only

- **A-7.1**: `uv run lint-imports` keeps `tax-is-measurement-only`.

## Design

### Domain (`src/hermes/domain/tax/`)

```python
# scale.py
@dataclass(frozen=True, slots=True)
class SavingsBand:
    up_to: Decimal | None
    rate: Decimal


@dataclass(frozen=True, slots=True)
class SavingsScale:
    bands: tuple[SavingsBand, ...]  # ascending, last unbounded (validated)

    def tax(self, base: Decimal) -> Decimal: ...  # 0 for base <= 0
    def band_at(self, base: Decimal) -> BandPosition: ...  # rate, next_rate, room_to_next


# year_position.py
def tax_year_of(at: datetime) -> int: ...  # Madrid


@dataclass(frozen=True, slots=True)
class Disposal:
    asset: str
    ref: str
    at: datetime
    gain: Decimal
    consumed_refs: frozenset[str]


@dataclass(frozen=True, slots=True)
class YearPosition:
    year: int
    gains: Decimal
    losses: Decimal  # losses as a positive magnitude

    @property
    def net(self) -> Decimal: ...
    @classmethod
    def of(cls, year: int, disposals: Iterable[Disposal]) -> YearPosition: ...


# netting.py
@dataclass(frozen=True, slots=True)
class CarriedLoss:
    origin_year: int
    amount: Decimal  # repr=False on values


@dataclass(frozen=True, slots=True)
class TaxpayerYear:  # every field repr=False
    general_income: Decimal = 0
    personal_minimum: Decimal = 0
    other_savings_income: Decimal = 0
    other_capital_gains: Decimal = 0  # signed
    carried_losses: tuple[CarriedLoss, ...] = ()


@dataclass(frozen=True, slots=True)
class NettingResult:
    savings_base: Decimal
    carried_used: tuple[CarriedLoss, ...]
    carried_left: tuple[CarriedLoss, ...]
    expiring: tuple[CarriedLoss, ...]


def net_savings_base(year: int, hermes_net: Decimal, taxpayer: TaxpayerYear) -> NettingResult: ...


# marginal.py
def minimum_remainder(taxpayer: TaxpayerYear) -> Decimal: ...  # max(0, minimum - general_income)


@dataclass(frozen=True, slots=True)
class SavingsTax:
    netting: NettingResult
    sheltered: Decimal  # min(minimum_remainder, savings_base)
    tax: Decimal  # scale(base) - scale(sheltered); repr=False


def savings_tax(scale, taxpayer, hermes_net, *, year) -> SavingsTax: ...


@dataclass(frozen=True, slots=True)
class MarginalTax:
    hermes_tax: Decimal
    with_hermes: SavingsTax
    without_hermes: SavingsTax


def marginal_tax(
    scale: SavingsScale, taxpayer: TaxpayerYear, hermes_net: Decimal, *, year: int
) -> MarginalTax: ...


# two_month.py
@dataclass(frozen=True, slots=True)
class Acquisition:
    asset: str
    ref: str
    at: datetime


@dataclass(frozen=True, slots=True)
class TwoMonthFlag:
    asset: str
    sold_on: date
    loss: Decimal
    bought_on: date


def two_month_flags(disposals, acquisitions) -> tuple[TwoMonthFlag, ...]: ...
```

All arithmetic runs in a 100-digit `localcontext`; nothing is rounded before display.

### Port and adapter

```python
# ports/ledger.py
@dataclass(frozen=True, slots=True)
class EurTradeRow:
    trade_id: int
    asset: Currency
    side: OrderSide
    qty: Decimal
    executed_at: datetime
    price_eur: Decimal | None
    fee_eur: Decimal | None


@runtime_checkable
class TaxTradeSourcePort(Protocol):
    def list_eur_trades(self) -> tuple[EurTradeRow, ...]: ...  # every lot-linked BUY and every SELL
```

`SqliteLedger.list_eur_trades` selects the same rows `_reproject` replays (`side = 'SELL' OR id IN lots.trade_id`), all
assets, ordered by `executed_at, id`.

### Application (`src/hermes/application/tax/`)

```python
@dataclass(frozen=True, slots=True)
class PositionTax:
    asset: str
    unrealised_gain: Decimal | None
    tax_if_sold_now: Tax


@dataclass(frozen=True, slots=True)
class TaxYearEstimate:
    year: int
    realised_gain: Decimal
    hermes_tax: TaxEstimate
    today_tax: TaxEstimate
    tax_if_sold_now: Tax
    per_position_if_sold_now: tuple[PositionTax, ...]
    carry_forward_used: Decimal
    carry_forward_left: tuple[CarriedLoss, ...]
    band: TaxBand
    flags: tuple[str, ...]
    notices: tuple[str, ...]
    is_shadow: bool
    is_estimate: Literal[True] = True

    @property
    def label(self) -> str: ...


def estimate_year(
    trades,
    *,
    year,
    scales: Mapping[int, SavingsScale],
    taxpayer: TaxpayerYear | None,
    default_personal_minimum: Decimal,
    prices: Mapping[str, Decimal],
    as_of: datetime,
    is_shadow: bool,
) -> TaxYearEstimate | TaxPending: ...


class TaxService:
    def __init__(self, source: TaxTradeSourcePort) -> None: ...
    def estimate(self, **same_keywords) -> TaxYearEstimate | TaxPending: ...
```

`estimate_year` groups rows by asset, replays each with `replay_fifo_eur`, folds consumptions into one `Disposal` per
sell, and computes the figures above. `taxpayer=None` means zeros with the config default minimum and adds the notice
`no taxpayer profile, assuming no other income`.

### Entry point

`hermes tax [--year YYYY] [--json] [--price ASSET=EUR]... [--db PATH] [--config-dir DIR]`: loads `tax_scales.yaml` and the
profile, maps them to domain types, opens the ledger read-only-in-practice (exit 1 when the file is missing, so a typo never
creates an empty ledger), runs the service with `as_of = now_utc()` and `is_shadow=True` (hermes has no live exchange in
MVP1), prints text or JSON. Figures print rounded to cents (`ROUND_HALF_UP`). Exit codes: 0 printed (estimate or pending),
1 ledger error, 2 usage, 3 config error.

### Commands run against the current tree (2026-10-03)

- `uv run lint-imports` -> `Contracts: 6 kept, 0 broken.`
- `config/tax_scales.yaml` has 2025 and 2026 (19/21/23/27/30, limits 6k/50k/200k/300k), personal minimum 5,550.
- `application` may not import `infrastructure` (contract `application-imports-only-domain-and-ports`), hence D-9.
- `rg "TaxEstimate|TaxPending" src` -> used by `views.py`, `render.py`, `fixture_read_model.py`; only `amount` and `label`.

## Test plan

1. Unit + hypothesis: `tests/unit/domain/tax/` (scale, year position, netting, marginal, two-month),
   `tests/unit/application/tax/` (service with in-memory rows and with a real in-memory `SqliteLedger`),
   `tests/unit/entrypoints/test_tax.py` (text, JSON, pending, missing ledger, privacy).
2. `uv run pytest tests/unit -q`, `uv run ruff check`, `uv run ruff format --check src tests`, `uv run mypy src`,
   `uv run lint-imports`.

## Risks

- **Law changes**: rates live in `config/tax_scales.yaml` per year; a missing year is pending, never a guess.
- **Gestor disagreement on the two-month rule**: the flag stays; switching to defer is a new ADR (ADR-016).
- **USDT/USD approximation**: EUR values inherit the de-peg error from #386; not corrected here.
- **Personal-minimum reading**: with no profile, general income is zero, so the minimum remainder (5,550) shelters the
  first hermes gains; the output carries the no-profile notice. A real profile with salary removes the effect.
- **Shared 25% room**: reading 2 above; if a gestor applies separate limits the estimate is slightly high (safe side).
- **Rounding**: the 100-digit context keeps today's figures summing exactly to the year; display rounds to cents only.

## Dependencies on other changes

- #386 (`trade-eur-values`, PR #435): `replay_fifo_eur`, EUR trade columns. This branch sits on it.
- #394 (`tax-profile-config`): scales and profile (on main). #438 may make `other_capital_gains_eur` signed; the domain
  already accepts a signed value.
- ADR-016. Blocks the tax lines in the console (#375, #377, #379).
