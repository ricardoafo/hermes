# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)
as defined in [ADR-006](docs/architecture/adr-006-versioning-strategy.md).

Every PR that changes production behavior must update the `[Unreleased]`
section under Added / Changed / Deprecated / Removed / Fixed / Security.
PRs that are pure docs, chore, or CI may skip. The CHANGELOG update check
lands in Phase 0 CI (per ADR-006).

## [Unreleased]

## [0.2.0] - 2026-10-18

### Added

- **`hermes reconcile` reads the live Binance spot balances** when `allow_mainnet` is set, with a dedicated read-only key from `HERMES_BINANCE_READ__API_KEY` and `HERMES_BINANCE_READ__API_SECRET`, and compares them with the trade book for the current month. The adapter sends only the signed `GET /api/v3/account`; a missing key or an unreadable exchange exits 3 instead of falling back to the ledger-only check. Refs #29.

- **Anomalies are recorded in the ledger** and listed with `hermes anomalies [--since 7d] [--json]`, read-only. Serve records every `AnomalyDetected` published on its bus. `archive-check`, `reconcile --notify` and `parity check --alert` record each alert before sending it, and the alert itself is unchanged. Record-only: nothing pauses trading on an anomaly; that decision is still open. Refs #136.

- **Console new-version notice** (`src/hermes/entrypoints/tui.py`): an open `hermes tui` compares the release it started on with the current one (the link `hermes status` reads) on its 5 s refresh and shows "new version deployed · press R to restart" as an amber `warn` row in the theme tokens, under the status row on every tab; R exits and re-execs the console with the same arguments (on Windows it exits with a message). Part of #652; the release-prune guard is issue #956.

- **Live activity feed** shows restarts (with the release), deploys, warnings and errors read from the journal, and one quiet `checked N coins · no signal` line per 4h check (`--all`, or `A` in the TUI); read-only, omitted when the journal is unreadable. Closes #653.

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

- Console: the Signals screen (tab 2) shows the week's bar checks per coin as a heat grid of how many entry rules held, the last check's rules as yes or no, the week's counts and the frozen rules. Rules show as yes or no only, never a price or how close a rule came (#714).

- **Console System screen** (tab 7 of `hermes tui`, view only): Services shows
  hermes, the bot, health, daily, backup, archive check and tax pack with their
  systemd state and next timer run; Releases lists the live release and the
  earlier ones, and Enter opens a release with its deploy steps and rollback
  target under `◂ System › Releases › <sha>`; Data and timing shows market
  data, order book and EUR rate freshness and a bar-check time sparkline;
  Machine shows cpu, memory and disk meters, the ledger size and the last
  ledger snapshot. Bootstrap, restart, rollback and a read-only diagnosis
  (`hermes status`) are shown as commands to copy: nothing runs from the
  screen. Over `--vps` it reads the VPS; elsewhere each panel says "not
  available here". Closes #717.

