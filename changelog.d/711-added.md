- **Cockpit console, step 1** (`src/hermes/entrypoints/tui.py`,
  `tui_theme.py`, `tui_widgets.py`, `tui_settings.py`): `hermes tui` gets the
  Cockpit look from the design spec (`docs/design/console-cockpit.md`). All
  colours, gradients, breakpoints, glyphs and timings live in one token module,
  with three themes built from them: Neon (default), Control room, and a dimmed
  night theme that Auto shows from 23:00 to 08:00. Switch with `T`, the theme
  chip, the command palette or `hermes tui --theme neon|control|auto`; the
  choice is saved in `~/.config/hermes/console.toml`. Every panel sits in a
  `HudPanel` frame labelled "computed" or "✦ AI supervisor"; Neon adds a window
  bar with a ticking binary strip and a 1 s clock. A tab bar (`1`-`8`, Tab /
  Shift+Tab, Ctrl+P), footer key chips, a next-bar countdown and the terminal
  tab title (with the portfolio total where money may show) show the state.
  The live dot is now cyan and pulses (it was all dim, "live is gray").
  `HERMES_CONSOLE_MOTION=off` stops every animation. Closes #711.
