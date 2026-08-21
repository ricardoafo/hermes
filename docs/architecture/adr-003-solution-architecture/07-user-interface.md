# 07 — User Interface (CLI + Interactive TUI)

**Parent ADR**: [README.md](./README.md)
**Status**: Accepted
**Accepted**: 2026-08-21

## Question this section answers

How does the operator interact with hermes? What commands exist? What does the interactive interface look like? Why a full Textual TUI (not just a web dashboard, not just Rich commands)? How do we ensure it's beautiful, accessible, and works over mobile SSH?

## Design principles (locked)

1. **CLI-first, dashboard deferred** — Textual TUI for MVP; Streamlit + Tailscale dashboard optional Phase 5
2. **Operator console, not viewer** — write actions (rebalance, kill swing, ack alert) with typed confirmations, NOT read-only monitoring. FTUI's biggest user complaint is being monitoring-only — we don't repeat that.
3. **CVD-safe palette** (Okabe-Ito: blue + orange + vermillion, NOT red + green) — triple encoding: hue + glyph + sign. Roughly 1 in 12 men have red-green CVD; also breaks under grayscale SSH / screenshot paste.
4. **Locale-aware** — es-ES currency (`1.234,56 €`) via `babel.numbers`, never `f"{x:,.2f} €"`
5. **Time semantics** — relative <7d ("2m ago"), absolute UTC beyond. Trade ledger + tax export ALWAYS UTC absolute (unambiguous for Hacienda)
6. **Same read path, two presentations** — CLI and TUI both go through `LedgerPort(read_only=True)`
7. **Mobile-first responsive** — designed at 60×20 (Termius portrait), scales up to desktop via `on_resize` breakpoints
8. **Kill-switch discipline** — dry-run default for write commands, typed confirmations, `Ctrl+K` double-confirm for destructive actions

## Two modes, two tools

| Mode                                | Tool           | When                                               |
| ----------------------------------- | -------------- | -------------------------------------------------- |
| **CLI** (one-shot, exit)            | Typer + Rich   | Scripting, cron, quick status, tax exports         |
| **TUI** (interactive, long-running) | Textual + Rich | Daily operator use, monitoring, rebalance triggers |

Both consume the **same** `LedgerPort` read path. Zero duplicated read logic.

---

## Part A — CLI (Typer + Rich, one-shot commands)

### Command catalog (MVP)

```text
$ hermes --help
Usage: hermes [COMMAND]

Commands:
  status        One-shot portfolio snapshot
  watch         Live-updating status (rich.live.Live)
  trades        Recent trades table (with filters)
  positions     Open swing positions
  tax           YTD gains, bracket, FIFO lot inventory
  health        Engine heartbeats, systemd status, backup age
  config show   Current YAML config (read-only)
  report        Generate weekly/monthly/yearly report
  ledger export CSV / Modelo 100 export
  reb dry       Rebalance dry-run (no orders placed)
  reb exec      Execute rebalance (typed confirmation)
  swing close   Close a swing position (typed confirmation)
  tui           Launch interactive TUI
```

### Rich output patterns

```python
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from babel.numbers import format_currency

console = Console(theme=HERMES_THEME)   # project-wide palette

# hermes status
table = Table(title="Open positions", box=None, show_lines=False)
table.add_column("Symbol", style="primary", no_wrap=True)
table.add_column("Qty", justify="right")
table.add_column("PnL", justify="right")

for pos in positions:
    glyph = "▲" if pos.pnl >= 0 else "▼"
    style = "success" if pos.pnl >= 0 else "error"
    pnl_str = format_currency(pos.pnl, "EUR", locale="es_ES")
    table.add_row(pos.symbol, f"{pos.qty:.4f}", f"[{style}]{glyph} {pnl_str}[/]")

console.print(Panel(table, title="hermes", border_style="primary"))
```

### Live watch (`rich.live.Live`)

```python
def hermes_watch(interval_sec: float = 2.0):
    with Live(build_status_table(), refresh_per_second=1/interval_sec, screen=False) as live:
        while True:
            time.sleep(interval_sec)
            live.update(build_status_table())
```