- **Cockpit console, screen 5: Shadow test** (`src/hermes/entrypoints/tui.py`,
  `tui_shadow.py`, `src/hermes/application/console/shadow_screen.py`,
  `src/hermes/adapters/console/shadow_records.py`): tab 5 of `hermes tui`
  shows a big "N / 90" over the shared 90-day run bar with month ticks, the
  five certification checks as pass, fail or pending only ("mechanical: pass
  or fail stands"), the day-0 record with its Ready-rule verdicts, the frozen
  pre-registered settings and the lab notebook entries. Under ADR-021 day 0 is
  the record `hermes shadow-start` will write beside the ledger (`runs/`);
  until it exists the screen says "warm-up · not counting". Which fact starts
  the count is one switch, and the 90-day line at the bottom of every tab
  counts from the same day 0. Read-only, no money shown, no new dependency.
  Closes #718.

- **Tax screen** (`hermes tui`, tab 4, view only): Summary (realised gains, fees, taxable base, estimated tax, open if sold now, two-month-rule flags for BTC, ETH and SOL only, savings bracket meters with "€X left before 21%"), Lots (every FIFO lot in the shared query list, month subtotals and the highlighted lot's sales), Brackets, Tax pack ("for your gestor, never submitted to AEAT") and Your profile (masked, `S` shows; over `--vps` it says only whether a profile exists and never reads it). `[` and `]` switch the sub-tabs. Every figure comes from the tax estimate (#719).

- Console: the Briefs screen (tab 6) shows the supervisor desk's daily brief, as `hermes supervisor brief` publishes it, beside the list of briefs. It never shows profit, equity or prices (issue #720).

- Console: a toast says when a new bar has been checked and when the next one is due, and `HERMES_CONSOLE_BEEP=on` rings the terminal bell once whenever hermes turns late or down (#721).

- **One query, filter and sort bar for console lists** (`application/console/query.py`, `entrypoints/tui_query.py`): a pure parser and matcher for `field:value`, `field>=value`, free text and `since:7d`, with stable multi-key sort, facet counts and Tab completion of field names, plus the `QueryBar`, `FacetFilter`, `SortableTable` and `QueryList` widgets, shown first on a demo activity list inside a `HudPanel`, styled only through the console theme tokens, with the activity rows now covering restarts, deploys, warnings, errors and folded check lines. Closes #729.

- **Console drill-down** (`src/hermes/entrypoints/tui_drilldown.py`): one shared
  way to open a detail in `hermes tui`. A `DetailPane` shows it beside the list;
  a `DetailScreen` shows it full screen under a breadcrumb
  (`◂ Home › Positions › ETH`). Esc or Backspace goes back with the list's
  filters, sort and cursor kept, `[` and `]` show the previous and next item
  (stopping at the ends), and number keys and Ctrl+P still work. Below 100
  columns a detail pane opens as a screen. `TruncatedText` cuts long text with
  "…" (and a "+N" count for lists) and shows it whole on Enter. The Home
  position detail now uses them, with the same content. Closes #730.

- **Console charts library**: `textual-plotext` (pinned `>=1.0.1,<2`, MIT) and a reusable `PriceChart` widget in `hermes.entrypoints.tui_charts` that plots a series of Decimal closes. The Code Owner approved the library on 2026-10-05 in the console design's "needs your OK" list. Not wired into a screen yet (issues #712 and #715 do that). Closes #732.

- **Winged H logo widget** (`src/hermes/entrypoints/tui_logo.py`): adds the `textual-image` dependency (`>=0.14.1,<0.15`) and a reusable `Logo` widget that shows the Winged H picture where the terminal can draw images and a block-letter HERMES wordmark everywhere else (including ssh and tests), drawn in the Neon brand gradient or plain Control room cyan from the console theme tokens. The Code Owner approved the library on 2026-10-05 in the console design's "needs your OK" list and chose the Winged H logo the same day. Not shown at start-up yet (issue #721). Closes #733.

- `hermes serve` records every risk-overlay halt and §10 kill of an entry in the ledger, alerts it on Telegram and Slack, and `hermes status` shows the last 24 hours and the newest one (#749).

- `hermes serve` runs the pre-registration §10 shadow kills (SA-9 rule 3+ times in 30 days, shadow Sharpe streak) and the sim-vs-live drift rule once per UTC day; a kill stops new entries for good, a drift breach is recorded for the return to UNTRUSTED, and both are alerted and shown in `hermes status` (#796).

- **`hermes shadow-start`**: the Code Owner's day-0 command for a counted shadow run (ADR-021 §2, §3). It checks the Ready rules a program can check (signed pre-registration hash in the lab notebook, backtest gate recorded for the running parameters, one enabled strategy) and lists rules 3, 4 and 6 for the Code Owner to confirm. With `--confirm START` and `hermes.service` stopped, it copies the warm-up ledger read-only into the archive, verifies the copy, removes the ledger so serve starts fresh, writes the day-0 record (engine version and release, config, pre-registration and data manifest hashes) and prints the lab notebook entry. Without it nothing changes (#797).

- Engine PRs now replay the strategy over Train on the base and on the head, and CI fails when a signal or fill decision changed unless the PR cites the Code Owner's OK; decisions only, no trial logged (ADR-025, issue #798).

- **Version tags on deploy** (`.github/workflows/cd.yml`, `scripts/deploy/version_release.py`, `src/hermes/release_version.py`): the first good deploy of a cut version also creates the `v<version>` tag (for example `v1.0.0-shadow.1`) on the commit that first shipped that version, with a GitHub pre-release whose body is that version's `CHANGELOG.md` section. Later deploys of the same version leave it as is, the `deploy-<sha12>` releases stay the latest ones, and a tagging failure is a warning, never a failed deploy (ADR-022 §3).

- CD can deploy hermes-01 only when a new version lands: setting the `DEPLOY_ON` repository variable to `version` at day 0 makes every other green `main` build and check without deploying (ADR-022 §6).

- `hermes status` shows the running version as its release tag (`version: v1.0.0-shadow.1`, also in `--json`), and the Telegram deploy message leads with `hermes v<version> (<sha12>)` (ADR-022 §4).

- **Portfolio backtest on one capital**: `scripts/backtest/run_portfolio_backtest.py` replays BTC, ETH and SOL through one fill kernel and one portfolio seeded with the roster's 5,000 USDT, so the §5.4 sizing, the 3-position limit, the 15% exposure cap and the RO-01 correlation lock bind across pairs. Each run appends one trial and writes the `hermes certify --performance` file (daily equity, round trips, trial statistics, bootstrap seed). RO-01 now follows the signed §5.5 text: an entry is blocked only when two open pairs and the candidate all correlate above 0.80 over 30 daily returns (#822).

- **The console's top bar shows the mainnet state, the release version and a countdown meter.** The Neon window bar (or, in Control room, the status row) reads `SHADOW · mainnet off` from the same `allow_mainnet` config `hermes serve` loads; `LIVE · mainnet ON` shows reversed in the down colour, and an amber `mainnet ?` when a live console cannot read its config; demo data always reads mainnet off. The status row names the package in its release form next to the running release (`v1.0.0-shadow.1 · 679b676`), and the next-bar countdown gains a meter of the share of the bar that has passed; at compact width the meter drops and the text stays. Refs #833, #815.

- **Serve risk inputs (record only)**: serve now samples each pair's depth book every 5 minutes and records the spread with its rolling 1-hour median (RO-02 input), and on each completed 1D bar records the 30-day correlation of every two pairs' daily returns (RO-01 input, pre-registration §5.5) in two new ledger tables. No rule reads them yet: RO-01 and RO-02 stay unwired, and a failed sample is logged and skipped (#835).

- New `hermes trials repair` and `hermes journal repair` drop a torn last line that a power loss or kill left in the trial log or the decision journal. They need `--confirm REPAIR`, record the drop as a chain line, and refuse a file whose chain breaks before its last line (issue #891).

- The Briefs screen lists the supervisor desk's earlier daily briefs, newest first: `hermes supervisor brief --keep` keeps each brief exactly as it passed the output gate in `briefs.jsonl` beside the ledger, once per day, and the new `hermes-brief.timer` runs it at 07:15 Madrid.

- **Backtest funnel (ADR-021 §5)**: `hermes funnel <strategy_id> --pre-registration FILE --evidence FILE` checks the eight funnel criteria that bind strategies from v2 on (CSCV probability of backtest overfitting, t-statistic, trade count, profit at stressed costs, positive years, bull and bear stretches, Validation against Train, neighbouring parameters, correlation with running strategies) and prints one `PASS`/`FAIL` line per check in the `hermes certify` format. Every threshold comes from a `backtest_funnel:` block in the signed pre-registration's front matter; a missing one refuses the run. Nothing is recorded, and `hermes certify` and v1's gate are unchanged (#957).

- `hermes parity window` replays the shadow window from day 0 and reports M-09 (P&L correlation) and M-10 (Sharpe match) as pass, block or insufficient; `hermes certify --shadow-parity FILE` makes both a condition of `SHADOW_CERTIFIED` (#965).

- A `swing-mvp1-v2` strategy config whose exits all sell 5 bps under the close of the 4h bar they fire on, the stop included, priced the same way in the backtest drivers (`--strategy swing-mvp1-v2`) and in serve, where it is also the drift model price (pre-registration v2 §5.3, §6.2, §7). v1 keeps its stop-market exit prices.

- `hermes certify` reports the shadow round trips the ledger holds toward SHADOW_CERTIFIED and flags the execution check weak under 30, without changing any pass or fail rule (#797).

- **Funnel evidence from the backtest driver**: `scripts/backtest/run_portfolio_backtest.py --funnel-evidence-out PATH` also replays the six pre-registration v2 §8.5 neighbours (Donchian 15/25, ADX 20/30, chandelier multiple 2.5/3.5) and the base parameters at twice the fees and slippage, appends each replay to the trial log as its own trial, and writes the evidence file `hermes funnel` reads (daily equity, trade count, cost stress, the base-and-neighbours trial matrix, neighbour ids, no running strategies). Without the flag the driver runs as before (#997).

- **v2 backtest gate**: `hermes certify swing-mvp1-v2` judges the backtest by the signed pre-registration v2: DSR at or above 0.95 (read from v2 §10), v1's §9 secondary rows and calibration fill (v2 §9 keeps them unchanged), and the ten ADR-021 §5 funnel checks from a new `--funnel-evidence FILE` (v2 §7); without that file the `funnel` row fails. v2 is fingerprinted from its own `strategies.yaml` entry, and gates past `BACKTEST_CERTIFIED` are refused for v2 until v2's shadow rule is implemented. v1's gate, output and records are unchanged (#1008).

- The console opens on a cold start screen: the Winged H, the running release and live checks of the ledger, engine, journal, market feed and AI supervisor, each with what it found or why it failed; a first run says when the first bar check is due. Any key opens Home at once, Q quits, and motion off skips it.

- `hermes risk-inputs` shows the risk inputs shadow records but never acts on (the RO-02 spread against its 1-hour median, the days since the equity high and the deepest drawdown since) with whether each rule would fire, plus the latest recorded RO-01 correlations (#1030).

- Research batches (ADR-026 §1): a batch and hypothesis card template, `hermes batch check FILE`, which checks the candidate count, one candidate per idea family, summaries, sections, free parameters, funnel floors, the selection rule, the signing hash, card hashes, the strategy roster and the bound code, and the backtest driver's `--batch FILE --candidate ID`, which runs a candidate's own neighbour grid and records the batch id on every trial line.

- One backtest gate for any signed strategy (ADR-026): pre-registration v2 and every research batch candidate are judged by the same code, with thresholds from the signed document. `hermes certify <id> --batch FILE` judges a batch candidate, deflating its DSR by every trial in the experiment's log; on a Train-only run `validation_vs_train` shows NA instead of failing, and only a Train + Validation pass is recorded. `hermes batch check` now also fails a batch edited after it was signed.

- Research batches are judged at their end by a signed rule: one trial count for every candidate, a Benjamini-Hochberg step on 1 - DSR across the batch, and the highest-DSR survivor as champion, or none (ADR-026 §4, §5).

- Roster entries can name their signal with `signal:`; serve, parity, the portfolio backtest and decision replay build every strategy through one factory, an entry with no `signal:` keeps today's Donchian breakout unchanged, and an unknown signal is refused at config load (research funnel contract §2a).

### Changed

- Serve now runs the RO-01 correlation lock on the same 30-day correlations of the 1D closes as the backtest, and the daily parity check replays it; RO-02 and the live-phase recovery, single SA-9 and 30-trade Sharpe kills stay off in shadow (#514).

- The console reads the ledger, the journal and the release in a worker thread and paints when the reads arrive, so a slow read no longer holds a screen switch, a key or an animation; each source is read once per poll (issue #1001).

- After downtime, serve records each bar that closed while it was down as missed, leaves entries on those bars unfilled, fills their exits at the price when it is back (never back-dated), and sends one restart report; the parity check skips outage days and the service waits for clock sync (#1078).

- **Release versions are `0.y.z` until a champion trades real money** (ADR-027, `scripts/dev/cut-release.py`): the `1.0.0-shadow.N` line is retired and the next cut is `0.2.0` (the 6 Oct `v1.0.0-shadow.1` cut counts as `0.1.0` and keeps its tag). The cut script refuses a new `-shadow.N` name, refuses `--engine-change yes` on a PATCH bump so a PATCH release never restarts a counted shadow, and refuses the first `1.x` cut unless `--real-capital` is passed. Old wheels, tags and changelog sections are still named `v1.0.0-shadow.N`.

### Fixed

- **Shadow loop and backtest**: an exit that is not filled (no book on a zero-volume bar while the depth endpoint is down, the fill model, a kill halt) now leaves the strategy in the position it still holds, with its original entry time and price, so its exit rules run again on the next bar and the coin is sold when an exit fills, also after a restart; the parity check mirrors this (#706).

- `hermes serve` no longer sends the ENTRY push for an entry that did not fill (a risk halt, a declined size); the halt alert or the log says why (#708).

- **The license check now checks something.** It read zero packages locally and in CI because pip-licenses listed its own tool environment; it now reads the project interpreter, fails when it counts zero packages, understands SPDX `OR` / `AND` expressions, and records the shipped LGPL libraries (fpdf2, python-telegram-bot, textual-image) and dev-only yattag in `known_licenses.yaml`. Closes #757.

- **Sentry events with extra context are no longer dropped.** The capture uses `sentry_sdk.new_scope()` instead of the deprecated `push_scope()`, whose warning the test settings turn into an error that dropped the event. Closes #758.

- Closed the connections and pipes that `hermes migrate --dry-run` (when the ledger cannot be copied) and the journal reader (`hermes logs`, console activity feed) left open until garbage collection.

- `hermes preview` now shows the exit message with a reason the strategy really sends (`chandelier`) instead of `trailing_stop`, which it never sends (issue #765).

- The swing strategy now moves its chandelier stop once per daily close and never from the 4h bar it is tested on, and a stop exit on a bar that opens below the stop fills at the open less slippage, in both the backtest and serve (pre-registration §5.3, #785).

- **Lab-notebook hashes can be checked from the file.** New entries are written with the blank lines markdownlint needs, so they hash exactly as they appear, and `LabNotebook.verify_file()` checks every entry, including the older ones whose blank lines were added by hand. Closes #786.

- **A config file that isn't UTF-8 gets a clear config error** naming the file and the bad byte, instead of a raw decode traceback. Closes #807.

- serve now restores the peak equity from the latest portfolio mark on restart, so the RO-08 drawdown no longer reads low until bars are marked again (integrity rules 4.1; no risk-rule or threshold change).

- Market data, exchange and FX parsers now refuse NaN and infinite numbers and out-of-range timestamps with their own error, so a broken row fails the feed cleanly instead of escaping as a raw decimal or overflow error (issue #841).

- **Every wire parser now fails with its own error on malformed input**: Binance depth stream and snapshot, Binance historical and archive klines, Kraken OHLC and fees, Telegram replies, config files, the trial log, the decision journal and every ledger reader refuse wrong types, non-objects, invalid UTF-8, too-deep nesting, NaN and naive times with the adapter's typed error instead of a raw exception; historical and Kraken candles off the interval grid or priced at or below zero are refused. Closes #842.

- **A broken forming candle no longer costs a trade.** The live candle feed now drops the still-forming candle before reading its prices, so a bad value in it can't fail the poll that delivers the bar that just closed. Before, serve exited on that poll and the breakout entry was missed after the restart. Closes #850.

- A 4h candle the exchange never publishes, or downtime longer than the warm-up window, no longer puts serve in a crash loop: the first exit is kept so a late bar is picked up on restart, and if the bar is still missing the restart reports it to Sentry and Telegram and carries on, so an open position keeps its exit watched (issue #852).

- A ledger write that fails on any error, not only a SQLite one, now rolls back, so the next successful write can no longer commit its partial rows (issue #853). The migration runner does the same for a failing migration.

- A trade is no longer made without its Telegram push: serve records each push in the ledger and sends it again after a crash between the signal row and the push, and a notifier error of any kind (a missing template as much as an outage) is logged instead of stopping serve, with the failed channel pushed again on the next bar while the signal is recent. A crash right after a push and before its record sends that push a second time; at least once is the rule for trades (issue #854).

- Serve no longer exits 1 when a reader (console, bot, tax pack, `hermes status`) holds the ledger for a few seconds: every ledger connection now waits up to 10 s for a lock. A second `hermes serve` on a ledger already in use refuses to start with exit code 4 and names the ledger, and trial-log and decision-journal appends take a file lock so two appenders cannot fork the hash chain. The ledger still runs in the rollback journal, not WAL: the read-only units cannot open a WAL ledger yet (issue #855).

- Serve now refuses to start on a corrupt ledger file or on a ledger a newer release has migrated, with a clear startup error instead of a raw SQLite error or a silent write; hermes.service sets HERMES_ENV=prod when the env file omits it; and the health ping reports a broken `hermes status` call (exit 2 on a release that has the command) as a failure instead of green (issue #856).

- The RO-03 slippage halt now works as intended in serve (issue #857). A fill's quality row is saved in the same transaction as its trade, so a crash can no longer drop the slippage RO-03 reloads on boot. A fill's slippage now counts for 24 hours: a single fill over 0.5% halts entries for a day instead of until the next fill, which could never come while flat. The 2% cumulative limit now sums the fills of the last 24 hours instead of always reading 0.

- **Live order adapters no longer lose track of a real order.** Binance orders carry a client order id, and a 5xx or timeout on submission is looked up instead of reported as a refusal: an order Binance never saw is a retry-safe error, a placed one returns its fill, and an unresolved one names the id to reconcile. A Binance commission paid in BNB is priced at its ticker, or at the pre-registered 10 bps when the ticker is down, so a filled order always returns its fill. Kraken orders carry a `userref`, errors after placement carry the txid, and an optional nonce file keeps nonces increasing across a restart with the clock stepped back (#858).

- **FX answers are checked against the question.** The Frankfurter adapter refuses a response quoted from a different base currency, which would read the rate in the wrong direction, and a rate dated after the requested day. Part of #859.

- After a restart, serve marks every open position at the last closed bar before it handles new bars, so the exposure cap, equity and drawdown no longer read entry prices until each pair's next bar (issue #863).

- An entry is now refused when the price actually paid at the ask would push total exposure past the 15% cap (pre-registration §5.5). Before, the cap was checked only at the signal price, and a spread could end exposure slightly over it. Sizing is unchanged (issue #864).

- A migration snapshot taken in the same second as an earlier one gets its own name instead of overwriting it, so every rollback point stays restorable (issue #865).

- Lot selection never draws a lot opened after the sale, and the order-book walk fills an exit of a quantity with more than 28 significant digits in full instead of refusing it (issue #866).

- `hermes serve` now refuses to start on a ledger path that does not exist unless it is given `--new-ledger`, so a wrong `--db` or an unmounted disk can no longer start a fresh, flat ledger and drop the open positions. It also refuses to start when `capital_budget` differs from the capital seed the ledger recorded, naming both amounts. `hermes shadow-start` now leaves an empty ledger for day 0 instead of none (issue #876).

- The ledger now runs in WAL, as ADR-003 says: a long read (tax pack, bot, console) no longer holds up serve's commit, and the read-only units still open the ledger while serve is stopped (issues #855, #879).

- The daily warm-up floor is now 300 bars everywhere (`warmup_bars_1d` and the `hermes parity check` minimum), up from 200: the EMA(200) regime filter is seeded from the oldest buffered daily closes, so a 200-bar warm-up could decide differently from serve (#880).

- Serve now watches a coin it holds without an entry signal (an old or manual trade, or lost signal rows): it restores the position from the coin's own fills, logs `serve.position.no_entry_signal`, keeps its exit rules running and never buys it a second time (issue #902).

- `hermes certify` and `hermes shadow-start` now read the signed pre-registration from beside `--config-dir`, as serve does, so they work from an installed release on the VPS (issue #923).

- The Binance Vision ingest no longer downloads any month on or after the sealed OOS start (2025-09-01), whatever its arguments, and the new `scripts/data/prepare_backtest_data.py --split train|validation` downloads only the missing klines a backtest needs and exits non-zero unless every pinned dataset matches the data manifest (issue #925).

- Backtests now load 300 days of daily bars before their window, as serve warms up with, so the EMA regime filter's first decisions match serve's (issue #880).

- Telegram pushes that hit the Bot API rate limit (HTTP 429) are retried after the wait Telegram asks for, at most three times and within 30 seconds, instead of being dropped on the first 429 (issue #859).

- The console's 1 s clock tick, its redraw and its effects (pulse, feed pulse, radar, shimmer) no longer crash when a widget is torn down while the app closes; a widget that is gone is skipped and the tab title is still written.

- `hermes status` and the daily brief no longer list an entry the engine refused or halted as an open position; only entries with a fill count (#962).

- `hermes shadow-start` proves it can write the ledger, archive and run-record directories before changing anything, so a read-only directory no longer leaves an archive and a day-0 record beside the old ledger (#797).

- The live depth snapshot now keeps Binance's `lastUpdateId`, and the diff stream drops diffs the snapshot already holds and refuses a first diff that skips past it, so an old diff can no longer reach a local book (#866).

- `hermes funnel` now checks `--expect-sha256` before it reads the evidence, so a wrong pre-registration hash always refuses (exit 1) even when the evidence file is bad, and it refuses a pre-registration whose bull or bear stretches overlap, because the shared days would compound twice (issue #987).

- The console switches screens and animates without stalling on a long ledger: the market watch finds each stream's newest bar check in one pass instead of one lookup per check (about 2 s down to about 15 ms at 60 days of history).

- `hermes tui` draws in 24-bit colour by default, so over ssh (where the terminal's colour setting is not forwarded) the console keeps its violet background and panels instead of falling back to 256 colours that turned them black and navy; set `TEXTUAL_COLOR_SYSTEM` to choose another depth.

- The Home Market panel shows each coin once when it is too narrow for the timeframe column, with that coin's soonest next check, instead of one identical-looking row per timeframe.

- **Frozen depth books and an off clock no longer pass quietly.** A Binance depth book whose `lastUpdateId` has not moved keeps the stamp of the response that first carried it, so RO-04 halts entries on a frozen upstream (a kill-rule change Rick approved on 17 Oct 2026). Serve records and alerts its clock leaving Binance time by more than the settle delay as a P0 anomaly, and `hermes status` shows "The server clock is off" in red until the clock is back. Closes #859.

- A klines reply that gives one bar two different rows is now refused as a bad reply instead of silently keeping the last row; exact repeats are still dropped.

- The end-to-end serve cycle test no longer expects a push for an entry the exposure cap refused, matching the serve change from issue #708 (#1038).

- The console's night theme draws labels, hints, gains, losses and AI marks at 4.5:1 contrast or more on its panels, so they no longer fade into the background after 23:00 (#1046).

- Console: every panel's key hint now starts in the body text's column, and hints, tab numbers, the theme chip and AI text read at 4.5:1 or more on every surface in Neon, Control room and night.

### Security

- Binance and Kraken trading adapters now need a `MainnetPermit` to reach a real account. Only `issue_mainnet_permit` makes one, in the prod tier with `HERMES_ALLOW_MAINNET` set and a LIVE_APPROVED record with unchanged parameters; with a permit, Binance accepts only its documented spot hosts (issue #781).

- The nightly backup sends its healthchecks.io ping URL to curl on stdin instead of in argv, where `ps` showed the secret to every local user on hermes-01.

- **Bot and preview hardening.** `/logs` on Telegram no longer sends the text of an unstructured journal line (a traceback can quote an upstream response), the bot skips a malformed Telegram update instead of restart-looping, and `hermes preview` writes attachments into a fresh private folder instead of a predictable `/tmp` path. Part of #783.

- Lot, journal and correlation IDs are now generated by `python-ulid`, which is maintained; it replaces `ulid-py`, unreleased since 2020. Stored IDs are unchanged, and IDs minted in the same millisecond now sort in creation order (issue #784).

- CD now copies and unpacks each release in `/var/lib/hermes-deploy`, a 0700 directory owned by the deploy account, instead of world-writable `/var/tmp`, and the non-root deploy sudoers rule points there; the VPS bootstrap upload moves from `/var/tmp` to `/var/lib/hermes-bootstrap` for the same reason (issue #782).

- With `HERMES_ENV=prod`, hermes no longer reads `.env.local` and refuses a relative `--config-dir`, so the working directory cannot choose a production config. `hermes preview --send` now runs only with `HERMES_ENV=dev`; an unset tier is refused (issue #783).

- `hermes certify --sign-off NAME` now asks for the `LIVE_APPROVED` phrase on an interactive terminal and refuses when stdin is not one; the `--confirm` flag is gone, so a script can no longer approve live trading unattended (issue #949).

- The tool freshness check reads registries through an HTTPS-only opener and refuses any other URL, so it cannot open `file://` or `ftp://` URLs; this clears the daily Semgrep finding (issue #1000).

## [1.0.0-shadow.1] - 2026-10-06

### Added

- **Slack notifier adapter** (`src/hermes/adapters/notifier/slack_adapter.py`):
  concrete `NotifierPort` implementation for the rich/reports channel per
  the 2026-08-23 decisions-journal entry. `notify_urgent` posts a short
  text message with the ADR-008 correlation-tail suffix; `notify_report`
  renders title + body as Block Kit (`header` + `context` + `section`
  blocks) and uploads attachments via Slack's v2 external-upload flow
  (`files.getUploadURLExternal` → signed PUT → `files.completeUploadExternal`,
  the only path since Nov 2025 sunset of legacy `files.upload`). An
  extension method `post_message` accepts explicit `blocks=` and
  `channel=` for callers that need full Block Kit control without
  cramming through the fixed report shape. Wire: raw `httpx.Client`
  (no `slack_sdk`) matching the Telegram adapter's rationale. Auth:
  `Authorization: Bearer` header sourced from
  `HERMES_SLACK__BOT_TOKEN` + `HERMES_SLACK__DEFAULT_CHANNEL` env vars
  via `SlackConfig.from_env` — never a YAML file (AGENTS.md prohibition
  #6). Errors: `SlackNotifierError` extends `InfraError` and wraps
  transport failures, non-2xx HTTP responses (401/429), non-JSON bodies,
  and the Slack HTTP-200-with-`ok=false` footgun (explicitly test-covered
  since a naive `raise_for_status()` would swallow those). 429 raises
  to surface rate-limit pressure to the caller; adaptive back-off stays
  a router-layer follow-up. Tests: respx-based contract tests cover
  happy path, `ok=false` body, 401 auth, 429 rate-limit, network error,
  non-JSON response, empty-attachment short-circuit, full 3-step upload
  flow, malformed step-1 response, step-2 PUT error status, step-2 PUT
  transport error, step-1 transport error, extension surface with
  explicit blocks + channel override, port conformance, and factory +
  close lifecycle. 100% branch coverage on the new package.
  Closes #101.
- **Notification router** (`src/hermes/application/notification_router.py`):
  pure function `route(EventClass, RouterConfig) -> tuple[NotifierPort, ...]`
  that encodes the 2026-08-23 decisions-journal Rationale table verbatim —
  Telegram-only for `SERVICE_CRASH`/`HEALTH_CHECK_FAIL`/`DEPLOY_STATUS`,
  Slack-only for `WEEKLY_REBALANCE_REPORT`/`MONTHLY_TAX_REPORT`, fan-out to
  both channels for `ROLLBACK_FIRED`/`CVE_DETECTED`, and log-only (empty
  tuple) for `TRADE_FIRED_SINGLE`. `EventClass` enum lives alongside the
  router in the application layer (routing is an application concern, not
  a domain one). `RouterConfig` dataclass carries `telegram` + `slack`
  handles as `NotifierPort | None` — a wanted-but-unwired channel drops
  silently from the returned tuple rather than raising, so partial rigs
  (Slack-only tests, early-boot before Telegram creds load) work without
  branching in the router. Fan-out order is deterministic (Telegram
  precedes Slack); dispatch is the caller's job (typically an EventBus
  subscriber wired in `entrypoints/`). Property tests (hypothesis) prove
  purity + full enum coverage; parametrized examples pin the ADR table
  row-by-row. Router file 100% coverage. Domain-purity preserved — router
  imports only `hermes.ports.notifier`, never adapter classes (enforced by
  the `application-imports-only-domain-and-ports` import-linter contract).
  Closes #100.
- **Telegram notifier adapter** (`src/hermes/adapters/notifier/telegram_adapter.py`):
  concrete `NotifierPort` for the urgent/terse channel per the 2026-08-23
  decisions-journal entry. `notify_urgent` posts to the Telegram Bot API
  `sendMessage` endpoint via `httpx.Client` (no `python-telegram-bot` SDK —
  we skip the extra event loop and stay aligned with the anyio-on-asyncio
  policy). Message format is MarkdownV2 with every reserved character
  backslash-escaped so dotted event names and correlation tails render
  literally; correlation tail = last 6 chars per ADR-008. `notify_report`
  is a documented no-op with a WARNING log line — long-form content
  belongs on Slack, not Telegram. New `TelegramNotifierError` extends
  `InfraError` and wraps transport failures, non-2xx HTTP responses
  (401 / 403 / 429), and `ok=false` API bodies; nothing is swallowed
  silently. Credentials come from `HERMES_TELEGRAM__BOT_TOKEN` and
  `HERMES_TELEGRAM__CHAT_ID` env vars via `TelegramConfig.from_env` —
  never a YAML file, per AGENTS.md prohibition #6. Contract tests via
  `respx` cover happy path, MarkdownV2 escaping, correlation-tail
  extraction, 429 rate-limit, 401/403 auth failure, network error,
  `ok=false` response, and non-JSON payload; 100% coverage on the new
  package. Follow-up: adaptive back-off on 429 (deferred until router
  layer lands). Closes #102.
- **Recharacterization tax guard** (`src/hermes/application/tax_guard/`):
  pure-function ADR-002 §Decision 6 monitor over trade activity. Two
  public functions — `compute_window(events, now, window_days)` returns
  a frozen `WindowStats` (trade count, distinct base assets, fees
  bucketed by currency) over the rolling `[now - window_days, now]`
  closed interval; `check_breach(stats, config, now, correlation_id)`
  emits `AnomalyDetected(source="tax_guard.recharacterization",
  severity=CRITICAL)` when any threshold is strictly exceeded, else
  `None`. `evaluate()` composes both. All ADR-002 §6 initial values
  ship in `config/tax_guard.yaml` (`trade_count_90d_max: 500`,
  `distinct_assets_90d_max: 12`, `fee_income_90d_max_eur: "0"`,
  `recharacterization_window_days: 90`) — mirrored as required fields
  on `TaxGuardConfig`. The application layer stays hexagonally pure via
  a `TaxGuardThresholds` `Protocol` (import-linter still forbids
  `hermes.application → hermes.infrastructure`; the infra
  `TaxGuardConfig` structurally satisfies the protocol). Property tests
  (`hypothesis`) pin the "streams crossing thresholds fire; streams
  that don't, don't" invariant across window inclusion, threshold
  strictness, and asset-cardinality axes. The engine-pause subscriber
  hook ADR-002 §6 mandates is DEFERRED to #25 (paper-trading engine —
  that PR owns the pause mechanism); this guard ships the emission
  contract that the engine will subscribe to. Application-layer coverage
  promoted from bootstrap to 100% enforced. Closes #105.

- **Domain events + in-process EventBus** (`src/hermes/domain/events/`):
  four concrete event dataclasses — `TradeExecuted`, `RebalanceTriggered`,
  `AnomalyDetected`, `LotClosed` — all frozen and pinned to
  `__event_schema_version__ = 1` per code-conventions §10. Ships alongside
  `EventBus`, a synchronous in-process pub/sub with exact-type dispatch,
  deterministic registration-order fan-out, and failure isolation via
  `EventDispatchError` aggregating every raising handler in dispatch
  order. Property tests (`hypothesis`) prove the fan-out invariant
  "N subscribers → N sync dispatches in registration order" that ADR-003
  §00 invariant I-06 will build on when the SQLite `events` table lands
  with #24. Coverage 100% on every new file in the events package.
  Closes #99.

- **Process hardening wave 2** (`scripts/dev/create-pr.sh`, `scripts/dev/pr-preflight.sh`,
  `.pre-commit-config.yaml`, `AGENTS.md`): create-pr.sh wrapper enforces PR body lint
  before `gh pr create` (closes drift class 1); pre-flight check #9 now uses
  CI-verbatim pytest invocation + check #14 adds coverage-check.py --bootstrap (closes
  pre-flight ↔ CI parity drift); sdd-design live-verify rule added to AGENTS.md (closes
  design-miss drift class). Closes #121, #122, #123.
- **Typed configuration loader** (`src/hermes/infrastructure/config/`):
  pydantic-settings-style loader for the five hermes YAML files —
  `config/strategies.yaml`, `config/exchanges/binance.yaml`,
  `config/fees.yaml`, `config/tax_guard.yaml`, `config/ml.yaml`.
  Composite `HermesConfig` root with per-file frozen `BaseModel` classes
  (`StrategiesConfig`, `BinanceExchangeConfig`, `FeesConfig`,
  `TaxGuardConfig`, `MLConfig`), all `extra="forbid"` so typos abort at
  process start rather than silently drift. 12-factor env override via
  `HERMES_<SECTION>__<FIELD>` double-underscore nested delimiter (including
  list-index addressing). `ConfigError` extends `InfraError` so operator
  fixes surface early with the offending file path and pydantic validation
  detail. Property tests (hypothesis) pin invariant enforcement on every
  bounded field. New runtime dep `pyyaml>=6,<7`; new dev dep
  `types-pyyaml>=6,<7` (mypy strict clean). Domain purity preserved —
  import-linter `domain-no-pydantic` contract still passes. Closes #103.
- **Code conventions guide** (`docs/guides/code-conventions.md`): 14
  sections codifying design patterns, error handling (hybrid Result in
  domain / exceptions in adapters), immutability, factories, async
  policy (AnyIO on asyncio), performance mindset with measured triggers,
  polyglot posture (Rust yes when profiled, Go no for solo scale),
  testing patterns, time handling (UTC internally / Madrid at edges),
  event schema versioning, feature flags, engine pause, migrations, and
  public/internal API. Research-backed (engram
  `phase-1-code-conventions-research-2026-08-23`). Closes #106.
- **6 domain value objects** in `src/hermes/domain/value_objects/`:
  Money, Price, Weight, Pair, Quantity, LotId — all frozen dataclasses
  with `from_*` factories and `Result`-based validation. Foundation for
  every Phase 1 domain feature. Closes #98.
- **HermesError hierarchy** (`src/hermes/domain/errors.py`): `DomainError`
  and `InfraError` subtrees. Enables the hybrid error-handling pattern.
- **Time helpers** (`src/hermes/infrastructure/time.py`): `now_utc`,
  `to_madrid`, `from_madrid` using `zoneinfo.ZoneInfo` (no pytz, no
  naive datetimes). Enforces §9 UTC-internally convention.
- **DomainEvent base class** (`src/hermes/domain/events/base.py`) with
  `__event_schema_version__` enforcement via `__init_subclass__`.
- **Hypothesis strategy library** in `tests/strategies/` with composable
  strategies for all 6 value objects.

- **New runtime dependencies**: `returns>=0.24,<1` (Result monad, used
  in domain error handling per code-conventions §2) + `anyio>=4,<5`
  (structured concurrency at I/O boundary per code-conventions §5).
- **Observability scaffold** (`src/hermes/infrastructure/logging.py`):
  structlog config with JSONRenderer (prod) / ConsoleRenderer (dev),
  ULID correlation-ID contextvar plumbing, starter field taxonomy in
  module docstring. First real Phase-1-shaping code — supports the
  ADR-008 observability discipline. Closes #18.
- **NotifierPort Protocol** (`src/hermes/ports/notifier.py`): contract
  for outbound notifications with `notify_urgent` + `notify_report`
  methods. Adapters land in Phase 1.
- **Symlink-swap rollback layout** in `deploy/bootstrap-vps.sh`:
  `/opt/hermes/{versions/, current -> versions/vX, previous -> ...}`.
  Enables Phase-1 rollback logic when real services + `hermes engine
  pause` CLI exist.
- **Code conventions enforcement**: 2 import-linter contracts
  (`domain-no-pydantic`, `domain-no-async-or-network` with
  `include_external_packages = true`), 2 pre-commit grep hooks
  (`no-naive-datetime`, `no-domain-mode-check`) with paired self-tests,
  and `tests/unit/test_public_api.py` asserting `__all__` on every
  `hermes.domain.*` and `hermes.ports.*` module. Adds `__all__` to
  `hermes.domain.__init__`, `hermes.ports.__init__`, `hermes.ports.exchange`,
  `hermes.ports.notifier`. Closes #109.
- **AI-authored-test enforcement** (`docs/guides/ai-authored-tests.md`):
  ruff `D103` enforced on `tests/**` (missing docstring on public test fn),
  `[tool.coverage.report] fail_under = 90` global floor, and a new
  `no-vague-test-assertions` pre-commit hook (Python AST walker at
  `scripts/dev/no-vague-test-assertions.py`) that fails a test function whose
  sole assertion is a vague null-guard (`is None`, `> 0`, bare `assert x`).
  Existing tests received 89 invariant-naming docstrings. Contributors: your
  next commit may fail if a new test lacks a docstring or has only a vague
  assertion — see the guide for the standard. Closes #116.
- Merge queue test — PR C (competing edit)
- Architecture Decision Records 001-010 (accepted 2026-08-21)
- Version-control discipline (`docs/guides/dev-workflow.md`) — trunk-based,
  Conventional Commits, squash-merge only, Ricardo-only approval
- AI collaboration protocol (`AGENTS.md`) — role separation, 10 absolute
  prohibitions, precedence order
- Pre-commit hook chain: markdownlint-cli2, conventional-pre-commit
  (--strict), pre-commit-hooks v5.0.0 (trailing-whitespace,
  end-of-file-fixer, check-yaml/json, check-merge-conflict,
  mixed-line-ending=LF)
- GitHub scaffolding: 4 issue templates (bug with severity rubric,
  feature, chore, config), CODEOWNERS assigning @ricardoafo, PR template
  with 7 mandatory sections, `.gitattributes` for LF normalization
- GitHub repo config: 27 labels (`type:*`, `sev-*`, `p*`, `area:*`,
  `size:*`), 4 milestones (Phase 0-3), branch protection on `main`
  (1 review + code-owner + linear history + no force-push + no admin
  bypass), squash-merge-only, auto-delete branches
- Idempotent bootstrap scripts (`scripts/dev/setup-labels.sh`,
  `setup-milestones.sh`, `setup-branch-protection.sh`)
- Doc surface scaffolding: `docs/guides/`, `docs/decisions/`,
  `docs/runbook/`, `docs/research/` with README indexes
- Local dev environment: `.venv/` (Python 3.14.7 via uv 0.12.5),
  pre-commit 4.6.2, markdownlint-cli2 0.23.2

- **Fill quality rows record arrival price and latency** (`FillQualityRecorded` schema v2): each shadow fill now stores the signal bar close (`arrival_price`) and the milliseconds from signal close to fill (`delta_ms`); record-only, v1 rows read back with both as none. Refs #30.

- **Kraken failover drill** (`docs/runbook/failover-to-kraken.md`): a monthly,
  timed rehearsal of the Binance to Kraken cutover from ADR-004. New read-only
  `hermes failover-check` compares the last closed bars of every traded pair on
  both venues; `scripts/deploy/failover_drill.sh` runs it on hermes-01, restarts
  `hermes.service` under `--execute`, checks open positions survive and writes a
  per-step timing report. Closes #31.

- **Kraken spot trading adapter** (`adapters/exchanges/kraken_spot.py`): signed market, IOC limit and exchange-side stop-loss orders, idempotent cancels and free balances behind `ExchangePort` and `StopLossPort`, with the order book and fee schedule from the public Kraken adapter. Kraken has no spot testnet, so the adapter is read-only unless the live-approval gate sets `allow_mainnet`; nothing wires it into `hermes serve`. Closes #32.

- **Read-only supervisor tools** (`hermes supervisor`): outcome-blind health, signals, open positions, decisions due and certification state, each behind its own `hermes:read:*` scope with deny-by-default grants, a memorization guard at 2026-02-01 and an audit line per call; `hermes supervisor mcp` serves them as an MCP server over stdio. No tool can place, cancel or change anything, and no output carries a price, value or P&L. Closes #34 and #470.

- **`hermes reconcile`** (`entrypoints/reconcile.py`): read-only monthly reconciliation. Compares the trade book (trades plus roster capital) with the `TradeExecuted` replay and the lot table per asset, reports the month's realized FIFO EUR P&L and the lots open at its end, and gives a CLEAN / REVIEW / DRIFT verdict with a month-end-cut readiness flag. `--json` for scripts, `--notify` pushes an amount-free Telegram alert when the month is not clean. A `BalanceReadPort` seam is ready for live exchange balances. Closes #35.

- **`hermes tax-filing --year YYYY`** (`src/hermes/domain/filing/`, `src/hermes/application/filing/`): figures for Modelo 100 (gains and losses from virtual-currency transmissions, per coin and per sale, with two-month repurchase flags) and Modelo 721 (31 December holdings, origin keys and the €50,000 / €20,000 rules), as a Markdown report and CSV files. Reads the ledger read-only, never reads the taxpayer profile and submits nothing; every report is labelled an estimate, and shadow figures say no real tax is due (#36).

- CVE-response runbook (`docs/runbook/cve-response.md`) with triage and patch flows, and `scripts/dev/cve-triage.py`, which splits `pip-audit` findings into production and dev-only packages so severity can be assigned (#37).

- **Tax guard reads the ledger** (`TradeHistoryPort`, `SqliteLedger.trades_between`, `evaluate_ledger`): the recharacterization guard can now evaluate the trades the ledger records for its rolling 90-day window, across all strategies, instead of an in-memory event list. Closes #135.

- **Binance spot trading adapter** (`adapters/exchanges/binance_spot.py`): signed market, IOC limit and exchange-side stop-loss orders, cancels, order book, balances and fees behind `ExchangePort` and a new `StopLossPort`; testnet only unless the live-approval gate allows mainnet. Closes #148.

- **Runner health command** (`src/hermes/entrypoints/runners.py`, `src/hermes/entrypoints/commands/runners.py`): `hermes runners [--json]` reads the self-hosted runners from the GitHub Actions API and prints online/total, busy and a status per label (`hermes`, `hermes-ops`, `dependabot`). OK means every group has at least half its runners online, DEGRADED means fewer than half, FAIL means a group has none online. Needs `HERMES_GITHUB_TOKEN` with repository Administration read. Closes #177.

- **Staging next to prod on hermes-01** (`HERMES_TARGET=staging`, `deploy/systemd/hermes-staging.service`, `docs/guides/staging-on-hermes-01.md`): a `staging` env tier and a second, isolated hermes layout with its own user, unit, code root, state directory, ledger and env file. The release scripts take `HERMES_TARGET=staging` and refuse to run if any path, unit, user or env file would be prod's, or if the env file is not `HERMES_ENV=staging`. The staging unit ships disabled; setting staging up is a manual runbook. Prod is unchanged. Closes #192.

- **Backtest phase 2: real fill path** (`src/hermes/application/execution/kernel.py`,
  `src/hermes/application/backtest/replay.py`,
  `src/hermes/domain/market_data/synthetic_orderbook.py`,
  `scripts/backtest/run_calibration_full.py`): backtest signals now go through
  `FillKernel`, the fill path `hermes serve` will share (#353): entries through
  the `OrderRouter`, exits as a sale of the held quantity, both filled by
  `OrderbookWalkFillModel`, folded into `PortfolioTracker` and returned as
  `TradeExecuted` v2 events that the startup rebuild can replay. Each signal
  gets a synthetic order book whose touch sits at the pre-registered price
  (§6.2: entries 5 bps over close, chandelier exits 15 bps under the stop),
  with depth from the bar's volume, so M-02 slippage is no longer zero. The
  trial each run logs now carries the Sharpe of the daily equity curve.
  Closes #313.

- **Signal event and portfolio state** (`src/hermes/domain/events/signal_emitted.py`,
  `src/hermes/domain/portfolio/`, `src/hermes/application/portfolio/tracker.py`):
  `SignalEmitted` carries a strategy signal onto the event bus and into the
  ledger, with its time taken from the bar. `PortfolioSnapshot` folds fills and
  closed bars into cash, positions and PnL with average-cost accounting, and
  `PortfolioTracker` hands the risk rules their `PortfolioState` from it.
  `config/strategies.yaml` is now the single `swing-donchian-4h` entry with
  `capital_budget` 5000 and `quote` USDT, which seed the tracker. Closes #308.

- **Telegram alert when `hermes.service` is down** (`deploy/systemd/`,
  `deploy/hermes-notify-failure.sh`, `deploy/hermes-health-ping.sh`):
  `hermes.service` gets `OnFailure=`, which sends one Telegram message
  (unit, host, last five journal lines with secrets masked) when the unit
  lands in `failed`. `hermes-health.service` now pings healthchecks.io only
  while the service is active, reads the URL from `/etc/hermes/env.production`
  and no longer swallows a failed ping, so a missed ping means hermes is down.
  Closes #312.

- **Notifications stay blind until OOS certification**
  (`src/hermes/application/messages/reporting_phase.py`,
  `src/hermes/application/messages/slack.py`,
  `src/hermes/adapters/notifier/slack_adapter.py`): a single `ReportingPhase`
  setting defaults to pre-certification, and under it the Slack daily trade
  digest withholds the winners/losers counts and the per-pair P&L CSV.
  Unblinding is an explicit `CERTIFIED` argument on the Slack adapter that
  logs a warning. Closes #319.

- **Live closed-bar source** (`src/hermes/adapters/market_data/binance_live_klines.py`, `src/hermes/domain/market_data/bar_schedule.py`, `src/hermes/ports/bar_feed.py`): `BinanceLiveKlinesAdapter` polls the public klines endpoint of `data-api.binance.vision` after each 4h and 1d close for BTC/USDT, ETH/USDT and SOL/USDT, and emits each closed bar once in a fixed order (close time, then 1d before 4h, then pair). It ships with `ClosedBarFeedPort` (`warm_up`, `stream_closed_bars`, `health`, `aclose`), a `MarketDataConfig` with a one-host allow-list (`config/market_data.yaml`), and a `hermes health market_data` probe. Public market data only: GET, no keys, no link to `allow_mainnet`. Two decisions entries record the host choice and why forward testing stays available inside the OOS window. Closes #307.

- **Shadow bar loop with Telegram and Slack fan-out** (`src/hermes/entrypoints/serve.py`, `src/hermes/application/signal_notifier.py`): `hermes serve` now warms up one swing strategy per pair, turns each live closed bar into a `SignalEmitted` ledger row and a Telegram plus Slack message, and exits 1 on any runtime failure (Sentry capture, ordered shutdown) so systemd restarts it; a notifier outage never stops the loop. A restart seeds history up to a persisted `BarProcessed` cursor without evaluating it, restores each pair's open position from the ledger, and evaluates every later bar, so nothing is re-notified, skipped or forgotten. Adds `StrategyPort.seed` and `OpenPosition`, `NotifierPort.notify`, a `SIGNAL_EMITTED` routing row (aggregate blind: no outcome figures), startup checks (exit 3), and ADR-013, which supersedes ADR-011 D-02 and its Shadow row. Closes #309.

- **Order and Fill carry a strategy id** (`src/hermes/domain/trading/`): `Order.strategy_id` is required and `Fill.strategy_id` is read from its order, so every order and fill is attributable to a strategy (ADR-011 D-03). The router passes its id into the order, and the simulated exchange now stamps lots from the fill instead of its own constructor field. Closes #323.

- **Shadow fills in `hermes serve`** (`src/hermes/application/execution/shadow.py`,
  `src/hermes/entrypoints/serve.py`,
  `src/hermes/adapters/market_data/binance_depth_snapshot.py`): every live
  signal now goes through the `FillKernel` the backtest uses. A simulated order
  walks a keyless Binance depth snapshot, or the pre-registration §6.2 book
  when the snapshot fails or the bar is a catch-up. The fill is recorded with
  `TradeRecorder` as a `TradeExecuted` v2 trade under the strategy id, with
  EUR values or pending. A `FillQualityRecorded` row stores the book used and
  the drift from the §6.2 price for the SHADOW_CERTIFIED gate. Entries pass
  the risk overlay (RO-03 to RO-08) and the §10 kill evaluator, and are sized
  by the roster's pre-registered sizing policy. A fill is never dated before
  the shadow capital seed, so the seed always pays for it, and each bar's
  portfolio mark is taken after that bar's fill. After a
  restart a re-evaluated bar does not fill twice, and the portfolio is rebuilt
  from the recorded trades. Fill logs and pushed notifications carry no P&L.
  Each release now ships the signed pre-registration and the fee history.
  Closes #353.

- **Backtest/live parity check** (`hermes parity check [--date YYYY-MM-DD] [--db PATH] [--config-dir DIR] [--alert] [--offline]`): replays a UTC day of the bars serve stored through the same strategy and fill kernel, read-only (a warm-up shortfall is filled from the public Binance klines unless `--offline`), and compares the decisions (signals, executed or not) with the ones serve recorded; prints one line per mismatch or `OK — N signals match`. Adds `order_for_delivery`, the one close-time, 1d-first order serve and the replay share. Refs #358.

- **Shadow-parity metrics M-09 and M-10** (`application/calibration/shadow_parity.py`): P&L correlation and Sharpe match between the live shadow run and a backtest replay of the same days, with ADR-011 thresholds; verdicts show only pass, block or insufficient, and raw values stay sealed until certification. Closes #360.

- **Execution-quality control charts** (`domain/monitoring/control_chart.py`, `application/monitoring/execution_quality.py`): EWMA and CUSUM charts on fee, slippage and latency raise one `AnomalyDetected` warning per breach episode, with no P&L in the inputs and no automatic pause. Closes #362.

- **Decision journal** (`hermes journal`): append-only, hash-chained record of Code Owner decisions with options, reasoning, expected outcome and review date; due list and scored reviews; strategy decisions stay sealed until certification. Closes #363.

- **ADR-012 continuous AI evaluation** (`docs/architecture/adr-012-continuous-ai-evaluation.md`): engineering, supervision and research loops plus the supervisor desk land in MVP1, with autonomy limits, outcome-blindness until OOS certification, and a monthly LLM cost cap. Partly supersedes ADR-003 §08. Closes #310.

- Daily alive line: at 07:00 Madrid hermes posts one Slack line with bars processed in 24 h, signals, the next 4h bar, journal error and warning counts and restarts (`hermes status --summary`); a late or down verdict replaces "alive" (#373).

- `hermes activity`: a day-grouped feed of what changed (opened, closed with net, folded stop raises, restarts, warnings, errors); `--all` adds bar checks and signals, `--json` the same data. Works on the `--demo` scenarios and reads the ledger read-only otherwise (#374).

- **`hermes positions` and `hermes positions COIN`** (`entrypoints/console.py`, `application/console/serialize.py`): one net-money row per open shadow position with the stop cushion bar, and a detail view with gross, fees, tax and net, on the demo fixtures (`--demo` or `HERMES_CONSOLE_DEMO`). `--json` nests money under `pnl` and omits it on blind surfaces; `--utc` switches the display timezone. Without a demo the command prints "No open shadow positions." and a note that fills start with #353. Closes #375.

- `hermes logs`: hermes's journal, one readable line per entry (`HH:MM:SS  LEVEL  event  key=value  …corr`), with `--level`, `--since`, `--cycle`, `--last`, `-f` and `--json`; the runbook gets a "Check on hermes" section (#376).

- **Read-only home screen** (`hermes tui`, `hermes --demo <scenario>`): a Textual dashboard on the console read model with the portfolio total first, a heartbeat that is one dim line when all is well, range keys 1/7/30/Y, positions with a plain stop column, activity, the 90-day line and a `?` help screen that carries the tax footnote. Closes #377.

- `hermes bot` and `hermes-bot.service`: a read-only Telegram bot (ADR-015) that answers the operator's chat with `/status`, `/next`, `/signals`, `/money`, `/pos` and `/logs`, pushes change-only heartbeat alerts (silent when late, loud when down, reminders on a backoff, quiet hours), and never changes anything; `hermes status` shows the bot state (#379).

- Slack crash notice: `hermes.service` posts one Slack line after any stop that is not clean (crash, signal, timeout), including crashes `Restart=` recovers from, with the result, exit code or signal and the last `serve.run.failed` line, secrets masked (#380).

- **Certification gates enforced in code** (`application/calibration/certification_gates.py`, `hermes certify`): every pre-registration §7 transition now checks its §9, 90-day and 2027-01-01 rules, missing statistics fail closed, any v1 parameter change returns the strategy to UNTRUSTED, and `hermes serve` refuses `allow_mainnet` unless the strategy is LIVE_APPROVED. Closes #355.

- **Data manifest** (`docs/experiments/swing-mvp1/data-manifest.json`, `src/hermes/application/strategy_protocol/pinned_dataset.py`): backtests now load bars only through a manifest that pins each pair, interval and split with a row count and content hash. A changed dataset fails with `DatasetHashMismatchError`, an unpinned range with `DatasetNotPinnedError`, and OOS bars are refused unless certification mode, `SHADOW_CERTIFIED` and a clock past the OOS window all hold. The backtest report lists the dataset ids and hashes it used. Train and Validation are pinned for BTC, ETH and SOL at 4h and 1d. `certification_context()` no longer turns frozen domain errors into `FrozenInstanceError`. Closes #356.

- **EUR values on every trade, fees in FIFO values** (migration `0004_trade_eur_values.sql`, `domain/lots/eur_fifo.py`, `application/ledger/`, `hermes fx backfill`): trade rows store the ECB EUR rate (USDT read as USD), `price_eur` and `fee_eur`, or `eur_status = pending` when the lookup fails, without ever blocking the fill; lot and consumption EUR values, buy and sell fees included, are replayed from the trades; the simulated exchange no longer labels the USDT price as the EUR basis; `config/frankfurter.yaml` moves to `api.frankfurter.dev/v1` (the old host answers 301). Closes #386.

- **Portfolio history in the ledger** (`portfolio_marks` table, `PortfolioHistoryPort`): `hermes serve` stores the strategy's portfolio after every handled bar (cash, positions, market value, equity, realized PnL, peak equity), so the console can read value and history without access to the engine's memory. One row per bar; a replayed bar writes nothing. Closes #387.

- `hermes serve` records the trailing stop of each open position on every 4h bar (new `stop_marks` ledger table: stop, chandelier level, ATR, high since entry, whether the stop moved), and hands the last stored stop back to the strategy after a restart. Record-only: no signal changes.

- **Console price lookup** (`ports/price_lookup.py`, `adapters/market_data/binance_ticker.py`): read-only current price from the public Binance ticker, cached for 5 s and rate-limited, for valuing positions in the operator console. The strategy cannot import it. Closes #389.

- `hermes serve` logs one `serve.bar.processed` line per handled bar (pair, interval, open and close time, signal, next due time) and a `serve.bar.next_due` line per stream when it goes live, so the journal shows the loop is alive between signals (#371).

- **Portfolio rebuilt from the ledger on startup** (`src/hermes/entrypoints/serve.py`, `src/hermes/application/portfolio/tracker.py`):
  `hermes serve` replays the strategy's recorded `TradeExecuted` events to restore cash and positions instead of
  restarting from the seed, and refuses to start when the ledger cannot be replayed. `TradeExecuted` now carries
  `strategy_id` (event schema version 2).

- **Tax config** (`config/tax_scales.yaml`, `hermes.infrastructure.config.tax`):
  per-year IRPF savings scale (2025 and 2026, 30% top band) with a fail-closed loader,
  plus a local taxpayer profile (`~/.config/hermes/taxpayer.yaml`, override
  `HERMES_TAXPAYER_PROFILE`) whose values never appear in repr, logs or errors.
  Closes #394.

- **Yearly tax estimates** (`domain/tax/`, `application/tax/`, `hermes tax [--year YYYY] [--json] [--price ASSET=EUR]`): the IRPF savings tax on hermes gains for a Madrid tax year, marginal over the taxpayer profile, with USDT replayed as its own FIFO asset once seeded (EUR/USD gains on spent USDT count), with the 25% cross-offset, 4-year carry-forward, the personal-minimum remainder, today's tax, tax if sold now (split per position) and two-month repurchase flags (flagged, never deferred); any EUR-pending trade or missing scale gives a pending notice instead of a guess. Every figure is labelled an estimate (and shadow); an import-linter contract keeps strategy, sizing, risk, fee and kill-rule code from reading it. Closes #395.

- **Operator sees shadow P&L under a binding rule** (`docs/decisions/2026-10-03-operator-sees-shadow-pnl.md`): the Code Owner sees shadow P&L, gross and after estimated tax, in the console and Telegram pull replies, bound by five rules until certification: no strategy change, halts only by a §10 kill criterion or a logged operational fault, mechanical certification, AI surfaces stay blind (ADR-012 D-06), and v2 cannot reuse the OOS window. Recorded in the lab notebook before the first simulated fill. Pre-registration v1 is unchanged. Closes #385.

- `hermes status`: a read-only progress check that leads with the shared verdict (all good, late, down) and its next step, then each stream's last and next close in UTC and Madrid time, signal counts and open shadow entries; `--json` for scripts. vps-diagnose now runs it (#370).

- **Certification statistics** (`domain/statistics/`, `hermes certify --performance`): Deflated Sharpe Ratio in its published form, circular block bootstrap CI on Sharpe, the six §9 secondary metrics, walk-forward and purged combinatorial CV splits, all stdlib-only. `hermes certify --performance FILE` computes a window's §9 metrics so the certification gates no longer fail closed for lack of statistics. Closes #354.

- **Console read model, demo data and renderers** (`application/console/`, `adapters/console/`, `hermes --demo`): one `ConsoleReadModel` contract with frozen view objects, six packaged demo scenarios (`HERMES_CONSOLE_DEMO` or `hermes --demo <scenario>`, always tagged DEMO DATA), shared Rich renderers for the heartbeat, portfolio hero and waterfall, positions, position detail, activity and the 90-day line, and `pnl_visible(surface)`, which shows shadow P&L only on the operator CLI, TUI and pulled Telegram replies. `rich` is now a direct dependency. Closes #384.

- **USDT as its own FIFO asset** (`src/hermes/domain/lots/quote_leg.py`,
  `src/hermes/adapters/ledger/sqlite_ledger.py`, migration `0006_capital_seeds.sql`):
  `hermes serve` records the shadow start capital once as a `ShadowCapitalSeeded` event and one
  USDT lot, dated by the ledger's earliest bar, signal or trade. From then on every BTC/USDT BUY
  disposes of USDT and every SELL acquires it through the same EUR FIFO replay, so EUR/USD drift
  on held USDT becomes a realized gain or loss. A BUY fee charged in the base asset now reduces
  the lot quantity and is priced in EUR. `hermes fx backfill` prices the pending seed by its seed
  date. Strategy, sizing, signals and fills are unchanged. Closes #405.

- **End-to-end serve cycle tests** (`tests/e2e/test_serve_cycle.py`): the real
  `hermes serve` composition root replays twenty days of recorded Binance bars
  for BTC, ETH and SOL and is checked against the pinned signals in the
  ledger, the notifications and the per-bar journal lines, across a stop and
  restart, a missed bar, and a `hermes status --json` snapshot. They run
  weekly and on manual dispatch in the Scheduled workflow. Closes #408.

- **Automatic trial log** (`docs/experiments/swing-mvp1/trial-log.jsonl`, `src/hermes/adapters/trial_log/jsonl_file.py`): every backtest run that evaluates the strategy appends a hash-chained trial record (strategy, parameters, split, window, pinned datasets, metrics, git SHA) before its report is written, with no opt-out. Edited, removed or reordered lines stop reads and appends. `scripts/backtest/trial_stats.py` prints the trial count, Validation touches and Sharpe variance for the Deflated Sharpe Ratio. Closes #357.

- **Live bar store** (`live_bars` table, `src/hermes/adapters/ledger/migrations/0003_live_bars.sql`): `hermes serve` now records each handled closed bar (OHLCV) in an append-only table keyed by pair, interval and open time, for the Binance archive check and the parity replay. Data only: strategy, signals, sizing and the `BarProcessed` schema are unchanged. Closes #421.

- **Daily Binance archive check** (`hermes archive-check`, `deploy/systemd/hermes-archive-check.timer`): compares yesterday's stored live bars with the Binance public daily archive (checksum-verified) for every traded pair and interval, and alerts once per missing or differing bar, naming pair, timeframe and bar. Read-only against the ledger. Closes #359.

- **ADR-015 (Proposed): read-only Telegram commands** (`docs/architecture/adr-015-read-only-telegram-commands.md`): a separate long-polling `hermes-bot.service` answers only the operator chat with `/status`, `/next`, `/signals`, `/money`, `/pos` and `/logs`, never accepts trade commands, keeps its token in a root-owned env file without exchange keys, keeps pushed messages blind to P&L, and sends change-only heartbeat alerts with rate limits and quiet hours.

- **Ledger migration rollback protocol** (`hermes migrate`, `docs/guides/ledger-migrations.md`): each migration now runs in one transaction, so a half-failed one leaves no trace. Migrations marked `-- hermes: destructive` never run when the ledger opens; they run only through `hermes migrate`, which writes and verifies a snapshot first. `hermes migrate --restore` puts a snapshot back, which is the rollback (ADR-006 keeps migrations forward-only).

- `hermes status` joins the console: `hermes --demo SCENARIO status` (or `HERMES_CONSOLE_DEMO`) shows the scenario heartbeat with the DEMO DATA tag, and `heartbeat_from_report` maps the status verdict onto the console heartbeat for the TUI and Telegram (#454).

- **Supervisor desk briefings** (`hermes supervisor brief [--weekly]`): a daily line and a weekly brief built by code from the read-only supervisor tools, each line citing its source, with no model call. Every brief passes the desk's output gate (no link, code fence, secret-shaped string or outcome word; every number and reference must exist in the input), and falls back to a digest of states and counts when free text fails it. First slice of #365; closes #496.

- `hermes money [--range today|week|month|year]`: the portfolio section on the command line (total value in EUR, change for the range, gross-to-net waterfall, tax footnote), on the `--demo` scenarios until live money lands; `--json` carries money under `pnl` (#498).

- `hermes tui`: the `L` key opens the last 50 journal lines, rendered like `hermes logs`, instead of a placeholder (#508).

- **Journal retention per ADR-008** (`scripts/deploy/vps_bootstrap.sh` step 9): bootstrap writes a journald drop-in capping the journal at 2G and 180 days, and restarts journald only when the file changed. It takes effect on hermes-01 once bootstrap is re-run. Closes #512.

- **Dead-man pings for the daily line and the archive check** (`deploy/hermes-daily-line.sh`, `hermes archive-check`): each timer pings its own healthchecks.io check when `HERMES_HEALTHCHECKS__DAILY_PING_URL` or `HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL` is set: success after a completed run, `/fail` when the run could not do its job. Unset, nothing is sent; a failed ping never changes the exit code. Closes #526.

- **`hermes config check [--config-dir DIR] [--json]`** loads the config and runs the startup checks without starting anything or touching the network; it names each unset secret variable and invalid field, never a value, and exits 0 clean, 1 findings, 2 usage, 3 config unreadable. Closes #537.

- **`hermes ledger verify`**: read-only ledger check (SQLite `mode=ro`) that runs `PRAGMA integrity_check` and reports the schema level, the event count and gaps in the event id sequence; `--json`, exit 0 OK, 1 findings, 3 ledger missing or unreadable. Closes #538.

- **`hermes version` reports what is running** (`hermes version [--db PATH] [--json]`, also `hermes --version`): package version, live release (the `/opt/hermes/current` target), Python version and ledger migration level, read-only; always exits 0 except 2 on usage. Closes #539.

- **`hermes health` takes `--json` and `-h`/`--help`**: `--json` prints one document with an object per integration (name, status, detail) and the overall exit code, with secret values masked; `--help` lists the exit codes and an unknown option exits 2. Tables and exit codes are unchanged. Closes #540.

- `hermes journal -h` now prints usage and the exit codes and exits 0, and `journal list --json` and `journal due --json` print the entries as one JSON document (#544).

- **Live positions in the console** (`adapters/console/ledger_read_model.py`): `hermes positions` and `hermes positions COIN` now read the ledger read-only (`mode=ro`, `--db PATH`) when no `--demo` is selected: size, entry and latest price from the newest portfolio mark, stop from the entry signal, cost, fees and net in EUR from the recorded trade values. A fill not yet priced in EUR shows as "EUR pending" with USDT prices instead of a guessed figure. Portfolio, activity, shadow test and `hermes tui` stay demo-only until the next slices of #546.

- **`hermes migrate --dry-run`** (`migration_runner.dry_run`): applies every pending migration, destructive ones included, to a temporary copy of the ledger and reports what ran, the copy's integrity check and each table's row count before and after; the real ledger is only read. Closes #556.

- `hermes ledger verify` also reports lots whose remaining quantity does not match their consumptions, orphaned consumptions and lot-trade links, and applied migrations the release does not ship.

- **`hermes tax-pack`** (`src/hermes/application/filing/pack.py`, `src/hermes/entrypoints/tax_pack.py`): keeps one tax filing pack per year up to date as hermes trades. Each pack holds a Spanish PDF sheet with the Renta Web entries (box from `config/renta_campaigns.yaml`, F/N keys) and the Modelo 721 fields when due, a PDF dossier for Hacienda, the Modelo 100 and 721 figures, a trail from every sale to its lots, fees and ECB rates, a readiness checklist, a dated log of changed figures and a checksum manifest. A closed year whose pack is ready freezes. The estimate is added only where the taxpayer profile exists. A daily `hermes-tax-pack.timer` (07:30 Madrid) ships switched off. Nothing is submitted to the AEAT (#568).

- **Console on the VPS from your PC** (`hermes --vps <command>`): runs a read-only console command (`tui`, `status`, `positions`, `activity`, `money`) on the VPS over `ssh -t` and shows it locally, with one-line errors for no ssh, unreachable host, auth failure and a VPS that needs a deploy. Host, user and key come from `HERMES_VPS_HOST`, `HERMES_VPS_USER`, `HERMES_VPS_KEY`. A deploy now installs a read-only `hermes` wrapper on the VPS, console commands default `--db` and `--config-dir` from `HERMES_DB_PATH` and `HERMES_CONFIG_DIR`, and `--demo` works after the command. Closes #610.

- **Serve prices pending EUR values at boot** (`price_pending_eur_once`): `hermes serve` runs the existing EUR backfill once after seeding, so the capital seed shows its EUR value without `hermes fx backfill`; an FX failure is logged and leaves the value pending. Closes #630.

- **Release in status** (`src/hermes/entrypoints/status.py`, `status_view.py`): `hermes status` shows the running release (the `/opt/hermes/current` target) and when it went live, in the text header and `--json`; `release: unknown` without the link. Closes #636.

- Console: a market watch lists each checked coin's last check, whether it produced an entry or exit signal, and the next check, with no prices. The range keys are now D/W/M/Y, the start line reads "started 5,000 USDT (€X at ECB rate, D)", the EUR total names the date of the ECB rate it uses (#692), the EUR-pending hint points at the next restart, and the top line names the running release (#650, #649).

- **`--vps` reconnects**: the ssh session now sends a keepalive (a dead link is noticed in about 30 s), a dropped connection is retried with a 2 to 30 s backoff until Ctrl+C, and the terminal leaves the alternate screen after every exit. Closes #651.

- `hermes serve` logs which of the three entry conditions (breakout, momentum, regime) held on each 4h check, as yes/no fields on `serve.bar.processed`. Record-only; no prices or indicator values.

- **Live home screen and activity** (`adapters/console/ledger_read_model.py`, `application/console/portfolio_figures.py`, `application/console/shadow_figures.py`): `hermes tui` (new `--db PATH`) and `hermes activity` now read the ledger read-only when no `--demo` is selected. The portfolio total is shown in EUR first, summed from each strategy's portfolio marks and capital seed at the newest recorded EUR rate; the start capital is the 5,000 USDT seed labelled "Equivalent in EUR"; Today / This year come from the FIFO values of closed sales. Opened and closed activity rows come from recorded fills (fill price in EUR, realised net on a close), or from signals on a ledger without fills. The shadow test line counts days from the capital seed toward 90 with the fill count, and says its verdict is not available yet. Anything without an EUR value shows "EUR pending", never a guess; a card the ledger cannot answer yet says "not available yet" instead of stopping the screen. `hermes money` and the bot's `/money` and `/status` read the same live portfolio.

- **Slack trade plan in a thread** (`adapters/notifier/slack_adapter.py`, `application/messages/slack.py`): a signal posts its headline as the Slack message and the plan (stop, distance, ATR multiple, the withheld actual) as a reply in its thread; a failed reply keeps the headline, logs a warning without the text and raises. Other events stay one message, and `hermes preview` shows both parts.

- **Telegram and Slack pushes show prices in EUR** (`application/messages/notes.py`, `application/ledger/eur_pricer.py`, `entrypoints/serve.py`): entry and exit signals and large-position fills now show price, stop, value and fee in EUR (for example `€41,928.16`) using the rate `serve` already holds for EUR pricing. The rate comes only from memory (new `EurPricer.cached_rate`, at most 7 days old), so a slow or failing FX source never delays a push; with no rate known the push keeps its USDT text. No profit or loss figure is added. `hermes preview` gains `--eur-rate RATE` to render the EUR form.

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

- The supervisor desk briefs (`hermes supervisor brief`, daily and `--weekly`) now include "What the engine did". For each pair, the section shows the bars checked, the entry and exit signals with the exit rule, the fills by side, and whether a position is held. The data comes from a new read-only `engine` tool (`hermes:read:engine`). No prices or values are shown.

- **Release cut** (`scripts/dev/cut-release.py`): `uv run python scripts/dev/cut-release.py 1.0.0-shadow.1` sets the version in `pyproject.toml`, `src/hermes/__init__.py` and `uv.lock`, compiles `[Unreleased]` and every `changelog.d/` fragment into a dated section, deletes the compiled fragments and writes the release PR body. Release versions are `X.Y.Z` or `X.Y.Z-shadow.N`; the package stores a shadow build as PEP 440 alpha `N` (`1.0.0a1`). `pr-body-lint.sh` rejects a release PR whose `Engine change:` line is not `yes` or `no` (ADR-022).

### Changed

- **Process hardening wave 4** (`scripts/dev/create-pr.sh`,
  `scripts/dev/aggregate-changelog.py`, `scripts/dev/changelog-check.sh`,
  `changelog.d/`, `AGENTS.md`): closes four drifts observed in the PR
  #124-#132 queue. (1) create-pr.sh title gate enforces the CI "PR title"
  `subjectPattern` locally (regex `^[a-z]+(\(.+\))?!?:\s+[a-z]`), rejecting
  proper-noun uppercase subjects (e.g. `Telegram`) before `gh pr create`
  — closes the PR #130 drift. (2) AGENTS.md documents `# gitleaks:allow`
  inline as the required marker for test-only credential-shaped literals
  (ruff `# noqa: S105` is insufficient) — closes the PR #132 drift. (3)
  AGENTS.md mandates `git fetch origin main` before worktree-isolated
  sub-agent launches — closes the wave-3 rebase-cascade drift. (4)
  Structural CHANGELOG conflict eliminated via `changelog.d/` fragments
  (one `<pr-number>-<type>.md` per PR); `changelog-check.sh` accepts
  either path; `scripts/dev/aggregate-changelog.py` merges fragments
  into `[Unreleased]` at release cut. THIS PR is the transition —
  it edits CHANGELOG.md directly AND ships a placeholder fragment
  (`changelog.d/wave-4-process-hardening.md`). Future feature/fix PRs
  should use the fragment path instead of editing this file. create-pr
  self-test grows to 11 fixtures (was 9); new pytest coverage for the
  aggregator in `tests/unit/infrastructure/test_aggregate_changelog.py`.
- **Process hardening wave 3** (`scripts/dev/create-pr.sh`,
  `scripts/dev/no-vague-test-assertions.py`, `scripts/dev/pr-preflight.sh`,
  `.markdownlint-cli2.jsonc`): closes three drifts observed in the
  PR #124-#127 queue. (1) create-pr.sh now copies repo-relative
  `--body-file` paths to a mktemp'd `/tmp/` location before lint +
  `gh pr create`, and `.markdownlint-cli2.jsonc` ignores `.pr-body-*.md`
  as belt-and-suspenders — sub-agents that wrote body files at repo
  root repeatedly hit MD041 because PR bodies start with H2 `## Summary`
  by template. (2) The vague-test-assertions AST walker now allows an
  `ast.Attribute` on either side of `==` (`assert side == TradeSide.BUY`
  and `assert obj.field == value` now pass); the true vague case
  `assert result == expected` (both sides plain Names) stays rejected.
  (3) pr-preflight summary now names its invocation mode ("with PR
  body lint" / "without PR body lint") so the 13-vs-14 check count no
  longer reads as silent drift. New create-pr self-test fixture (9
  total, was 8); new vague-assertion self-test fixtures (5 PASS + 4
  REJECT, was 3+3).
- **AI-authored-tests guide** (`docs/guides/ai-authored-tests.md`): new
  test pyramid tier 2+3 definitions (integration and e2e: markers,
  fake/mock/testnet policy, hermes density guidance) and "AI development
  lifecycle × test tier" matrix mapping 7 SDD phases across 3 tiers.
  Enforcement table rewritten to reflect PR A truth (D103, `fail_under = 90`,
  `coverage-check.py`, and `no-vague-test-assertions` now `live`; mutation
  testing deferred to Phase 1 pending #118). Cross-link added from
  `ai-development-lifecycle.md` Step 5. Closes #117.
- **Notification channels** — added Slack alongside Telegram, routed
  by content class (Telegram = urgent/terse, Slack = rich/reports).
  Supersedes ADR-008 §4 "Telegram alerts — user-facing signals only"
  via decisions-journal entry
  `docs/decisions/2026-08-23-slack-alongside-telegram.md`.

- **Notification templates v2**: every Telegram and Slack message is rewritten in one house style (plain-text severity glyphs, EUR-style fixed precision and thousands separators, Madrid time, one correlation ref, no P&L before certification) with the shape per event family set in one table (`SHAPES` in `application/messages/shapes.py`); `StrategyResumed` now reaches Telegram, `RebalanceTriggered` is routed, signals carry their stop, Telegram pushes are silent unless down, P0 or a rejected order, and `hermes preview` covers every event on both channels. Part of #144.

- **`lots.strategy_id` has no default any more** (migration `0005_lots_drop_strategy_default.sql`): the legacy `DEFAULT 'swing'` is dropped, so a lot written without a strategy id fails instead of getting a second spelling. Existing rows keep their values. The migration is marked destructive and runs on an existing ledger only through `hermes migrate`, which snapshots first; a brand-new ledger now applies destructive migrations at first open, since it has nothing to protect. Closes #325.

- **CD can deploy hermes for the first time** (`.github/workflows/cd.yml`,
  `scripts/deploy/`): a `bundle` job ships the CI-built wheel, the
  hash-pinned requirements, `config/`, `deploy/*.sh` and the deploy scripts as
  one tarball.
  The VPS installs it into `/opt/hermes/versions/<sha12>/`, flips `current`
  atomically, restarts `hermes.service` and gates the result (unit active
  30 s, same PID, `serve.run.started` in the journal); a failed gate rolls
  back to `previous`. `vps_bootstrap.sh` is now the only bootstrap script
  (user, dirs, uv, pinned and verified Python 3.14.7, units, env file written
  once, optional deploy sudoers rule), each step guarded and tested, safe to
  re-run over a live storage-box mount, and `deploy/bootstrap-vps.sh` is
  gone. The smoke guard and the `confirm` aggregator now fail when they
  should. Closes #179.

- The `PR body` check now re-runs when a PR body is edited, without re-running the rest of CI, and Dependabot PRs are no longer exempt from it (#350, #162).

- ADR-014: the signed pre-registration v1 is the binding gate for real capital — no live capital before the one-shot OOS certification, which cannot run before 2027-01-01. Partially supersedes the real-capital dates in ADR-004 and ADR-006. The forward-testing guardrail (D-8) is recorded as confirmed by the Code Owner.

- `vps-diagnose` now shows `hermes.service` status, its last hour of journal, serve lifecycle events, the failure-alert units, per-integration `hermes health` probes and the newest processed bar per stream from the ledger, all read-only (#348).

- vps-diagnose prints one plain Sentry line (`CONFIGURED`, `NOT_CONFIGURED` or `FAIL`), and `deploy/README.md` documents the prod Sentry alert rule to Slack `#hermes-reports` (#381).

- **Taxpayer profile accepts a same-year loss outside hermes** (`src/hermes/infrastructure/config/tax.py`): `other_capital_gains_eur` may now be negative, so a loss on other investments nets against hermes gains in the same tax year (#438).

- The merge queue gates and merges every approved PR in one pass: it
  merges them into one tree, oldest approval first, runs the gates once,
  lands the batch back to back, and bisects a red batch to find the PRs
  that broke it, which get one comment naming the failing gate (#413).

- **One module per CLI command** (`src/hermes/entrypoints/commands/`): each `hermes` subcommand declares itself in its own module and `cli.py` discovers them, so adding a command no longer edits a shared list and parallel PRs stop conflicting in `cli.py`. No behaviour change. Closes #457.

- **Timestamp-named ledger migrations** (`scripts/dev/migration-sql-check.py`, `docs/guides/ledger-migrations.md`): new migrations are named `YYYYMMDDTHHMM_<slug>.sql` so parallel PRs cannot take the same number; the numbered files 0001-0006 keep working and sort first. A pre-commit and unit-test check rejects badly named files and any `BEGIN`/`COMMIT`/`END`/`ROLLBACK`/savepoint statement outside trigger bodies, since the runner wraps each migration in one transaction. Closes #483.

- **Console panels are discovered like CLI commands**: `run_positions` and `run_tui` moved from `entrypoints/console.py` into their command modules, and `CONSOLE_COMMANDS` is replaced by `console_commands()`, derived from each command's `wants_demo`. A new panel adds one file and edits no shared list. No output changes. Closes #525.

### Fixed

- Removed non-functional `Add to project board` step from CI workflow;
  `GITHUB_TOKEN` cannot write to user-owned projects (only org-scope).
  Items now added manually via `gh project item-add` until a PAT-based
  restoration is set up (documented inline in `.github/workflows/ci.yml`).

- **Kraken adapter**: ETH/USDT and ETH/USDC now use Kraken's `ETHUSDT` and
  `ETHUSDC` symbols (the old `XETHZUSDT` form was an unknown pair), and 4h
  candles are supported, so the backup venue can serve the swing strategy's
  bars (#31).

- **`hermes.service` boots `hermes serve` under systemd** (`deploy/systemd/`,
  `src/hermes/__main__.py`): the unit runs the active release's console
  script from `/var/lib/hermes` with `/etc/hermes/env.production`, bounds
  crash loops (no restart on exit 2 or 3, 5 starts per 10 minutes), and
  `python -m hermes` now works. The env example is paper-only and loads
  through the real config loader, both timers parse under systemd, and the
  nightly backup now includes `*.sqlite3` files. Closes #306.

- **Calibration no longer reads daily bars before they close**
  (`src/hermes/domain/strategy/swing/adapter.py`,
  `scripts/backtest/run_calibration.py`): the calibration driver merges the
  4h and 1D streams by close time, and the swing adapter's EMA regime filter
  ignores any 1D bar that has not closed by the 4h bar it evaluates, so a
  caller ordering bars by open time can no longer leak up to 16 hours of
  future prices into entries and exits. Closes #311.

- **VPS bootstrap re-run** (`scripts/deploy/vps_bootstrap.sh`): step 3 skips the
  mount-point `install` when `/mnt/storage-box` is already mounted, so re-running
  the bootstrap no longer fails on the live sshfs mount. Closes #334.

- **BalanceLow payload hash** (`src/hermes/domain/events/balance_low.py`): the event
  now runs the base `DomainEvent.__post_init__`, so `payload_sha256` is populated
  instead of empty; a guard test checks every event override calls the base.
  Closes #324.

- **`hermes health` reads `.env.local`** (`src/hermes/entrypoints/cli.py`):
  probes now see credentials kept in `.env.local`, as `load_config` does; a
  missing file is a no-op and real environment variables still win.
  Closes #316.

- The Telegram failure alert for `hermes.service` now runs from `/opt/hermes/bin`, so a missing or broken release no longer silences it; its unit is named `hermes-notify-failure@hermes.service`, and the VPS bootstrap clears a stale failed alert unit and turns on the healthchecks.io timer once the ping URL is configured (#364).

- The healthchecks.io ping now reports progress: it runs `hermes status --json` and sends a `/fail` ping with the status when a check is late or hermes is down, so a running but stalled loop alerts within one tick (#372).

- **Shadow Sharpe kill counts 60 consecutive days** (`application/strategy_protocol/shadow_sharpe_kill.py`): the pre-registration §10 shadow rule now fires only after 60 consecutive UTC days with the 30-day rolling Sharpe (daily returns, annualised by √365) below 0.3, instead of on a single value; the reason never reveals the Sharpe. Closes #422.

- **Deploys snapshot the ledger first** (`scripts/deploy/install_release.sh`):
  before a release starts, CD takes an SQLite online backup of
  `/var/lib/hermes/hermes.sqlite3` into `backups/pre-<sha12>.sqlite3`
  (newest 5 kept), and a release that fails the health gate is rolled
  back onto that snapshot, so a schema migration never meets the old
  code (#455).

- Position sizing now follows pre-registration §5.4/§5.5: each trade risks 1% of the configured experiment capital (risk ÷ stop distance × entry), capped at 5% per position and 15% total open notional, with the shares and capital read from `config/strategies.yaml`; the Kelly cap and the equity-based ATR formula are removed (#480).

- The `hermes tui` home screen no longer crashes when a refresh lands between opening a panel (detail, logs or help) and the panel drawing; this also fixes the flaky `test_app_refreshes_and_only_reads`.

- Risk-overlay log lines (halt, warn and per-rule decisions) now carry only the rule id and a fixed reason, such as `rule=RO-08 reason=drawdown_limit`, so no drawdown or other P&L-derived figure reaches the journal that blind surfaces read; the full reason stays on the rejection for operator surfaces.

- `hermes positions` and `hermes tui` answer `-h`/`--help` with their usage, flags and exit codes and exit 0, instead of "bad arguments" with exit 2.

- Tidy the top-level `hermes` help: accurate header, name column sized to the longest command, a note on `--demo` and `--json` commands, and a "did you mean" hint for a mistyped command.

- **Every `hermes` command writes its ERROR lines to stderr** (`src/hermes/entrypoints/_streams.py`): stdout now holds only a command's real output, so a failing `--json` call no longer feeds an error line to the JSON consumer. Exit codes and message wording are unchanged. Closes #543.

- `hermes serve` now logs one JSON object per line, as ADR-008 specifies. Before this, the daily alive line counted zero errors and warnings however many there were, and `hermes logs` could not filter by level or correlation id (#547).

- **Reconciliation property test**: `test_book_conserves_value_at_trade_prices` now checks the book in its own exact decimal context. The default 28-digit context rounded the expected side for inputs mixing 1E-16 and 1E12 flows, turning main red at random. The book itself was exact. Closes #585.

- **Kraken failover fees** (`src/hermes/adapters/exchanges/kraken_spot.py`):
  Kraken stopped publishing fee tiers in its public `AssetPairs` reply, so the
  Kraken adapter now reads the account's own maker and taker fee from the
  private, read-only `TradeVolume` call. The fee contract test skips without a
  Kraken key. Closes #599.

- `hermes ledger verify` names a consumption whose quantity cannot be read, instead of reporting its lot as a remainder mismatch.

- The deploy integration tests now pass on Windows when `HERMES_TEST_BASH` points at Git's `bin/bash.exe` launcher: the harness runs the `usr/bin/bash.exe` it wraps, so the test stubs stay first on `PATH` (#618).

- `hermes --help` now lists every command that accepts `--json` (activity, config, failover-check, health, journal, ledger, logs, money, positions, reconcile, runners, status, tax, version), built from the command registry instead of a hand-kept list that had fallen behind.

- **Boot EUR pricing no longer delays catch-up or blocks shutdown** (`hermes serve`): pending EUR values are priced after the catch-up fills, off the event loop, capped at 20 FX calls and 60 s per boot, and a failing `(quote, date)` rate is tried once instead of once per trade. Leftovers stay pending for the next boot or `hermes fx backfill`. Closes #658.

- The chandelier stop no longer moves down while a position is open: the stop in force is the highest level since entry, a new position starts from its own level, and a restart continues from the stored stop (pre-registration §5.3).

- **Shadow loop and backtest**: an entry that is not filled (stale book, exposure cap, a risk or kill halt, no book) now leaves the strategy flat for that pair, also after a restart, so it no longer signals and notifies an exit for a position it never held and can enter that pair again (#707).

- **Backtest**: an entry signalled during warm-up, before the test window, now leaves the strategy flat as a refused entry does, so every window starts flat and no longer skips its first real entry or signals an exit for a position it never held (#723).

- CD deploys again: the schema guard added for issue #655 is fed to the service user on stdin instead of from the root-only staging dir, which it could not open, and a guard that fails to run is reported as such instead of as a schema refusal.

- Hermes errors raised inside a `with` block of a generator context manager now reach the caller as themselves instead of `FrozenInstanceError`, so `hermes parity` reports an exchange outage cleanly and the supervisor and tax maths keep their error messages (issue #734).

- A deploy no longer prunes an older release that a running process, such as a console opened before the deploy, still runs from; it keeps it and logs the pid.

- Deploy and rollback now tell a schema-guard refusal (exit 3) from a guard that crashed, instead of reporting both as "cannot read the events" (issue #751).

### Security

- Telegram and Slack bot tokens no longer appear in `repr` output, and Sentry error events no longer include stack-frame local variables, so a failed alert can't ship a bot token to Sentry (#506).

- The AI review no longer puts the workflow's GitHub token on a command line or into the model's input when it cannot read a PR's closed issue; it reads the issue with the `issues: read` permission it was missing (#642).

- The AI reviewer can no longer write files, load a PR's own agent settings, pass the check on a PR that changes the review or merge gates, take over another person's comment, or render links and images from model text (#778).

- Setting `HERMES_BINANCE__API_KEY` / `HERMES_BINANCE__API_SECRET` no longer breaks config loading. Config errors no longer echo the rejected value, so a secret can't leak through one, and the config's repr masks the Sentry DSN and healthchecks ping URL (#794).

### Notes

- No production runtime yet — Phase 0 lands `pyproject.toml`,
  hexagonal `src/hermes/` skeleton, CI workflows, testing scaffold.
- Version stays at `0.0.0-dev` until Phase 0 tags first pre-release.

[Unreleased]: https://github.com/ricardoafo/hermes/commits/main
[0.2.0]: https://github.com/ricardoafo/hermes/releases/tag/v0.2.0
[1.0.0-shadow.1]: https://github.com/ricardoafo/hermes/releases/tag/v1.0.0-shadow.1
