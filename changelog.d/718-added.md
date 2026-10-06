- **Cockpit console, screen 5: Shadow test** (`src/hermes/entrypoints/tui.py`,
  `tui_shadow.py`, `src/hermes/application/console/shadow_screen.py`,
  `src/hermes/adapters/console/shadow_records.py`): tab 5 of `hermes tui`
  shows a big "N / 90" over the shared 90-day run bar with month ticks, the
  five certification checks as pass, fail or pending only ("mechanical: pass
  or fail stands"), the day-0 record with its Ready-rule verdicts, the frozen
  pre-registered settings and the lab notebook entries. Under ADR-021 day 0 is
  the record `hermes shadow-start` will write beside the ledger (`runs/`);
  until it exists the screen says "warm-up · not counting". Which fact starts
  the count is one switch. Read-only, no money shown, no new dependency.
  Closes #718.