**Data source**: SQLite polling. Never opens its own WebSocket (swing daemon owns that; CLI reads what daemon has already stored).

### Progress bar for backtests

```python
with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TextColumn("{task.completed}/{task.total}"),
    TimeRemainingColumn(),
) as prog:
    task = prog.add_task("Backtesting Donchian on BTC", total=len(candles))
    for candle in candles:
        run_bar(candle)
        prog.advance(task)
```

### Distribution

```bash
uv tool install --from git+https://github.com/ricardoafo-org/hermes hermes
# → hermes ends up in ~/.local/bin/
```

No PyInstaller/Nuitka. VPS has Python 3.11 via `uv`. Overkill for solo/self-deployed use.

---

## Part B — TUI (Textual, interactive operator console)

### App skeleton

```python
# src/hermes/tui/app.py
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import Screen
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, DataTable, Digits, RichLog, Sparkline

from hermes.tui.theme import hermes_dark

class HermesApp(App):
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "switch_mode('dashboard')", "Dashboard"),
        Binding("p", "switch_mode('portfolio')", "Portfolio"),
        Binding("s", "switch_mode('swings')", "Swings"),
        Binding("l", "switch_mode('ledger')", "Ledger"),
        Binding("t", "switch_mode('tax')", "Tax"),
        Binding("e", "switch_mode('events')", "Events"),
        Binding("h", "switch_mode('health')", "Health"),
        Binding("c", "switch_mode('config')", "Config"),
        Binding("ctrl+k", "kill_switch", "Kill", priority=True),
        Binding("ctrl+d", "toggle_dense", "Dense mode"),
    ]

    MODES = {
        "dashboard": "hermes.tui.screens.dashboard.DashboardScreen",
        "portfolio": "hermes.tui.screens.portfolio.PortfolioScreen",
        "swings":    "hermes.tui.screens.swings.SwingsScreen",
        "ledger":    "hermes.tui.screens.ledger.LedgerScreen",
        "tax":       "hermes.tui.screens.tax.TaxScreen",
        "events":    "hermes.tui.screens.events.EventsScreen",
        "health":    "hermes.tui.screens.health.HealthScreen",
        "config":    "hermes.tui.screens.config.ConfigScreen",
    }
    DEFAULT_MODE = "dashboard"

    portfolio_value: reactive[float] = reactive(0.0)
    positions: reactive[list] = reactive(list, always_update=False)

    def __init__(self, ledger: LedgerPort):
        super().__init__()
        self.ledger = ledger

    def on_mount(self) -> None:
        self.register_theme(hermes_dark)
        self.theme = "hermes-dark"
        self.set_interval(2.0, self.poll_state)   # 2Hz — smooth, CPU-friendly
        self.poll_state()

    def poll_state(self) -> None:
        # SQLite read is fast (µs) — safe on the event loop
        self.positions = self.ledger.list_positions()
        self.portfolio_value = self.ledger.portfolio_value_eur()

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < 80, "-compact")

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        yield SystemCommand("Rebalance now", "Threshold check + execute if breach", self.action_rebalance)
        yield SystemCommand("Export tax report", "CSV to ~/hermes-tax.csv", self.action_export_tax)
        yield SystemCommand("Close swing position", "Interactive picker + confirm", self.action_close_swing)

    def action_kill_switch(self) -> None:
        self.push_screen(KillSwitchModal(), self._handle_kill)

if __name__ == "__main__":
    HermesApp(ledger=SqliteLedger(read_only=True)).run()
```

### Screen list (9 screens, single-key nav)

