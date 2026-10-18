# hermes console: design spec (Look A "Cockpit")

Status: agreed with the Code Owner on 2026-10-05 (issue #711, round 3 comment).

Visual reference (animated mock, all screens):
<https://claude.ai/artifact/XasRPByihoZZc4hjovguts>

Implementation: issues #711–#722, in the build order below. Mock figures are synthetic. Build from the real read model.

## Decisions the Code Owner made

| Date (UTC) | Decision |
| --- | --- |
| 2026-10-05 12:50 | Look A, "Cockpit" ("in general I like the design"). |
| 2026-10-05 12:50 | Market watch shows live Kraken public prices, refreshed every 10 s, in **MVP1**. |
| 2026-10-05 12:48 | Console actions that change the VPS (bootstrap, restart, rollback) wait for **MVP2**. MVP1 System screen shows the command to copy. |
| PR #659 | Engine brief shows the yes/no per entry condition. The record-only field is built in PR #659. |

## Hard rules (every screen)

- Never places, changes or closes a trade. Never changes strategy, sizing, mainnet or secrets (pre-registration v1).
- Signal analysis shows each entry rule as yes or no only. No "distance to trigger" numbers (ADR-012, sealed test window).
- Supervisor-authored panels (engine brief, briefs) never see P&L, equity, position value or prices from the test window (ADR-012 D-06, #319).
- Live Kraken prices are display-only for the operator. They never feed the engine, the ledger, the supervisor or any notification.
- Taxpayer figures stay in `~/.config/hermes/taxpayer.yaml` on the operator's PC and are masked by default. Over `--vps` the Tax profile panel says only whether a profile exists.
- Keep the colour-blind palette: up = blue (70,140,230), down = orange (240,120,20), warning = amber.
- Everything uses Textual 8 + Rich 15 only. No new libraries without the Code Owner's yes. The live ticker reuses `KrakenPublicAdapter.get_ticker` (src/hermes/adapters/exchanges/kraken_public.py), so no new dependency is needed.

## Look A: visual language

- Custom Textual theme ("hermes"): ink-navy background, slightly lighter panels, cyan accent (88,209,235), violet secondary (157,101,255). Register it with `App.register_theme`.
- Panels: `border: round`, with `border_title` in the accent colour and `border_subtitle` for key hints (for example the D W M Y range).
- Portfolio total: `Digits` widget with a horizontal gradient (cyan → blue → violet), drawn per character with Rich `Color.blend`. It counts up over about 0.6 s when the value changes (animate a reactive float).
- Charts: braille area chart for the portfolio curve, with a vertical colour gradient (drawn with Rich Text, no add-on). `Sparkline` for per-coin and per-position trends.
- Meters: gradient `ProgressBar` for the countdown to the next bar, the 90-day run, cash vs invested, and stop room. The stop-room meter turns amber when the stop is close.
- Chips: reverse-video key chips in the footer (`D W M Y`, `↑↓`, `↵`, `L`, `?`, `^P`, `Q`).

## Design tokens: futuristic, AI-made

The Code Owner, 2026-10-05 12:56–12:57: "more alive … as modern as possible", "futuristic … AI made product let it look like it".

Define them once as a Textual `Theme(name="hermes", …, variables={...})` and use only the variables in TCSS and renderers. This is the base token sheet; the Neon theme (round 3, below) overrides some of them.

| Token | Value | Use |
| --- | --- | --- |
| `void` (background) | #070B14 | screen |
| `panel` (surface) | #0C1424 | panels |
| `panel-focus` (boost) | #0F1B33 + border #2C4F86 | focused panel; title becomes a cyan chip |
| `overlay` | #18233A | toasts, dialogs, boot screen |
| `line` | #1A2740 | idle borders |
| `live` (accent) | #58D1EB | live dot, focus, links, live tags |
| `ai` (secondary) | #9D65FF | anything the AI supervisor wrote (✦), AI panel borders |
| `up` | #468CE6 | gains (colour-blind blue) |
| `down` | #F07814 | losses (colour-blind orange) |
| `warn` | #E6BE28 | late, stop near |
| `text` / `muted` / `dim` | #E6ECF5 / #8A94A8 / #5D6780 | figures / labels / hints |
| gradient `brand` | #8BE9FF → #468CE6 → #9D65FF | big totals, aurora rule, 90-day bar |
| gradient `gain` / `loss` / `ai` | #1B3A6B→#468CE6→#8BE9FF / #7A3A0A→#F07814→#E6BE28 / #3A2A66→#9D65FF→#E0C8FF | meters, charts, AI marks |

Motion tokens: `instant` 80 ms, `fast` 150 ms (focus/border), `base` 300 ms (toast, slide), `slow` 600 ms (count-up), `ambient` 1.6 s (pulse) and 9 s (aurora). Easing `out_cubic` for UI changes, `in_out_sine` for ambient effects. Ambient effects repaint at most one row, at no more than 8 fps.

Type tokens: hero figures in `Digits` with the brand gradient and a shimmer pass every ~6 s; panel titles UPPERCASE with the accent; figures bold; labels muted; hints dim.

Signature elements (each in the mock):

- **Corner brackets** on every panel (cyan top-left, violet bottom-right). This needs a small custom frame renderer (about a day of work); the fallback is `border: round`.
- **Aurora rule**: a 1-row gradient line under the top bar whose colours drift slowly.
- **Start-up sequence** of about 2 s when the console opens: link, ledger, market feed, engine and AI supervisor checks, each ending "ok". It doubles as a real connection check, and is skipped with any key or with motion off.
- **AI typewriter**: AI-written text (engine brief, briefs) types in at about 25 chars/s with a violet block cursor, and is marked ✦.
- **Live tick flash** (blue up / orange down, 0.9 s) on every live price change.
- **Screen enter**: 0.22 s fade and 1-row rise when switching screens.
- **Shimmer** across big numbers.
- Not possible in terminals, so not in the design: glow, blur, transparency.

## Motion (all cheap over ssh)

- **Live dot:** cyan, pulsing with a ~1.6 s period via a timer that cycles 3–4 colour steps. **This fixes "live is gray":** `render_heartbeat` styles the whole OK line `dim` (src/hermes/application/console/render.py, OK branch). The dot gets the accent colour; the text can stay muted.
- **Checking:** while a bar is being processed, the dot becomes a braille spinner and the line reads "checking 18:00 bar…".
- **Late / down:** amber ◐ / red row, as today.
- **Bar close:** a toast via `App.notify`, for example "✓ 18:00 checked · no signal · next 22:00". The new activity row slides in with a 2 s highlight fade (`styles.animate("background", …)`). The total counts up.
- **Empty positions:** a small radar spinner plus "hermes is watching BTC ETH SOL and will look again at HH:MM".
- **Motion off:** honour a setting; the MVP2 Settings screen toggles it. In MVP1 an env var is enough (`HERMES_CONSOLE_MOTION=off`).

## Live between bars

The Code Owner, 2026-10-05 12:52: "user can see things moving constantly … open positions can be tracked live".

- The 10 s Kraken ticker drives more than the Market panel: each open position's "now" price, change %, unrealised gross/net, stop-room meter and sparkline, plus the Home portfolio total and the "today" change.
- Show two layers clearly: **live** values (a small pulsing `live` tag, updated every 10 s) and **booked** values (from the ledger at the last bar close, "as of 16:00"). The waterfall, tax figures and Shadow test stay booked-only.
- A price tick flashes the cell briefly: blue for up, orange for down.
- Live values are computed in the console process only. They are never written to the ledger, never change a stop or an exit (the engine still acts only on its own bar-close data), and never reach the supervisor, briefs, Telegram or Slack (ADR-012 D-06, #319). The operator seeing live P&L is already covered by docs/decisions/2026-10-03-operator-sees-shadow-pnl.md.
- If the ticker is stale for more than 60 s, live values dim and the tag reads "live · paused HH:MM:SS"; the booked values stay.

## More live data

The Code Owner, 2026-10-05 12:52: "other things that are static or semi static today". Built by issue #713 (the ticker) and issue #722 (the items below).

All are display-only in the console and follow the same rules as above (never ledger, never stops, never AI or notifications). Sources already in the codebase: `KrakenPublicAdapter.get_ticker` / `get_orderbook`, the journal reader, systemd state.

1. **Forming 4h candle** per coin: the bar being built right now (open, high, low, last) with the time left. Market panel and Positions chart.
2. **Market context** from the ticker: 24 h change, 24 h high/low and volume per coin.
3. **Live exposure**: cash vs invested and the 15% exposure meter at live prices.
4. **Live "tax if sold now"**: the open-positions tax estimate at live prices (labelled estimate, live).
5. **Order book health**: spread in bps and depth per coin every 10 s, plus a live "book fresh / stale" flag (stale blocks new entries, so this explains a skipped entry before the bar closes).
6. **Feed latency**: round-trip time to Kraken and Binance and the age of the last price, as a tiny sparkline in the top bar.
7. **Heartbeat age ticking**: "checked 2m 14s ago", plus a countdown to the next bar and to certification (days, hours).
8. **Live activity stream**: any new journal warning or error appears in Activity as it happens, with a toast, instead of waiting for the 5 s poll.
9. **System live**: cpu, memory and disk meters every 2 s, service uptimes ticking, `F` follow on Logs.
10. **EUR rate live (secondary)**: a Kraken USDT/EUR quote next to the ECB rate. Booked figures keep using ECB (decision: ECB for EUR).
11. **Not recommended, needs the Code Owner's OK:** a live "would the rules fire on the forming bar?" preview. It invites second-guessing the engine between bars, and the pre-registration acts only at bar close.

The Code Owner approved all ten live items on 2026-10-05 12:54 ("we can do all that live perfect"). Item 11 stays out.

## Who produces each analysis (label every panel)

- **Computed by hermes** (deterministic code over the ledger, the journal and the ticker; no AI): every number, chart and yes/no on Home, Signals, Positions, Tax, Shadow test, System and Logs. That includes rule yes/no, counts, waterfall, stop room, FIFO lots and brackets, and the certification checks. Panel label: `computed`.
- **Written by the AI supervisor** (ADR-012 supervision loop; read-only, P&L-blind): the Engine brief panel and the Briefs screen only. They are written from recorded facts, never prices from the test window or P&L. Until issue #365 ships the supervisor desk, the daily review is the stopgap. Panel label: `✦ AI supervisor`, with the note `never sees profit`.
- Nothing on screen is an AI trading opinion, and no AI output feeds the engine (AGENTS.md rule 8, ADR-001).

## Small extras (approved by the Code Owner 2026-10-05 12:55)

- Terminal tab title shows the state at a glance, for example `hermes ● live · €4,456 · next 16:00` (amber or red when late or down).
- Optional terminal bell on down, late or error, off by default.
- Night mode: from 23:00 to 08:00 local, motion slows and colours dim, but alerts still show.

## Refresh model

- The read model is polled every 5 s, as today. The clock, the countdown and the pulse tick every 1 s locally.
- Live ticker: every 10 s, in a worker (`run_worker`, thread) so the UI never blocks. On failure, keep the last price, dim it and show "as of HH:MM:SS". Never raise into the UI.
- After a deploy, show a "new version, restart the console" notice (issue #652).

## Navigation

- Top tab bar: `1 Home · 2 Signals · 3 Positions · 4 Tax · 5 Shadow test · 6 Briefs · 7 System · 8 Logs · 9 History`.
- Number keys jump to a screen, Tab / Shift+Tab cycle through them, Ctrl+P is the command palette (screens and commands), `?` is help, Q quits.
- `D W M Y` sets the range on every screen that has one. This replaces the earlier `1 7 3 Y`, which clash with the screen numbers.
- Esc goes back from detail panes.

## Screens

| # | Screen | MVP | Issue | Panels |
| --- | --- | --- | --- | --- |
| 1 | Home | MVP1 | #711 (shell), #712 (layout) | Top bar (brand, SHADOW · mainnet off, live dot, last bar ✓, next bar + countdown meter, release). Left: Portfolio (big total, change + net, braille curve, cash/invested meters, waterfall Today/This year, open + tax if sold now). Right: Positions (rows with sparkline, change, net, stop-room meter) or the designed empty state. Market (live Kraken price, change, sparkline per coin). Activity (grouped by day, icons). Engine brief (AI supervisor, P&L-blind, yes/no per condition). Bottom: 90-day run gradient bar. Footer chips. |
| 2 | Signals | MVP1 | #714 | Heat grid: one square per 4h bar per coin over the range, coloured by how many of the 3 rules were met (◆ = all 3 = signal). Last-check table: breakout 4h / trend 1D / momentum 4h as yes/no chips, "blocked by" (max positions, exposure cap, correlation lock, stale book, already holding), result. Engine brief for that bar. Week counts: checks, signals, opened, blocked, data late, % of bars each rule was met. The frozen rules (pre-registration §5.1, §5.5). Enter on a row opens that bar. |
| 3 | Positions | MVP1 | #715 | List (open + recently closed). Detail: big net, entry/now/size, "Why it opened" (3 yes chips + blockers none), braille price chart since entry with the trailing-stop step line, "What would close it" (stop hit + room meter in % and ATR, time limit day N of 90, trend break yes/no), money waterfall, timeline. |
| 4 | Tax | MVP1 view; profile editor MVP2 | #719 | Sub-tabs: Summary · Lots · Brackets · Tax pack · Your profile. Summary: realised, fees, taxable base, est. tax (FIFO), open-if-sold-now, two-month-rule flags (BTC/ETH/SOL only), Murcia bracket meters with "€X left before 21%". Lots summary is one row per coin. The full Lots tab scrolls (DataTable), is grouped by month with subtotals, filters (all / open / closed, per coin), has search and sort, and shows the selected lot beside it (FIFO sales that used it, proceeds/cost/fees/gain, two-month rule). Tax pack status: "for your gestor, never submitted to AEAT". Profile masked (S shows, E edit = MVP2 + needs OK). |
| 5 | Shadow test | MVP1 | #718 | Big "N / 90" with a gradient progress bar and month ticks; the certification checks (90 days in a row, M-02 slippage median ≤ 30 bps, no kill trigger, kill-trigger check clean, lab notebook signed), with the note "mechanical: pass or fail stands"; frozen settings; lab notebook entries. |
| 6 | Briefs | MVP1 daily; weekly/monthly MVP2 | #720 | List of briefs plus the selected brief: what the engine did, rules met per coin (counts of yes), health, deviations. Footer: "written by the AI supervisor, never sees profit". |
| 7 | System | MVP1 view; actions MVP2 | #717 | Services (hermes, bot, health, daily, backup, archive check, tax pack: state + timer), Releases (live sha + earlier, deploy steps), Data and timing (market data, order book, EUR rate freshness, bar-check time sparkline), Machine (cpu, memory, disk, ledger size, last snapshot). Actions: MVP1 shows each command to copy plus "run a read-only diagnosis". MVP2 adds keys B/R/K with a typed-confirmation box, the bar-close hold, PC-only, journaled. |
| 8 | Logs | MVP1; saved queries MVP2 | #716 | Query line (`level>=warn service:engine coin:ETH since:7d`, Tab completes fields), facet filters with counts (level, service, coin), volume histogram coloured by level (drag/keys to zoom), sortable columns (time, level, service, event, coin, details, trace), selected line as pretty JSON (copy, export to PC), trace pane with every line of one correlation id as a tree with timings (T jumps there), F follows live. Source: the journal reader behind `hermes logs` (src/hermes/application/console/logs.py). |
| 9 | History | MVP2 | — | Every trade and event since the start, filters, monthly/yearly summaries, CSV export to PC. |
| ? | Help | MVP1 | #711 | Keys, what each screen shows, the read-only promise, the tax footnote. |
| , | Settings | MVP2 | — | Theme, motion on/off, time zone, EUR/USDT, layout presets, key bindings; saved on PC. |

## MVP2 list (for planning, not this build)

VPS actions (needs OK), log extras (saved queries, alerts, compare two bar checks, export), candle/equity charts (needs OK if a chart add-on, issue #732), day replay, history, tax profile editor + on-demand pack + what-if (needs OK), weekly/monthly briefs + Q&A with the supervisor (P&L-blind), alert/Telegram settings (needs OK), backtest/challenger lab view, settings, phone/browser access (needs OK), multi-run compare (issue #731).

## Build order

| Step | Issue | What |
| --- | --- | --- |
| 1 | #711 | Theme, panels, tab bar + navigation, footer chips, live-dot pulse (fixes the gray dot). |
| 2 | #712 | Home layout at full width (follows issue #650) with the Digits gradient, braille curve, meters, designed empty state and the 90-day bar. |
| 3 | #713, #722 | Live Kraken ticker worker + Market panel; then the more-live-data items. |
| 4 | #714 | Signals (needs the per-condition record-only field from PR #659). |
| 5 | #715, #716, #717, #718, #719, #720 | Positions detail, Logs, System (view), Shadow test, Tax (view + Lots table), Briefs. |
| 6 | #721 | Motion polish: start-up sequence, bar-close toast, row slide-in, count-up, checking spinner, AI typewriter. |
| — | #729, #730, #731 | Shared query bar, drill-down, run-aware console (used by the screens above as they land). |
| — | #732, #733 | The approved chart and image dependencies, one PR each. |

Tests: Textual `run_test` snapshots per screen at 80x24, 120x36, 160x45 and 240x60, plus the existing plain-text assertions. Mocks are not tests.

## Round 3 decisions and design (the Code Owner, 2026-10-05, 13:00–14:34 UTC)

### Approvals

- All MVP2 ideas: approved (13:14).
- "Needs your OK" list approved (13:16): chart add-on (`textual-plotext`, issue #732), image support (`textual-image`, issue #733), live prices. Any new library is still added by its own PR with a version pin.
- Look: **A, with C's best parts** (13:18): per-coin braille charts in Market, risk meters (exposure vs the 15% cap) in Portfolio.
- Two themes from the same tokens: **Neon** (retro-futurist, vibrant; the Code Owner's reference 14:33) and **Control room** (calmer). Recommended: Neon by default, with night mode (23:00–08:00) switching to Control room. Data colours stay semantic in both: up blue, down orange, warning amber. Neon only restyles the chrome: magenta→violet→cyan title bars, cyan/magenta bracket corners, and deep indigo surfaces (#0B0420 / #120731 / #170A3D). Every theme (Neon, Control room, night) opens with the window bar: one row of that theme's `chrome` gradient with no characters on it (Code Owner 2026-10-17, issue #1097). What the Neon bar used to carry lives on the status row below it: the mainnet badge leads that row in every theme; the live-feed pulse (one glyph per recent ticker poll, 16 wide, newest on the right: "●" in the live colour for a good poll, "○" dim for a failed one; the newest brightens briefly on each poll, not with motion off; Code Owner 2026-10-06) and the local clock (`HH:MM:SS`) sit before the theme chip and take only spare cells, the clock dropping first on a narrow row. The next-bar countdown that the bar showed without a ticker is gone: the status row's countdown already says it.
- Pushback recorded: no scanlines, blur or glow, and no neon colours on the data itself. The console is read all day and holds money figures, so legibility wins.

### Neon theme tokens (overrides on top of the base tokens)

`void` #0B0420 · `panel` #120731 · `panel-focus` #170A3D · `line` #4A2399 · `live` #00F0FF · `ai` #FF3DF0 · `frame` #8A5CFF · `text` #C8FBFF · `muted` #8FA8D8 · `dim` #5A5F9A. Gradients: `chrome` #FF3DF0→#8A5CFF→#00F0FF; `brand` #00F0FF→#8A5CFF→#FF3DF0.

### Reusable components (one implementation each, all styled only through tokens)

| Component | Used by | Issue |
| --- | --- | --- |
| `HudPanel` (frame, title bar, subtitle, focus state, AI variant) | every panel | #711 |
| `QueryBar` (free-text query, facet dropdowns, sort, result count) + `FacetFilter` + `SortableTable` | Lots, Briefs, History, Logs, Releases, Signals bar list, trade timeline | #729 |
| `DetailScreen` (breadcrumb, back, previous/next, footer) | every drill-down that needs a full screen | #730 |
| `DetailPane` (inline right-hand detail) | rows whose detail fits beside the list | #730 |
| `BigNumber`, `Meter`, `Sparkline`, `AreaChart` (braille), `RingGauge` (braille), `HeatGrid` | figures and charts | #712, #714 |
| `Chip`, `KeyHint`, `StatusDot`, `LiveTag`, `Toast`, `ConfirmBox`, `EmptyState`, `Timeline`, `Breadcrumb`, `TruncatedText` | everywhere | as needed |

Rule: if two screens need the same thing, they use the same component. Never build a second variant; extend the component.

Shared facets: **year, month, day, coin, kind/state/level** (the Code Owner 13:07 "filtering by year, month, day, coin"). The query syntax is the same everywhere: `field:value`, `field>=value`, free text, `since:7d`. Tab completes field names, and sort is chosen by clicking a column header or pressing S.

### Drill-down rules (issue #730)

- Enter opens. If the detail fits, it opens as a `DetailPane` beside the list (lot detail, brief text, log line). If it needs room, it opens as a `DetailScreen` (a trade's full event history, a release, a bar check, a brief at full size on compact terminals).
- In a `DetailScreen`, the top bar becomes a breadcrumb (`◂ System › Releases › 2bc2798`). Esc or Backspace goes back one level and keeps the list's filters, sort and cursor. `[` / `]` go to the previous / next item without going back. Number keys still jump between screens. `Ctrl+P` searches everything.
- Long text truncates with "…" at the panel edge (`text-overflow: ellipsis`), with a "+N" count when a list is cut. The full text is always one Enter away.

Inventory:

| From | Opens | As |
| --- | --- | --- |
| System › Releases row | release detail: title, PRs, changelog, deploy steps with timings, rollback target, health since | DetailScreen |
| History row | the trade's full story: bar, signal, fill, stop moves, warnings, live state | DetailScreen |
| Positions row | position detail | DetailPane (wide) / DetailScreen (compact) |
| Signals bar cell or row | that bar check: rules, blockers, trace, brief | DetailScreen |
| Lots row | lot FIFO detail | DetailPane |
| Briefs row | brief | DetailPane (wide) / DetailScreen (compact) |
| Logs line | JSON + trace | DetailPane; T opens the trace as a DetailScreen |
| Activity row (Home) | jumps to History detail | DetailScreen |
| Services row | the unit's status + recent journal lines | DetailPane |

### Responsiveness (the Code Owner 14:32)

Before this redesign there were two layouts: below 120 columns everything stacks; from 120 columns there are two columns. The big total shows from 30 rows. Breakpoint tokens, using Textual 8's built-in `HORIZONTAL_BREAKPOINTS` / `VERTICAL_BREAKPOINTS` (checked: present in the pinned version):

| Width | Layout |
| --- | --- |
| compact < 100 cols (80×24 ssh, phone) | one column; tabs show numbers only; charts become sparklines; detail panes become screens |
| standard 100–159 | two columns, as today |
| wide 160–219 | the mock layout |
| ultra ≥ 220 (4K, ultrawide) | adds a third column: Signals heat grid and a live log tail on Home |

Height: < 30 rows hides the curve and keeps the figures; ≥ 45 rows doubles the chart heights. Snapshot tests at 80×24, 120×36, 160×45 and 240×60.

### Multi-strategy shadow tests (the Code Owner 13:08, issue #731)

Recommendation: **one console that is run-aware, which can also be opened once per run.**

- A run selector in the top bar (`RUN swing-donchian-4h ▾`, `Ctrl+R`). Every money, signal, position, brief and shadow-test view is scoped to the selected run. System and Logs are shared, with a `run:` facet.
- A new **0 Runs** overview screen: one row or card per run, showing status, day N of 90, open positions, trades, net (operator only) and certification checks, plus side-by-side compare.
- `hermes tui --run <name>` pins a console to one run. This gives the operator "multiple consoles" (for example split panes in Windows Terminal) without building two apps.
- Each run keeps its own pre-registration and its own blind for the supervisor. The console never mixes runs in one total unless asked (`run:all` facet).

### TUI library (the Code Owner 14:32)

- hermes uses **Textual** (Python, with Rich underneath). It was already the console's toolkit before this redesign, and it is the most capable modern TUI framework in Python. It offers CSS-like styling, themes, animation, async workers, responsive breakpoints, a command palette, snapshot testing, and `textual-serve` to show the same app in a browser (the MVP2 phone/browser access item).
- Flashier toolkits exist in other languages: Ratatui (Rust) with shader-like effect add-ons, Bubble Tea + Lip Gloss (Go), and newer TypeScript/Zig frameworks. Using any of them means rewriting the console outside the Python codebase and its tests. Not recommended.
- To push the look within Python: `textual-image` (pictures, approved, issue #733), `textual-plotext` (charts, approved, issue #732), and, needing the Code Owner's OK, a terminal text-effects library for the start-up sequence (decrypt, matrix and beam reveals).
