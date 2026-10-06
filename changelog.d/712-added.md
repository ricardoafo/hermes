- **Cockpit console, step 2: the home screen at full width**
  (`src/hermes/entrypoints/tui.py`, `tui_home.py`, `tui_charts.py`,
  `tui_widgets.py`): the `hermes tui` home screen now lays itself out per
  terminal size: one column under 100 columns (charts become sparklines), two
  from 100, the mock layout from 160, and a third column from 220 with a
  Signals placeholder and a live log tail. Under 30 rows the curve hides; from
  45 rows charts are twice as tall. The portfolio total is a big number in
  bold, high-contrast digits (at least 7:1 in every theme, never dimmed at
  night) over a gradient underline; it counts up when it changes. Below it are
  a braille curve, cash /
  invested meters and an exposure meter against the roster's cap (amber near
  it). Positions show a sparkline, change, net and a stop-room meter that turns
  amber when the stop is close, or a radar with "hermes is watching … and will
  look again at HH:MM". Market, Activity (with icons), an Engine brief slot and
  the 90-day run bar complete the screen. Read-only; no new dependency.
  Closes #712.
