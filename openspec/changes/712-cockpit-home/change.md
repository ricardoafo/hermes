# Change: 712-cockpit-home

**SDD change**: `712-cockpit-home`
**Date**: 2026-10-06
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/712-cockpit-home/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Step 2 of the Cockpit console (design spec `docs/design/console-cockpit.md`:
the Home row, round 3 "Look A with C's best parts", the responsiveness table).
The `hermes tui` home screen gets a layout per breakpoint token (compact,
standard, wide, ultra; short, tall, towering), a big total in bold
high-contrast digits over a gradient underline that counts up and shimmers, a braille area curve with a vertical gradient,
cash / invested / exposure meters, position rows with a sparkline and a
stop-room meter, the designed empty state, a Market panel on the existing
market watch, Activity with icons, an Engine brief slot (AI variant), a
Signals placeholder and a log tail at ultra width, and the 90-day run bar.
Everything is drawn with Textual 8 and Rich 15 (no new dependency) and styled
only through the issue #711 tokens. The main risk is snapshot churn; live
prices (issue #713) are out of scope.

## Scope

### In

- `src/hermes/entrypoints/tui_charts.py` (new): pure renderers for the
  big-number digits and underline, the braille area curve, the meters, the gradient sparkline
  and the 90-day bar.
- `src/hermes/entrypoints/tui_home.py` (new): the breakpoint choice
  (`home_layout`), the cockpit portfolio, position, market, empty-state,
  log-tail and run-bar content; every money figure goes through
  `RenderContext.shows_money` (`pnl_visible`).
- `src/hermes/entrypoints/tui_widgets.py`: `BigNumber` (a `Digits` in the
  `hero-digits` colour over a `hero-underline` gradient, count-up and shimmer).
- `src/hermes/entrypoints/tui.py`: the per-breakpoint home layout, the Engine
  brief, Signals and log-tail panels, the radar spinner, the exposure cap.
- `src/hermes/entrypoints/tui_theme.py`: chart heights, the radar frames;
  `TWO_COLUMNS_FROM` is removed (two columns now start at the standard
  breakpoint, 100).
- `src/hermes/application/console/render.py`: `render_portfolio` is split
  into `render_portfolio_head` and `render_portfolio_books` (the CLI output is
  unchanged); `render_activity(icons=True)` adds one icon per row;
  `render_market_watch(title=False, chart_slot=True)`.
- `src/hermes/entrypoints/console.py` + `commands/tui.py`: the exposure cap
  is read from the roster (`config/strategies.yaml`,
  `sizing.max_total_exposure_pct`) and passed to the app.

### Out

- Live Kraken prices, per-coin braille charts with prices, live P&L and the
  "live" tag: issue #713. More live data: issue #722.
- Signals heat grid: issue #714 (a placeholder panel holds its slot).
- Engine brief text: the supervisor desk (issue #365); the slot says
  "brief not available yet".
- Bar-close toast, row slide-in, checking spinner, typewriter: issue #721.
- `textual-plotext`: its own PR (issue #732).

## Decisions

### Code Owner decisions

| Date | Decision |
| --- | --- |
| 2026-10-06 | Gradient digits were illegible; the big number's digits are one bold colour (`hero-digits`, the theme's text colour) and the gradient moved to a 1-row underline (`hero-underline`), which alone carries the shimmer. Big numbers keep at least 7:1 contrast in every theme (measured on `panel`: Neon 17.0:1, Control room 15.5:1, night 16.3:1); night never dims them. This is a property of `BigNumber`, so it holds on detail screens too. Compact width and under 30 rows keep the plain bold total line. |

### D-1: one layout function, breakpoints as classes on `#body`

- **Decision**: `home_layout(width, height)` returns a frozen `HomeLayout`
  (width class, height class, columns, whether the curve shows, curve rows,
  sparklines-only, big total, log-tail rows). The app sets the width and
  height classes on `#body`; TCSS does the rest.
- **Alternatives**: style only by Textual's screen breakpoint classes (the
  content choices, such as sparklines instead of the curve, still need Python).
- **Rationale**: one tested source for every breakpoint decision.
- **Cost of reversal**: LOW.

### D-2: the exposure cap comes from the roster, never from code

- **Decision**: `roster_exposure_cap(env)` loads `strategies.yaml` from the
  console's config dir and returns `sizing.max_total_exposure_pct` when every
  enabled strategy agrees; otherwise `None`, and the meter says the cap is not
  known. The app takes it as `exposure_cap`.
- **Alternatives**: a new read-model view (the ledger does not record the
  sizing policy); a constant 15% (forbidden: the pre-registration owns it).
- **Rationale**: the roster is the pre-registration's machine copy (pre-reg
  §5.5); the console only reads it.
- **Cost of reversal**: LOW.

### D-3: exposure is invested over start capital

- **Decision**: exposure = invested ÷ start capital in EUR when the ledger
  knows it, else invested ÷ total value. The meter turns amber from 80% of
  the cap.
- **Alternatives**: open notional at entry over the USDT capital, as sizing
  checks it (the read model has no notional view yet).
- **Rationale**: the sizing check measures against the fixed capital
  (pre-reg §2), and the start capital is its EUR value; this is a display
  meter, booked figures only.
- **Cost of reversal**: LOW.

### D-4: the big total is a `Digits` subclass

