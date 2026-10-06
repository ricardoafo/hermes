- **Cockpit console, step 3: live prices between bars**
  (`src/hermes/entrypoints/tui.py`, `tui_live.py`, `tui_home.py`,
  `src/hermes/application/console/live.py`, `src/hermes/ports/live_prices.py`):
  `hermes tui` now reads Kraken's public ticker for BTC, ETH and SOL every
  10 s in a background worker, so the screen never blocks. The Market panel
  shows each coin's live price, its change over the last 15 minutes and a
  braille sparkline. Each open position's now price, change, net, stop-room
  meter and trend, and the big total and today's change, follow the live
  prices under a pulsing `live` tag, beside the booked values ("booked as of
  16:00"); the waterfall, tax and Shadow test stay booked-only. A tick flashes
  the cell blue (up) or orange (down), off with `HERMES_CONSOLE_MOTION=off`. A
  failed fetch keeps the last price, dimmed, "as of HH:MM:SS"; after 60 s
  without a price the live values dim and the tag reads "live · paused".
  Live prices stay in the console process: they are never written to the
  ledger and never reach the engine, the supervisor or a notification
  (import-linter contract `live-prices-are-console-only`). Demo data gets no
  live prices. The empty-ledger heartbeat now names the next 4h check instead
  of "due". Every Portfolio chart now has a dim caption saying what it
  covers ("this week · 18 checks since 3 Oct · flat", or start → end and the
  high), and the braille curve has a time axis (hours, weekdays, days or
  months). The Neon window bar's decorative binary strip is replaced by a
  live-feed pulse (one dot per recent poll, hollow for a failed one), or the
  time to the next 4h check when there is no ticker, and the whole window
  now takes the theme background. No new dependency. Closes #713.