| Key | Screen              | Purpose                                                                                                | MVP?     |
| --- | ------------------- | ------------------------------------------------------------------------------------------------------ | -------- |
| `D` | **Dashboard**       | Total EUR (Digits widget), 24h P&L (sparkline), drift status glance, open swing count, latest 5 events | Yes      |
| `P` | **Portfolio**       | Rebalancer view — weights vs targets with drift band bars, simulate + execute                          | Yes      |
| `S` | **Swings**          | Donchian positions table with stop distance, EUR risk, days held, MFE sparkline                        | Yes      |
| `L` | **Ledger**          | All trades, filter by engine/asset/date                                                                | Yes      |
| `T` | **Tax lots**        | FIFO inventory per asset, realized YTD, savings-base bracket estimate                                  | Yes      |
| `E` | **Events / Alerts** | Log-stream of alerts (breakout, band-breach, stop-hit, heartbeat lag)                                  | Yes      |
| `H` | **Health**          | Engine heartbeats, systemd unit status, DB size, last backup timestamp                                 | Yes      |
| `C` | **Config**          | Current YAML config (read-only in MVP)                                                                 | Yes      |
| `?` | **Help**            | Bindings + command list                                                                                | Yes      |
| `B` | **Backtest**        | Trigger + view backtest results                                                                        | Phase 1+ |
| `M` | **Market**          | Live prices + sparklines                                                                               | Phase 2+ |

### Command palette (`Ctrl+P`) — Bloomberg-style

Fuzzy-match verbs registered via Textual's `SystemCommand` API:

```text
> reb dry          → Rebalance dry-run
> reb exec         → Execute rebalance (typed confirm)
> swing close LINK → Close LINK swing at market (typed confirm)
> lots btc 2024    → BTC FIFO lots from 2024
> tax ytd          → Show YTD realized gains
> alerts ack       → Ack all alerts
> health           → Health screen
> kill             → Kill switch (double confirm)
```

Beats memorizing 30,000 Bloomberg function codes with a fuzzy palette.

### Mockup — Dashboard screen

```text
╔═══ hermes ══════════════════════════ 2026-08-20 14:32 UTC ═══════╗
║                                                                    ║
║   ┌─── PORTFOLIO ────────────┐  ┌─── DRIFT ──────────────────┐   ║
║   │                          │  │ BTC   64,2 %  (60,0 %)  ▲   │   ║
║   │      1̌2̌.̌8̌4̌7̌,̌5̌0̌  €           │  │ ETH   26,8 %  (30,0 %)  ▼   │   ║
║   │      ▲ +1,23 %  24h      │  │ USDC   9,0 %  (10,0 %)  ▼   │   ║
║   │                          │  │ Status: OUT-OF-BAND (BTC)   │   ║
║   │ 24h P&L  ▁▂▄▆█▇▆▅▃▂▁     │  │ Last reb: 12d ago           │   ║
║   └──────────────────────────┘  └────────────────────────────┘   ║
║                                                                    ║
║   ┌─── OPEN SWINGS (3) ──────────────────────────────────────┐   ║
║   │ ETH   0,04  entry 3.650€  stop 3.830€  ▲ +4,9%  9d      │   ║
║   │ SOL   0,50  entry 142€    stop 155€    ▲ +18,3% 14d      │   ║
║   │ LINK 40,00  entry 12,4€   stop 11,9€   ▲ +2,1%  2d      │   ║
║   └──────────────────────────────────────────────────────────┘   ║
║                                                                    ║
║   ┌─── LATEST EVENTS ────────────────────────────────────────┐   ║
║   │ 2m ago   REBAL   Cycle OK (no action) BTC drift 4,2 pp   │   ║
║   │ 9d ago   SWING   ETH entry at 3.650€ (55d breakout+ADX)  │   ║
║   │ 12d ago  REBAL   BTC sell 0,001 (drift 12,1% → 60,3%)    │   ║
║   └──────────────────────────────────────────────────────────┘   ║
║                                                                    ║
╚═══[D]ash [P]ort [S]wing [L]edg [T]ax [E]vt [H]lth [Ctrl+P] [Q]uit═╝
```

### Mockup — Portfolio pane (rebalancer)