- **Decision**: `BigNumber(Digits)` renders Textual's bold 3x3 glyphs in
  the `hero-digits` token, never dimmed (not at night, not when stale), and a
  4th row: a `hero-underline` gradient rule as wide as the digits
  (`tui_theme.gradient`, the token blend). A reactive float counts up over
  `MOTION.slow` with `out_cubic`; a shimmer pass every `MOTION.shimmer` moves a
  bright band along the underline at 8 fps, never over the digits. With motion
  off both are skipped and the value is set at once. A stale figure dims only
  the underline; the head line says "as of".
- **Alternatives**: per-character gradient digits (the first build; the Code
  Owner found them illegible on 2026-10-06); the box glyphs of
  `format.big_digits`.
- **Rationale**: legibility first for money figures; the gradient stays as an
  accent; one widget for every big number.
- **Cost of reversal**: LOW.

### D-5: the curve is drawn with braille in Rich text

- **Decision**: `braille_area(values, width, rows)` fills each dot column from
  the bottom to the value's height (2x4 dots per cell); `area_chart` colours
  each row from a gradient, bright at the top: the gain gradient when the
  range ends at or above its start, the loss gradient below.
- **Alternatives**: `textual-plotext` (a new dependency, issue #732).
- **Rationale**: the spec asks for no add-on; a pure function is easy to
  property-test.
- **Cost of reversal**: LOW.

### D-6: extend the shared renderers, keep one variant

- **Decision**: the CLI and the TUI share `render_portfolio_head`,
  `render_portfolio_books`, `render_activity` and `render_market_watch`; the
  TUI passes options (icons, no inner title, chart slot). The cockpit position
  rows are a TUI-only renderer because the CLI table has no meter.
- **Alternatives**: copy the renderers into the TUI.
- **Rationale**: design spec rule "never build a second variant; extend the
  component".
- **Cost of reversal**: LOW.

## Requirements

### R-1: breakpoints

- **A-1.1**: `home_layout` gives one column under 100 columns, two from 100,
  the mock layout from 160 and three columns from 220; under 30 rows the
  curve is hidden, from 45 rows chart heights double.

### R-2: portfolio

- **A-2.1**: the big total's digits are one bold `hero-digits` colour with at
  least 7:1 contrast on the panel in Neon, Control room and night (stale
  included), over a 1-row `hero-underline` gradient; it counts up when the value
  changes (motion on) and the shimmer moves only along the underline; with
  motion off the final value shows at once and no shimmer timer runs.
- **A-2.2**: the braille curve has exactly the asked rows and columns, uses
  only braille cells, and a higher value never draws a lower column.
- **A-2.3**: cash, invested and exposure meters; the exposure meter names the
  roster cap, turns amber from 80% of it, and says when the cap is unknown.
- **A-2.4**: the Today / This year waterfall and the open line keep their text.

### R-3: right column

- **A-3.1**: each position row has a sparkline, the change, the net and a
  stop-room meter that is amber when the stop is within 2 ATR.
- **A-3.2**: with no position, the radar and "hermes is watching BTC ETH SOL
  and will look again at HH:MM".
- **A-3.3**: Market keeps last check, signal and next check, with no price.
- **A-3.4**: Activity rows carry an icon per kind, grouped by day.
- **A-3.5**: the Engine brief panel is the AI variant and says "brief not
  available yet".

### R-4: ultra and the run bar

- **A-4.1**: at 220 columns a Signals placeholder and a log tail show; the
  tail says when no journal is readable or the data is a demo.
- **A-4.2**: the 90-day line ends with a gradient bar and the percentage.

### R-5: visibility and read-only

- **A-5.1**: every cockpit money renderer returns no figure when
  `pnl_visible` is false for the surface.
- **A-5.2**: the app still calls only read-model read methods.

### R-6: snapshots

- **A-6.1**: SVG snapshots at 80x24, 120x36, 160x45 and 240x60 for
  ok-two-positions, empty, and an empty live ledger, motion off, frozen clock.

## Design

```text
src/hermes/entrypoints/
  tui_theme.py   + CURVE_ROWS, RADAR_FRAMES, hero_digits / hero_underline,
                 HERO_MIN_CONTRAST, contrast_ratio; - TWO_COLUMNS_FROM
  tui_charts.py  hero_digits, gradient_rule, braille_area, area_chart, spark_text
  tui_home.py    HomeLayout, home_layout, total_text, portfolio_curve, portfolio_meters,
                 exposure_share, position_rows, empty_positions, log_tail, run_line
  tui_widgets.py + BigNumber
  tui.py         #body > #left-column (Portfolio) | #right-column (Positions,
                 #mid-row (Market | Activity), Engine brief, detail) |
                 #third-column (Signals, Log tail; ultra only)
  console.py     + roster_exposure_cap
```

| Width class | Columns | Portfolio chart |
| --- | --- | --- |
| compact < 100 | 1 | sparkline line |
| standard 100-159 | 2 (left 50) | braille, 5 rows |
| wide 160-219 | 2 (left 60), Market beside Activity | braille, 6 rows |
| ultra >= 220 | 3 | braille, 6 rows |

Height: short < 30 hides the curve and the big total; towering >= 45 doubles
the curve rows.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the PR.

Test tiers touched: unit, property

## Risks

- **Snapshot churn**: every home SVG changes. Mitigation: motion off and a
  frozen clock; the changed snapshots are listed in the PR.
- **Roster not readable on the operator's PC**: the exposure meter says the
  cap is not known instead of guessing.
- **Journal call at ultra width every 5 s**: one `journalctl` read per poll,
  only at ultra width and only on Home.

## Dependencies on other changes

- Builds on `711-cockpit-theme` (not merged yet). Follow-up: issue #713.
