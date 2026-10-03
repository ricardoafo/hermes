# Change: console-read-model

**SDD change**: `console-read-model`
**Date**: 2026-10-03
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/console-read-model/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

This change adds the one contract every operator console surface reads from:
a `ConsoleReadModel` protocol in the application layer that returns plain,
frozen view objects (heartbeat, portfolio for a range, position rows and
detail, day-grouped activity, the 90-day shadow test, and a tax figure that is
either an estimate or pending). A fixture adapter serves six named scenarios
shipped as package data, `hermes --demo <scenario>` and `HERMES_CONSOLE_DEMO`
select it, and shared Rich renderers turn the views into the screens of the
approved UX proposal, with a "DEMO DATA" tag whenever the data is a fixture.
`pnl_visible(surface)` decides, per surface and with no override, whether any
money appears. The main risk is a later surface or renderer that shows money
without asking `pnl_visible`; every renderer takes the surface in its render
context and the blind tests scan the rendered text for money.

## Scope

### In

- `src/hermes/application/console/`: `views.py` (view objects), `read_model.py`
  (`ConsoleReadModel` protocol), `visibility.py` (`Surface`, `pnl_visible`),
  `format.py` (money, percent, price, sparkline and stop-bar helpers),
  `render.py` (`RenderContext` and the six renderers plus an overview).
- `src/hermes/adapters/console/`: `FixtureReadModel` and the six scenario JSON
  files as package data under `scenarios/`.
- `src/hermes/entrypoints/console.py`: the `--demo` flag and
  `HERMES_CONSOLE_DEMO` resolution, the read-model factory future console
  commands call, and `hermes --demo <scenario>` printing the overview.
- `src/hermes/entrypoints/cli.py`: strips the leading `--demo` option before
  dispatch.
- `pyproject.toml`: `rich` promoted from a transitive to a direct dependency.
- Tests: view invariants (hypothesis), formatting (hypothesis), visibility per
  surface, fixture loading and validation, renderer behaviour (DEMO DATA, blind
  surfaces, tax pending, stale numbers), syrupy snapshots per scenario and
  renderer at 80 and 120 columns, CLI demo plumbing.
- `changelog.d/wave-384-console-read-model.md`.

### Out