```text
PORTFOLIO           Total: 12.847,50 €    24h ▲ +1,23%    Last reb: 12d ago
──────────────────────────────────────────────────────────────────────────
Asset   Target  Current  Drift    EUR      Δ EUR      Band            Action
BTC      60,0%   64,2%   +4,2 pp  8.248€  -540€    [====|--]  SELL 540 €
ETH      30,0%   26,8%   -3,2 pp  3.443€  +412€    [==|-===]  BUY  412 €
USDC     10,0%    9,0%   -1,0 pp  1.156€  +128€    [===|===]  within band
──────────────────────────────────────────────────────────────────────────
Band: ±10%  ·  Status: OUT-OF-BAND (BTC)   [R] simulate  [X] execute  [Esc]
```

Drift in **percentage points** (`pp`) — reduces "0.4% of 30% = ?" ambiguity. Band as ASCII gauge with band edges marked — instant OK/breach read.

### Mockup — FIFO tax lots (BTC)

```text
FIFO LOTS — BTC (0,1852 BTC)     Cost basis: 4.218,32 €    Unreal: +892,17 €
──────────────────────────────────────────────────────────────────────────
#   Acquired          Qty       Price EUR    Cost EUR    Now EUR    PnL EUR   Age
1   2023-04-12   0,05000000   24.500,00€   1.225,00€   1.470,00€   +245,00€  680d
2   2024-01-08   0,08000000   31.200,00€   2.496,00€   2.352,00€   -144,00€  411d
3   2025-11-30   0,05520000   35.800,00€   1.976,16€   1.622,88€   -353,28€   56d
──────────────────────────────────────────────────────────────────────────
Realized YTD (savings base): +1.240,50 €   Est. tax @19-28%: 235 – 347 €
```

### Data flow: SQLite polling via `set_interval` (Pattern A)

```python
class DashboardScreen(Screen):
    positions: reactive[list] = reactive(list, always_update=False)

    def on_mount(self) -> None:
        self.load_data()
        self.set_interval(2.0, self.load_data)   # 2Hz — perceptibly live

    def load_data(self) -> None:
        # SQLite read is fast — safe on event loop
        self.positions = self.app.ledger.list_positions()

    def watch_positions(self, new: list) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for p in new:
            table.add_row(p.symbol, f"{p.qty:.4f}", f"{p.pnl:+.2f}")
```

**Golden rules** (from Track 5):

- NEVER mutate reactives from a thread worker → use `call_from_thread`
- Reactives on `App` subclass; child widgets `data_bind(App.attr)` — no global store
- 2Hz is "imperceptibly live" for portfolio data; don't chase 60fps
- WebSocket-in-TUI is unnecessary: swing daemon owns WS, TUI polls DB

### Write actions — dry-run + typed confirmation

```python
class ExecuteRebalanceModal(ModalScreen[bool]):
    """Blocks input until user types 'CONFIRM' or cancels."""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Execute rebalance?", classes="modal-title"),
            Static("SELL 0,001 BTC  →  BUY 0,006 ETH"),
            Static("Estimated fee: 0,08 €   Realized gain: 12,50 €"),
            Static("Tax impact: +2,37 € (still 19% bracket)"),
            Input(placeholder="Type CONFIRM to execute", id="confirm-input"),
            Horizontal(
                Button("Cancel", variant="default", id="cancel"),
                Button("Execute", variant="error", id="execute", disabled=True),
            ),
        )

    @on(Input.Changed, "#confirm-input")
    def _validate(self, event: Input.Changed) -> None:
        self.query_one("#execute", Button).disabled = event.value != "CONFIRM"
```

Every write path requires this. **Dry-run is the DEFAULT**; explicit typing to arm the button.

## Theme — Okabe-Ito palette (CVD-safe)

```python
# src/hermes/tui/theme.py
from textual.theme import Theme

hermes_dark = Theme(
    name="hermes-dark",
    dark=True,
    primary="#0072B2",       # blue — accent, actions
    success="#009E73",       # bluish-green — gains (distinguishable from red)
    warning="#E69F00",       # orange — warnings, deferred rebalance
    error="#D55E00",         # vermillion — losses, kill-switch (NOT pure red)
    accent="#CC79A7",        # reddish-purple — highlights
    surface="#1e1e1e",
    panel="#2a2a2a",
    background="#101010",
    foreground="#EEEEEE",
)
```

