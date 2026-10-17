- **Console drill-down** (`src/hermes/entrypoints/tui_drilldown.py`): one shared
  way to open a detail in `hermes tui`. A `DetailPane` shows it beside the list;
  a `DetailScreen` shows it full screen under a breadcrumb
  (`◂ Home › Positions › ETH`). Esc or Backspace goes back with the list's
  filters, sort and cursor kept, `[` and `]` show the previous and next item
  (stopping at the ends), and number keys and Ctrl+P still work. Below 100
  columns a detail pane opens as a screen. `TruncatedText` cuts long text with
  "…" (and a "+N" count for lists) and shows it whole on Enter. The Home
  position detail now uses them, with the same content. Closes #730.
