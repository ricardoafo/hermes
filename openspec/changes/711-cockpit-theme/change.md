# Change: 711-cockpit-theme

**SDD change**: `711-cockpit-theme`
**Date**: 2026-10-05
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/711-cockpit-theme/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Step 1 of the Cockpit console (design spec `docs/design/console-cockpit.md`,
round 3). `hermes tui` gets one design-token module, three Textual themes built
from it (Neon, Control room, night), a reusable `HudPanel` frame around every
panel, the Neon window bar, a screen tab bar with number keys, footer key
chips, a theme chip and picker, a 1 s clock and countdown, a terminal tab
title, and a pulsing cyan live dot that fixes "live is gray". The home screen
keeps its content; its full-width layout is issue #712. The main pick is to
keep everything on Textual 8 + Rich 15 (no new dependency) and draw the
signature chrome (bracket corners, gradient title bars) with Rich text instead
of Textual borders. The main risk is visual churn in the existing snapshots.

## Scope

### In

- `docs/design/console-cockpit.md`: the agreed design spec, with issue numbers.
- `src/hermes/entrypoints/tui_theme.py`: base tokens, Neon overrides, the night
  sheet, three Textual themes, breakpoints, glyphs, motion timings, the
  start-up logo slot with the HERMES wordmark fallback.
- `src/hermes/entrypoints/tui_settings.py`: `~/.config/hermes/console.toml`
  (theme choice), read with `tomllib`, written by hand.
- `src/hermes/entrypoints/tui_widgets.py`: `HudPanel`, `WindowBar`, `TabBar`,
  `ThemeChip`, `ThemePicker`, `key_chips`.
- `src/hermes/entrypoints/tui.py`: the console chrome, tabs `1`-`8` with
  "coming next · issue #N" screens, Tab / Shift+Tab, Ctrl+P commands, Esc back,
  T theme picker, clock and countdown ticks, night switching, OSC 2 title,
  motion-off switch.
- `src/hermes/application/console/render.py`: the OK heartbeat's dot gets its
  own style (`RenderContext.live_dot`); the words stay dim.
- `src/hermes/entrypoints/commands/tui.py`: `--theme neon|control|auto`.

### Out

- Big-total shimmer and count-up, the full-width home layout: issue #712.
- Live price flashes: issue #713. More live data: issue #722.
- Start-up sequence, AI typewriter, slide-ins, bar-close toast: issue #721.
- Query bar, drill-down, runs: issues #729, #730, #731.
- The screens behind tabs 2-8: issues #714-#720.

## Decisions

### D-1: one token module in the entrypoints layer

- **Decision**: `tui_theme.py` holds every colour, gradient, breakpoint, glyph and timing; TCSS reads them as `$hud-*` theme variables and Python renderers read the `Tokens` sheet.
- **Alternatives**: TCSS file with literal colours (two sources of truth); tokens in the application layer (it would import Textual).
- **Rationale**: a token change is a one-line edit; the application layer stays free of Textual (lint-imports contracts).
- **Cost of reversal**: LOW.

### D-2: night is a third theme, not a dimmed Control room at runtime

- **Decision**: `hermes-night` is its own sheet (Control room dimmed, darker up/down, warn unchanged); Auto picks Neon by day and night from 23:00 to 08:00.
- **Alternatives**: dim Control room with CSS opacity (dims warnings too); two themes only (cannot darken gains/losses without touching the day theme).
- **Rationale**: the Code Owner's theme chip comment: dimmed colours at night, warnings fully bright.
- **Cost of reversal**: LOW.

### D-3: bracket corners and gradient title bars drawn as Rich text

- **Decision**: `HudPanel` draws a top and a bottom edge row itself (cyan `┏━` top-left, magenta `━┛` bottom-right, a per-cell gradient title bar, the producer label right-aligned).
- **Alternatives**: Textual `border: round` with `border_title` (one colour per border, no gradient); a custom `render_line` frame (more code, no gain).
- **Rationale**: the spec's signature element needs two corner colours and a gradient; text rows are cheap over ssh and testable as plain functions.
- **Cost of reversal**: LOW (fallback is `border: round`).

### D-4: tabs are a `ContentSwitcher`, not Textual modes

- **Decision**: one base screen keeps the chrome; the body switches between eight containers.
- **Alternatives**: `App.MODES` (each mode its own screen stack; the chrome would be duplicated per screen).
- **Rationale**: the chrome and the 5 s poll are shared; existing panel screens (detail, logs, help) keep working on top.
- **Cost of reversal**: MEDIUM once real screens land.

### D-5: terminal title via OSC 2 through the driver

- **Decision**: `osc_title()` builds `ESC ] 2 ; text BEL` with control characters removed; the app writes it through its driver only when the title changes.
- **Alternatives**: none in Textual 8 (no title API).
- **Rationale**: isolated in one tested function; the headless test driver ignores writes.
- **Cost of reversal**: LOW.

### D-6: two columns stay at 120 until issue #712