**Never write raw hex** in widget code — always use `$primary` / `$success` / `$error` CSS variables. Theme controls everything.

**Triple-encode always**:

```text
BTC   +2,34 %  ▲   ← hue AND glyph AND sign
ETH   -1,12 %  ▼
USDC   0,00 %  ·
```

Never rely on color alone. Works under grayscale SSH, screenshot paste, and CVD.

## Testing — `pytest-textual-snapshot` + `Pilot`

```python
# tests/tui/test_dashboard.py
import pytest
from hermes.tui.app import HermesApp

@pytest.mark.asyncio
async def test_dashboard_shows_positions(tmp_ledger):
    tmp_ledger.seed_positions([("BTC", 0.5, 120.0)])
    app = HermesApp(ledger=tmp_ledger)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 1

@pytest.mark.asyncio
async def test_execute_rebalance_requires_confirm():
    async with HermesApp().run_test() as pilot:
        await pilot.press("p", "x")   # portfolio, then execute
        await pilot.press("W", "R", "O", "N", "G", "enter")
        # button remains disabled
        assert app.query_one("#execute", Button).disabled

# Snapshot test — visual regression
def test_dashboard_snapshot(snap_compare):
    assert snap_compare(
        "src/hermes/tui/app.py",
        press=["d"],
        terminal_size=(120, 40),
    )
```

SVG snapshots committed to git under `tests/__snapshots__/`. Run `pytest --snapshot-update` after intentional visual changes. **CI runs headless** on GitHub Actions `ubuntu-latest`.

Annotate SEV-1 paths per the severity rubric:

```python
# SEV-1: F5 — double-book prevention for concurrent rebalance requests
async def test_execute_rebalance_is_idempotent():
    ...
```

## Deployment — SSH + mosh + tmux + Termius

Local:

```bash
uv sync
uv run hermes tui
```

VPS deploy:

```bash
uv tool install --from git+https://github.com/ricardoafo-org/hermes hermes
# → hermes in /home/hermes/.local/bin/
```

Mobile access stack (per Track 5):

```text
Termius (iOS / Android)
   └── mosh                    ← survives cellular↔wifi handoffs, laggy links
        └── tmux (server-side) ← survives SSH disconnects
             └── hermes tui
```

`mosh` uses UDP + server-side buffering → keyboard feels instant even at 200ms latency. `tmux` means the TUI survives disconnects. Termius supports mosh 1.3+.

**Terminal compatibility (2026)**:

- **Excellent**: WezTerm, Kitty, Alacritty, Ghostty, iTerm2, Windows Terminal, Termius
- **Good with tweaks**: macOS Terminal.app — box characters render poorly; recommend Menlo font or switch to Ghostty
- **Fonts**: no Nerd Font strictly required; Nerd Fonts unlock nicer icons if desired

## Accessibility + responsive design

**Responsive breakpoints**:

```python
def on_resize(self, event: events.Resize) -> None:
    self.set_class(event.size.width < 80, "-compact")
    self.set_class(event.size.width < 60, "-mobile")
```

```css
/* app.tcss */
Screen.-compact #sidebar { display: none; }
Screen.-mobile DataTable { padding: 0; }
Screen.-mobile #events { max-height: 5; }
```

Test each layout at both extremes via `pilot`'s `size=` param.

**Safe glyphs** (render across all terminals): arrows (↑↓←→), block chars (█▓▒░), box-drawing (─│┌┐└┘). Avoid emoji flags, complex ZWJ sequences, skin-tone modifiers.

**Screen readers**: Textual TUIs aren't screen-reader-friendly. Fallback is the Rich CLI commands (`hermes trades`, `hermes tax`) which output plain tables that screen readers can parse.

## Kill-switch (`Ctrl+K`)