- The commands themselves: `hermes activity` (#374), `hermes positions`
  (#375), `hermes tui` and the static home screen (#377), Telegram replies
  (#379). They register into `CONSOLE_COMMANDS` and call the factory added here.
- `hermes status` and its read-only ledger queries (#370, built in parallel).
- The ledger-backed read model (`LedgerReadModel`, build-plan step 7). Until
  it exists the factory raises `LiveReadModelUnavailableError` without `--demo`.
- Day grouping and folding of real ledger rows (#374): the view objects carry
  already grouped days and folded stop raises; the fixture provides them.
- `--json` and `--utc` output (#374, #375): the views are plain dataclasses so
  a JSON encoder is a small addition there.
- The tax module (P2), EUR values on trades (P3), portfolio in the ledger (P5),
  stop per bar (P6) and the live price lookup (P7) from the build plan.
- Textual widgets, big digits and the colour-blind theme (#377). Rich styles
  here use theme-neutral names only.

## Decisions

### D-1: The protocol and views live in `hermes.application.console`

- **Decision**: `ConsoleReadModel`, the view objects and `pnl_visible` live in
  a new application package; the issue fixes the layer.
- **Alternatives**: (a) `hermes.ports`: the existing ports are driven ports of
  the trading core; this is a query-side contract owned by the console use
  case. (b) `hermes.entrypoints`: the Telegram adapter (#379) could then not
  import the views, because adapters must not import entrypoints.
- **Rationale**: the application layer is importable by adapters and
  entrypoints alike, and the `application-imports-only-domain-and-ports`
  contract keeps it free of adapters and infrastructure.
- **Cost of reversal**: LOW (module moves).

### D-2: Renderers live in the application layer, beside the views

- **Decision**: `hermes.application.console.render` holds pure functions from
  view objects plus a `RenderContext` to Rich renderables.
- **Alternatives**: (a) `hermes.entrypoints`: unreachable from the Telegram
  adapter. (b) a new adapters package: the TUI, the CLI and the Telegram reply
  handler would depend on an adapter for formatting, and the renderers do no
  I/O.
- **Rationale**: the same precedent as `hermes.application.messages` (pure
  message templates). Rendering never reads the clock or the environment: the
  context carries `now`, the timezone, the surface and the demo scenario.
- **Cost of reversal**: LOW.

### D-3: Scenarios ship as package data; tests read the same files

- **Decision**: the six scenarios live in
  `src/hermes/adapters/console/scenarios/<name>.json` and load through
  `importlib.resources`. Tests use `FixtureReadModel.load(name)`; there is no
  copy under `tests/fixtures/console/`.
- **Alternatives**: (a) `tests/fixtures/console/` as the issue words it: the
  `tests/` tree is not in the wheel, so `hermes --demo` would fail on the VPS
  and on any installed copy. (b) both locations: two copies drift.
- **Rationale**: `--demo` must work from an installed wheel; hatchling's
  `packages = ["src/hermes"]` ships every non-ignored file in the package, and
  `git check-ignore` reports no rule matching the scenario path. One source of
  truth for demo and tests.
- **Cost of reversal**: LOW. This is the one deviation from the issue text.

### D-4: The read model names its own data source

- **Decision**: `ConsoleReadModel.demo_scenario` returns the scenario name for
  a fixture and `None` for real data; `RenderContext.for_model(...)` copies it,
  and every renderer prints a "DEMO DATA" tag when it is set.
- **Alternatives**: a CLI-only flag passed to each renderer: a TUI or Telegram
  handler built on the fixture could forget it.
- **Rationale**: the tag follows the data, so a demo can never be mistaken for
  real numbers whatever surface shows it.
- **Cost of reversal**: LOW.

### D-5: `pnl_visible` is a closed allow list over a `Surface` enum

- **Decision**: `pnl_visible(surface) -> bool` is True only for
  `OPERATOR_CLI`, `OPERATOR_TUI` and `TELEGRAM_PULLED`; every other member,
  including any added later, is blind. No date, environment or config input.
- **Alternatives**: reuse `ReportingPhase` (#319): that governs pushed messages
  by certification phase; the Code Owner's ruling on 2026-10-03 is per surface
  and has no override.
- **Rationale**: fail closed; a new surface is blind until a reviewed change
  adds it to the allow list. Pushed messages stay blind under both rules.
- **Cost of reversal**: LOW.

### D-6: Money is `Decimal` EUR; display rounds half up to 2 decimals

- **Decision**: view objects hold `Decimal` EUR amounts; `format.py` rounds €
  and % to 2 decimals (`ROUND_HALF_UP`), and every change or P&L figure carries
  its sign and an arrow (`▲ +`, `▼ −`, and a middle dot for zero after rounding). Levels
  (total value, cash, invested, prices) are unsigned.
- **Alternatives**: the `Money` value object: its `Result`-returning arithmetic
  adds nothing to read-only display DTOs that are EUR by definition.
- **Rationale**: ADR-003 §07 "triple-encode" (hue, glyph, sign) and the UX
  proposal's readability rules. The arrow follows the rounded value so a
  `-0.004` never prints as `▼ −€0.00`.
- **Cost of reversal**: LOW.

### D-7: Net is derived, never stored

- **Decision**: `WaterfallColumn.net` and `PositionDetail.net` are properties
  (`gross − fees − tax`, with tax counted as zero while pending). Scenario
  files store no row net, change since entry or stop distance: the fixture
  loader derives them from the detail and the prices, so a scenario cannot
  contradict itself, and a test checks row net equals detail net.
- **Rationale**: net = gross − fees − tax is the invariant #375 asserts; making
  it structural removes a class of fixture and adapter bugs.
- **Cost of reversal**: LOW.

### D-8: `rich` becomes a direct dependency

- **Decision**: add `rich>=15,<16` to `[project] dependencies`.
- **Alternatives**: keep relying on the transitive copy from `textual` and
  `typer`: an upstream change to their constraints could drop or move it.
- **Rationale**: the console imports `rich` directly; the lock already resolves
  `rich 15.0.0`, so no new package enters the environment.
- **Cost of reversal**: LOW.

### D-9: `--demo` is a leading global option; the environment variable is soft

- **Decision**: `hermes --demo <scenario> [command]` (or `--demo=<scenario>`)
  is parsed before dispatch. With no command it prints the overview. With a
  non-console command (`serve`, `certify`, `health`, `preview`) it exits 2.
  `HERMES_CONSOLE_DEMO` only feeds console commands; other commands never read
  it. An unknown scenario exits 2 and lists the valid names.
- **Rationale**: a demo flag must never reach the bar loop; a stray environment
  variable on the VPS must not break `hermes serve`.
- **Cost of reversal**: LOW.

## Requirements

### R-1: The contract

- **A-1.1**: `uv run pytest tests/unit/application/console -q` passes.
- **A-1.2**: `uv run lint-imports` keeps all five contracts.
- **A-1.3**: `uv run mypy src` is clean; `FixtureReadModel` is checked against
  `ConsoleReadModel` structurally by an assignment in its test module.

### R-2: Fixtures

- **A-2.1**: `uv run pytest tests/unit/adapters/console -q` passes: all six
  scenarios load (`ok-two-positions`, `late`, `down-stale`, `losing-year`,
  `tax-pending`, `empty`), an unknown name raises `UnknownScenarioError`, a
  naive timestamp or a float amount raises `ScenarioError`, and every row net
  equals its detail net.

### R-3: Demo switch

- **A-3.1**: `uv run hermes --demo ok-two-positions` exits 0 and prints
  `DEMO DATA`.
- **A-3.2**: `uv run hermes --demo nope` exits 2; `uv run hermes --demo
  ok-two-positions serve` exits 2.
- **A-3.3**: a test renders every renderer for every scenario in demo mode and
  finds `DEMO DATA` in each output.

### R-4: Visibility

- **A-4.1**: one parametrised test per blind surface asserts False and one per
  operator surface asserts True.
- **A-4.2**: blind renders of every scenario contain no `€` sign and no
  percentage.

### R-5: Renderers

- **A-5.1**: syrupy snapshots exist for every scenario × renderer at 80 and 120
  columns (`tests/unit/application/console/__snapshots__/`).
- **A-5.2**: the tax-pending scenario prints `tax estimate: not available yet`;
  the losing-year scenario prints the tax as a credit (`+`).

## Design

```text
src/hermes/application/console/
  __init__.py      re-exports
  views.py         Heartbeat, HeartbeatState, PortfolioRange, PortfolioSummary,
                   WaterfallColumn, TaxEstimate, TaxPending, Tax, TaxBand,
                   PositionRow, PositionDetail, TimelineEvent, ActivityKind,
                   ActivityItem, ActivityDay, ShadowTest, ShadowTestStatus
  read_model.py    ConsoleReadModel (Protocol)
  visibility.py    Surface (StrEnum), pnl_visible
  format.py        fmt_eur, fmt_pct, fmt_change, fmt_level, fmt_price,
                   sparkline, stop_bar, cushion_level, CushionLevel
  render.py        RenderContext, render_heartbeat, render_portfolio,
                   render_positions, render_position_detail, render_activity,
                   render_shadow_test, render_overview, to_plain_text
src/hermes/adapters/console/
  __init__.py
  fixture_read_model.py   FixtureReadModel, ScenarioError, UnknownScenarioError,
                          available_scenarios
  scenarios/{ok-two-positions,late,down-stale,losing-year,tax-pending,empty}.json
src/hermes/entrypoints/console.py   CONSOLE_COMMANDS, DEMO_ENV_VAR,
                                    split_demo_flag, apply_demo_flag,
                                    resolve_read_model, run_overview,
                                    LiveReadModelUnavailableError
```

```python
class ConsoleReadModel(Protocol):
    @property
    def demo_scenario(self) -> str | None: ...
    def heartbeat(self) -> Heartbeat: ...
    def portfolio(self, range_: PortfolioRange = PortfolioRange.TODAY) -> PortfolioSummary: ...
    def positions(self) -> tuple[PositionRow, ...]: ...
    def position(self, coin: str) -> PositionDetail | None: ...
    def activity(self, *, include_quiet: bool = False) -> tuple[ActivityDay, ...]: ...
    def shadow_test(self) -> ShadowTest: ...


@dataclass(frozen=True, slots=True)
class TaxEstimate:
    amount: Decimal  # owed; negative is a credit


@dataclass(frozen=True, slots=True)
class TaxPending:
    label: str = "tax estimate: not available yet"


type Tax = TaxEstimate | TaxPending


def pnl_visible(surface: Surface) -> bool: ...
```

Heartbeat carries `state`, `as_of` (read time), `last_check_at`,
`next_check_at` and `down_since`. The portfolio carries the range, `as_of`,
total value, the change in € and %, net for the range, the sparkline points,
cash and invested, the Today and This-year waterfall columns (closed trades
only), open P&L with "tax if sold now", and the tax band for the footnote
(`None` while pending). A position row carries coin, timeframe, side, size,
opened at, entry, now, change %, net, stop, high since entry and the stop
distance in ATR; the detail adds strategy, entry values, stop moves, gross,
fees paid, estimated fees to close, tax, a price path and a timeline. Blind
renders show only coin, side, opened at, entry, size and stop level (#375).

Scenario JSON keeps every amount as a string (`"12.30"`) so it parses into an
exact `Decimal`, and every timestamp with an offset. Times render in
Europe/Madrid unless the context says otherwise.

Commands run against the current tree while writing this design:

```text
uv run lint-imports            -> Contracts: 5 kept, 0 broken.
rg -n "rich" pyproject.toml    -> no match (transitive via textual and typer)
rg -n '^name = "rich"' -A1 uv.lock -> version = "15.0.0"
git check-ignore -v src/hermes/adapters/console/scenarios/ok-two-positions.json -> exit 1 (not ignored)
uv run python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Madrid')" -> ok (tzdata in lock)
```

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest tests/unit -q`, `uv run ruff check`, `uv run ruff format
   --check src tests`, `uv run mypy src`, `uv run lint-imports` all green.
3. A human reads the snapshot file once, since it is the first rendering of the
   console screens.

## Risks

- **A future renderer shows money without asking `pnl_visible`**: every
  renderer takes `RenderContext.surface`, and the blind-scan test runs every
  renderer for every scenario on a blind surface. New renderers join the same
  parametrised list.
- **Snapshots drift silently**: snapshot updates need `--snapshot-update` and
  appear in the diff; the behaviour tests (sign, arrow, net, tax label, DEMO
  DATA) fail independently of the snapshots.
- **Trailing whitespace in Rich output**: the pre-commit hook would rewrite the
  snapshot file; `to_plain_text` strips line ends and the snapshots store one
  string per line, so blank lines survive the hook.
- **Blind leak through activity**: a close price next to the entry price gives
  away the result, so blind activity rows drop the close price as well as net.
- **Parallel edit of `cli.py` by #370**: the change there is a few lines at the
  top of `main`, so a rebase conflict stays small.

## Dependencies on other changes

- None blocking. #374, #375, #377 and #379 build on this. #370 runs in
  parallel; `hermes status` can adopt `--demo` by joining `CONSOLE_COMMANDS`.