- **Decision**: breakpoint classes (compact/standard/wide/ultra, short/tall/towering) are on the screen now, but the home screen keeps today's two-column split at 120.
- **Alternatives**: split at 100 (the positions table wraps badly at 100-119 with today's renderers).
- **Rationale**: issue #711 re-hosts home with unchanged content; issue #712 owns the per-breakpoint layout.
- **Cost of reversal**: LOW (`TWO_COLUMNS_FROM`).

### D-7: the theme choice file is written by hand

- **Decision**: `tomllib` reads `console.toml`; the writer emits `theme = "<choice>"`.
- **Alternatives**: a TOML writer library (new dependency, needs the Code Owner's yes).
- **Rationale**: one string key; no new dependency in this PR.
- **Cost of reversal**: LOW.

### D-8: what counts as motion

- **Decision**: the live-dot pulse and the binary strip are effects and stop with `HERMES_CONSOLE_MOTION=off`; the 1 s clock and countdown keep ticking because they carry information.
- **Alternatives**: freeze the clock too (the countdown would lie).
- **Rationale**: motion off means no animation, not stale figures.
- **Cost of reversal**: LOW.

### D-9: token values the spec leaves open (implementer decision)

- **Decision**: Control room's `frame` is #2C4F86 (the base focus border) and its `chrome` gradient is #58D1EB → #468CE6 → #9D65FF (the base live → up → ai); Neon's `focus_border` is #8A5CFF (the Neon `frame`). All three live as tokens in `tui_theme.py`.
- **Alternatives**: flat Control room title bars (no gradient); a separate Neon focus colour.
- **Rationale**: the spec gives `frame` and `chrome` for Neon only and no Neon focus border; reusing colours each theme already has keeps every theme to its own palette.
- **Cost of reversal**: LOW (three token values).

### D-10: the window bar is Neon chrome

- **Decision**: the window bar ("▐ HERMES // SHADOW CONSOLE", binary strip, clock) shows in Neon only; Control room and night hide it, so the tab bar leads the screen and no clock is shown there.
- **Alternatives**: keep the bar in Control room in calmer colours (the first build).
- **Rationale**: the spec lists the gradient window bar and the ticking strip among what Neon adds to the chrome.
- **Cost of reversal**: LOW (one `display` switch in `_apply_theme`).

## Requirements

### R-1: tokens and themes

- **A-1.1**: `uv run pytest -q tests/unit/entrypoints/test_tui_theme.py` passes: data colours are blue/orange/amber in Neon and Control room, night darkens up/down and keeps warn, breakpoints are 100/160/220 and 30/45.

### R-2: chrome, tabs and keys

- **A-2.1**: `uv run pytest -q tests/unit/entrypoints/test_tui_cockpit.py tests/unit/entrypoints/test_tui_widgets.py` passes: no key bound twice, `1`-`8` switch tabs, Tab / Shift+Tab cycle, D W M Y set the range, compact tabs show numbers only, every home card sits in a `HudPanel`.

### R-3: live dot, title and motion

- **A-3.1**: the OK dot is drawn in the live colour and not dim; late is amber; down is red.
- **A-3.2**: the terminal title reads `hermes ● live · €1,021 · next 18:00` on the OK fixture and is written as OSC 2. The total (whole euros) appears only where `pnl_visible` allows money for the surface and never in the down title; without it the title is `hermes ● live · next 18:00`.
- **A-3.4**: the window bar shows in Neon only; Control room and night hide it.
- **A-3.5**: the DEMO DATA tag is drawn on the `warn` amber.
- **A-3.3**: with motion off no effect timer exists and the strip never advances.

### R-4: theme switch

- **A-4.1**: T opens the picker; choosing saves `theme = "<choice>"` to the settings path (tests use `tmp_path`).
- **A-4.2**: Auto shows `hermes-night` at 23:30 Madrid and `hermes-neon` at 14:12; a hand-picked Neon stays Neon at night.
- **A-4.3**: `hermes tui --theme control` saves the choice; an unknown name exits 2.

### R-5: snapshots

- **A-5.1**: SVG snapshots at 80x24, 120x36, 160x45 and 240x60 with motion off and a frozen clock.

## Design

```text
src/hermes/entrypoints/
  tui_theme.py     Tokens BASE / NEON / NIGHT, THEMES, breakpoints, MOTION, glyphs, WORDMARK
  tui_settings.py  ThemeChoice, ConsoleSettings, load_settings, save_settings
  tui_widgets.py   HudPanel, WindowBar, TabBar, ThemeChip, ThemePicker, key_chips
  tui.py           HomeApp (chrome, tabs, timers, theme, title), run_home
  commands/tui.py  --theme
```

Screen layout, top to bottom: window bar, tab bar, status row (demo tag,
release, heartbeat, countdown, theme chip), down row, `ContentSwitcher`
(`tab-1` home grid of `HudPanel`s, `tab-2`..`tab-8` placeholders; Briefs uses
the AI variant), 90-day line, footer chips + status.

Timers: poll every `MOTION.poll` (5 s); clock every `MOTION.clock_tick` (1 s:
clock, countdown, Auto night switch, terminal title); effects only with
motion on: pulse every `pulse_period / pulse_steps` (slower at night), strip
every `strip_tick` (not at night).

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the PR.

Test tiers touched: unit, property

## Risks

- **Snapshot churn**: every console SVG changes. Mitigation: snapshots are taken with a frozen clock and motion off, so they change only when the design does.
- **Theme picked in the palette bypasses saving**: Textual's own theme command is removed from the palette; only the three hermes choices are offered, and they save.
- **OSC 2 on terminals that ignore it**: harmless; nothing is written when the title has not changed.

## Dependencies on other changes

- None. Follow-ups: issues #712-#722, #729-#733.