```python
class KillSwitchModal(ModalScreen[str]):
    """Requires typed 'KILL' to arm, then second Enter to fire."""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("KILL SWITCH", classes="modal-title-danger"),
            Static("This will:"),
            Static("  • Pause hermes-swing.service"),
            Static("  • Cancel all open stop-losses"),
            Static("  • Halt the rebalancer timer"),
            Static("  • Trigger Telegram alert"),
            Input(placeholder="Type KILL to arm", id="kill-input"),
            Horizontal(
                Button("Cancel", variant="default"),
                Button("FIRE", variant="error", id="fire", disabled=True),
            ),
        )
```

Hummingbot's "double `Ctrl+C` to exit" convention applied to nuclear actions.

## Anti-patterns to avoid (from Track 5)

1. **Calling `.refresh()`** when a reactive would do it → wastes cycles, breaks the model
2. **Deep widget nesting** (>10 layers) → sluggish CSS resolution
3. **Reading DB inside `render()` or `compose()`** → must be pure; read in `on_mount` + `set_interval`
4. **Hardcoded colors** instead of `$primary` / `$success` → breaks themes
5. **Threaded workers touching UI directly** → race conditions, crashes; use `call_from_thread`
6. **`DataTable` inside `TabPane` without `height: 1fr`** → invisible table
7. **Long handlers without `asyncio.create_task`** → blocks message pump
8. **Reactive mutations without `mutate_reactive(cls.attr)`** → watchers don't fire

## Alternatives considered

| Option                                      | Why rejected                                                              |
| ------------------------------------------- | ------------------------------------------------------------------------- |
| **Streamlit dashboard as MVP**              | Slower to MVP, phone works via Termius+mosh, deferred to Phase 5 optional |
| **Rich-only CLI (no TUI)**                  | User is engaged operator (Q7); TUI adds huge value for daily use          |
| **`textual-serve` browser mode as primary** | Adds complexity; Tailscale SSH is simpler and works fine                  |
| **PyInstaller / Nuitka distribution**       | Overkill — VPS has Python; user IS the operator                           |
| **Nerd Fonts required**                     | Portability cost > icon benefit; use safe Unicode                         |
| **prompt_toolkit (Hummingbot style)**       | Textual gives DataTable / Sparkline / reactives for free                  |
| **Full FreqUI-style browser dashboard**     | 4GB RAM footprint; Tailscale + Streamlit later is lighter                 |

## Setup order (recommended build sequence)

1. **CLI foundation** — Typer scaffold, `hermes status` returning fake data, Rich theme in place (~2 hrs)
2. **LedgerPort read implementations** so both CLI and TUI have data to display (~2 hrs)
3. **CLI commands** — `status`, `trades`, `tax`, `positions`, `health` (~4 hrs)
4. **CLI live watch** — `hermes watch` via `rich.live.Live` (~1 hr)
5. **TUI app skeleton** — `HermesApp` with `MODES`, one screen (Dashboard) working (~3 hrs)
6. **Dashboard screen** — Digits + Sparkline + DataTable, real data via `set_interval` (~4 hrs)
7. **Screens 2-9** — Portfolio, Swings, Ledger, Tax, Events, Health, Config (~8 hrs)
8. **Command palette + keybindings** — registered SystemCommands (~2 hrs)
9. **Write action modals** — Rebalance execute, Swing close, Kill switch (~4 hrs)
10. **Snapshot tests** for each screen + modal (~4 hrs)
11. **Responsive breakpoints** — verify at 60x20 mobile, 120x40 desktop, 200x60 wide (~2 hrs)

Total estimate: **~36 hrs of focused work** for a beautiful, production-ready TUI. Roughly 1 developer-week.

## Cross-references

- [01-code-organization.md](./01-code-organization.md) — where `tui/` and CLI entrypoints live in the src tree
- [02-data-flow.md](./02-data-flow.md) — what data flows through the same `LedgerPort` for both CLI and TUI
- [03-persistence-schema.md](./03-persistence-schema.md) — SQLite tables that the reads come from
- [06-notifications.md](./06-notifications.md) — Telegram alerts pair with TUI events pane
- [09-deployment.md](./09-deployment.md) — systemd + Tailscale + mosh + tmux stack
- Research: `hermes/research/track-4-tui-prior-art` + `hermes/research/track-5-textual-technical` (engram)
