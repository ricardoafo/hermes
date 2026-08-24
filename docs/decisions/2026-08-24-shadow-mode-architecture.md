# Shadow-mode foundation — HERMES_SHADOW flag + adapter-layer order interception

**Date**: 2026-08-24
**Author**: claude on behalf of Ricardo
**Related**: MVP1 status (#141), issue #175, ADR-003 §04 (config surface),
`docs/decisions/2026-08-23-credential-storage-layered-by-env.md` (three-tier
env vars), `docs/decisions/2026-08-23-binance-testnet-klines.md` (read-only
adapter pattern this ADR extends to writes).

## Decision

MVP1 ships hermes in SHADOW MODE against real markets. A single root-level
config flag, `HERMES_SHADOW`, gates whether order-submit operations reach
the wire. Read paths (klines, orderbook, balance, ticker) always hit the
exchange — the strategy engine sees real market data and generates real
signals — but every WRITE (`place_market_order`, `place_limit_order`,
`cancel_order`, future `withdraw`/`transfer`) is intercepted by a
`ShadowGate` middleware wrapper around the exchange adapter and short-
circuits before touching the exchange. The intercepted call fabricates a
realistic synthetic fill from the current live orderbook and emits the
same `TRADE_EXECUTED` domain event that a live fill would, tagged
`shadow=true`. Ledger, tax guard, notifications, dashboards, and every
downstream consumer run their normal code path — they see a
`TradeExecuted` event; the `shadow` flag lets them tag output or filter
aggregates but never branches business logic.

Real money requires THREE gates set simultaneously:

1. `HERMES_SHADOW=false`
2. `HERMES_ENV=prod`
3. `HERMES_ALLOW_MAINNET=yes`

Missing any one → shadow. Set incompatibly (e.g. `HERMES_SHADOW=false` in
`dev`) → pydantic `model_validator` raises at load time AND
`env_gate.require_live` raises at adapter construction (belt-and-
suspenders across two axes).

**Locked design points (operator + claude, 2026-08-24)**:

| # | Question | Decision |
| --- | --- | --- |
| D1 | How is order-submit intercepted? | Middleware wrapper: `ShadowGate(<concrete ExchangePort>)` implements the same `ExchangePort` and delegates read methods; write methods short-circuit. **Venue-agnostic**: wraps Binance OR Kraken with zero code changes (relevant because we may swap venues if Binance blocks EU users). |
| D2 | What does a shadow write return? | A realistic synthetic `OrderResult` computed from a snapshot of the live orderbook taken at execute-time. **Fee model is venue-agnostic**: `ShadowGate` calls `wrapped.get_fee_schedule(pair)` (the `FeeSchedulePort` shipped with the Kraken adapter, PR #193) — never hardcodes Binance rates. Slippage walks the book from the top; partial fills possible if the requested qty exceeds book depth at the limit price. **BNB-burn is a Binance-only concern**: guard lives in `binance_startup_guard.py`, NOT inside `shadow_gate.py`. MVP1 hard-fails at BNB balance > 0 or `BNB_BURN_ENABLED=true`; the BNB-in-BNB FEE ledger row is MVP2. |
| D3 | How does `TradeExecuted` surface shadow? | Extend the event with `shadow: bool = False`. Bump `__event_schema_version__` 1 → 2. Migration test proves v1 events still parse. |
| D4 | Where is the three-gate rule enforced? | Both. Config-time: `HermesConfig.model_validator` refuses incompatible combos. Runtime: `env_gate.require_live()` is called by every write path before the wire. |
| D5 | How do notifications signal shadow? | Explicit `SHADOW` prefix in the tag line: `🔵 TRADE EXEC · SHADOW · bought 0.5 BTC @ 65,250 USDT`. Colour blue/gray, never red. |
| D6 | Does `hermes health` show mode? | Yes — prominent header. Blue/gray for SHADOW, RED for LIVE. Fails loud when gates are half-configured. |
| D7 | How is the LIVE path tested? | `sandbox` env with `HERMES_SHADOW=false` against Binance TESTNET — fake balance, real signed requests. Never against mainnet in CI. |
| D8 | What is the kill switch? | `HERMES_SHADOW=true` in `/etc/hermes.env` + `systemctl restart hermes`. No hot-reload — a process restart is the audit trail. |
| D9 | Where does shadow state persist across restarts? | **Two separate SQLite files** — `hermes.db` (live) + `hermes-shadow.db` (shadow). Chosen at connection time by config: `SHADOW=true → hermes-shadow.db`. Freqtrade path (PR #2697). Zero cross-contamination possible; one code path, different file. Rejects the alternative "single table with `shadow BOOL` column" as an anti-pattern (aggregation footguns, index bloat, unique-constraint collisions, migration pain). |
| D10 | How is time modelled for shadow vs backtest vs live? | Inject a `ClockPort` into `ShadowGate` (and every fill-model/order-execution component). Shadow + live pass `WallClock()` (`datetime.now(tz=UTC)`). Backtest passes `ReplayClock(bar.timestamp)`. Never call `datetime.now()` directly inside the execution path — it silently corrupts backtest timestamps. |
| D11 | What is the shadow limit-order timeout? | Global `HERMES_SHADOW__UNFILLED_TIMEOUT_HOURS = 24`. Uncrossable-at-placement limit orders are rejected immediately (`OrderRejected(reason="unfillable at limit in shadow")`); resting-limit-book simulation is deferred. The 24h timeout guards against future features that queue orders — for MVP1 with reject-on-placement, the timeout is a safety net, not a live path. Per-strategy override deferred until the second strategy needs a different value. |

## Context

Operator direction 2026-08-24 (mid-session correction of prior MVP scoping):

> Almost all of MVP2 is supposed to be in MVP1 — we want the product
> working with shadow testing and backtesting of the markets. MVP2 flips
> the shadow flag off, adds the Web UI, adds warm-standby resilience. But
> the product has to be OPERATING against real markets in MVP1.

This changes the target: MVP1 is no longer "domain + a CLI that could
trade if wired." It is a full pipeline running on the Hetzner VPS against
live Binance market data, generating real signals, executing them
synthetically, and emitting the same events + notifications that
production will. Every layer except the very last mile (the actual
`POST /api/v3/order` HTTP call) is battle-tested before we flip to live.

Constraints:

- The wiring for adapter interception must NOT be per-adapter boilerplate.
  Every future exchange (Kraken, Coinbase, …) gets shadow behaviour for
  free via the same wrapper.
- Shadow behaviour must be indistinguishable from live to every layer
  ABOVE the adapter — same events, same notification templates, same
  ledger writes. Otherwise MVP2's flip is a code change, not a config
  change, and we lose the whole point of shadow.
- The three-gate rule (SHADOW=false + ENV=prod + ALLOW_MAINNET=yes) must
  be impossible to bypass. Operator error is the single largest risk on a
  single-VPS single-operator deploy.
- Backtesting reuses the same event pipeline — a historical klines
  replay pushes `KlineReceived` events through the same signal engines,
  and the ShadowGate synthesises fills from the replayed orderbook
  snapshot. Nothing in the strategy layer knows the difference.
- `sandbox` env against Binance TESTNET is the ONE combination where
  `HERMES_SHADOW=false` is legal outside `prod`. Testnet has no real
  money, so the three-gate rule needs a `sandbox+testnet` carveout for
  CI + operator smoke tests.

## Alternatives considered

**Dry-run flag on each adapter method**:

- `place_market_order(..., dry_run=True)` returns a fake fill without
  hitting the wire.
- Requires every caller (application layer) to remember to pass the flag
  based on config. One missed call site = real trade in dev.
- Rejected — the whole point of a middleware wrapper is that the
  application layer never touches the flag. Config decides which adapter
  is wired at composition root; the application code is identical.

**Mock the exchange adapter entirely in shadow**:

- Replace `BinanceAdapter` with `FakeBinanceAdapter` in the composition
  root when `HERMES_SHADOW=true`.
- Loses real market data — reads (klines, orderbook) come from the
  mock, so strategies test against synthetic data. Kills the whole
  "test against real markets" motivation.
- Rejected — MVP1's value proposition is testing against LIVE data.
  A mock exchange defeats the exercise.

**Shadow at the domain-event bus level**:

- Application layer emits `TradeExecuted` directly; adapters are wired
  only when live.
- Skips the entire order-execution code path in shadow — never exercises
  the adapter's serialisation, signing, error handling. Bugs in the
  adapter surface for the first time when we flip to live.
- Rejected — the whole point of shadow is to prove the adapter WORKS
  against real signed requests (D7 — sandbox+testnet path). Bypassing
  the adapter is the opposite of what we want.

**Two separate binaries (`hermes-shadow`, `hermes-live`)**:

- Compile-time choice; no runtime flag at all.
- Doubles the CI matrix, doubles the deploy surface. Operator has to
  swap binaries + systemd units to flip modes. Adds no safety over the
  three-gate rule.
- Rejected — the three-gate rule + `model_validator` gives the same
  safety at 1/10 the ops overhead.

## Rationale

**Why a middleware wrapper (D1)**: The `ExchangePort` interface is the
right boundary. Every future exchange implements it once; the
`ShadowGate` wraps any implementation without knowing the concrete
adapter. The composition root (`hermes.infrastructure.composition_root`)
decides at wire time: `SHADOW=true → adapter = ShadowGate(BinanceAdapter(config))`.
Application code sees an `ExchangePort` either way and cannot tell.

**Why realistic synthetic fills (D2)**: An operator override of the
initial "return a placeholder OrderResult" plan. Reasoning: the whole
point of shadow is to test the pipeline against real market conditions.
A placeholder fill (always at requested price, always instant, always
full qty) makes shadow reports look nothing like live reports and hides
slippage / partial-fill bugs. Synthesising from the live orderbook at
execute-time gives:

- Realistic slippage (walking the book for large orders)
- Realistic partial fills (order qty > book depth at limit price)
- Realistic fees (0.10% Binance standard, tunable per venue). **MVP1
  explicitly assumes no BNB balance and no BNB burn**: fee comes out
  of the traded quote asset, not from a BNB balance. A startup guard
  fails loud if BNB balance > 0 or `BNB_BURN_ENABLED=true` so we never
  silently misprice fills when BNB burn is active (see Round-2 Q5 —
  under BNB burn, `received_qty = order_qty * (1 - fee)` is wrong;
  correct model requires a FEE ledger row denominated in BNB, deferred
  to MVP2).
- Notifications that look identical to live notifications with just the
  `SHADOW` tag flipped

The ShadowGate holds a reference to the same adapter it wraps for its
READ path — `ShadowGate.place_market_order()` internally calls
`self.wrapped.get_orderbook(pair)` to fetch the snapshot, then computes
the fill against that snapshot. No new port, no new dependency injected
into the caller.

**Why two SQLite files instead of a `shadow` column (D9)**: Freqtrade
faced this exact choice in PR #2697 and chose separate `.sqlite` files
(`tradesv3.sqlite` + `tradesv3.dryrun.sqlite`). Reasons the same for
hermes:

- **Aggregation footgun**: `SUM(pnl)` without `WHERE shadow=0` silently
  mixes shadow + live populations. Invisible in logs, corrupts every
  report. Two files make cross-contamination physically impossible.
- **Index bloat**: SQLite's planner doesn't infer partial-index intent;
  every hot query would need `WHERE shadow=0` as a literal filter for
  the partial index to bite. Two files, one index each, no discipline
  required.
- **Unique-constraint collisions**: shadow order IDs would clash with
  real Binance order IDs unless namespaced (`SHD-<uuid>`). Two files,
  the same order-id can appear in both without a `UNIQUE` violation.
- **Migration reversibility**: retiring shadow in MVP2 is
  `rm hermes-shadow.db` — not a schema migration that audits every
  query.

The two-file model has ONE cost: cross-tier reporting queries need a
`UNION` (or a Python-side merge). For MVP1 this is a non-cost — the
shadow report and live report are separate operator surfaces on
purpose. If unified reporting becomes required later, we build a
`LedgerReader` that opens both files read-only and merges.

**Why inject a `ClockPort` (D10)**: The same execution simulator will
serve three tiers: SHADOW (wall-clock, live market data), BACKTEST
(replayed timestamps, historical klines), and LIVE (wall-clock, real
fills). If `ShadowGate.place_market_order()` calls `datetime.now()`
internally, backtest mode produces wrong `TradeExecuted.timestamp`
values that unit tests (which mock time) will not catch — the bug
only surfaces when a real historical replay produces nonsensical
timeline. A `ClockPort` (one-line Protocol: `now() -> datetime`) is
passed to `ShadowGate` at construction. `WallClock()` for
SHADOW/LIVE; `ReplayClock(bar.timestamp)` for BACKTEST. Same gate,
three clocks. This is not premature abstraction — it is the ONLY way
to reuse `ShadowGate` for backtest without silent bugs.

**Why 24h unfilled-timeout global-only (D11)**: MVP1 rejects
uncrossable-at-placement limits immediately, so the 24h timeout is a
safety net for future features that queue orders (resting-limit-book
simulator, MVP2). Global default of 24h maps to a "yesterday's
decision" horizon — most strategies re-emit a fresher signal by then.
Per-strategy override is a 5-line shim when the second strategy needs
a different value; YAGNI to add now with zero strategies live.

**Why extend `TradeExecuted` instead of a new event type (D3)**: A new
`ShadowTradeExecuted` event would require the ledger, tax guard, and
every notification consumer to subscribe to two event types and treat
them identically. That's a lie — for every consumer above the adapter,
shadow IS trade-executed, tagged. One event type, one code path, one
`shadow: bool` field. The event schema version bump ensures serialised
events on disk (event log, replay tests) still round-trip after the
change.

**Why config-time AND runtime enforcement (D4)**: Config-time catches
misconfig at process start (fail loud, no ambiguity). Runtime catches
the case where a future refactor accidentally instantiates a live
adapter directly (bypassing composition root). Two independent axes,
either catches a bug the other misses. This mirrors the existing
belt-and-suspenders discipline documented in
`docs/decisions/2026-08-23-credential-storage-layered-by-env.md`.

**Why the SHADOW tag on notifications (D5)**: Operator + on-call sees
notifications first, code second. A tag prefix is visible in Telegram
push previews, Slack message headers, and the daily digest without
having to open the message. Blue/gray colour codes reinforce the tag —
no red squares in the mobile feed for shadow-mode "losses" that are
paper losses.

**Why `hermes health` surfaces mode (D6)**: `hermes health` is the
operator's first-look diagnostic. If mode is buried in `hermes config
dump`, the operator can restart the daemon after a config edit and not
realise `HERMES_SHADOW=false` was silently ignored due to a typo. A
prominent header is visible on every restart; a red LIVE header is
impossible to miss.

**Why sandbox+testnet is the LIVE test path (D7)**: Binance TESTNET has
no real money but requires real signed HMAC-SHA-256 requests. Running
`HERMES_SHADOW=false` against TESTNET exercises the FULL live code path
(auth, signing, HTTP, error handling, retry) without any financial
risk. This is the only combination allowed in CI. Contract tests under
the `contract` pytest tier hit TESTNET on the nightly cron.

**Why kill switch = env change + systemctl restart (D8)**: A process
restart is a durable audit event in the systemd journal. Hot-reload
requires either a signal handler or an admin socket — both add attack
surface for a solo operator. Restart latency is < 5 seconds on the VPS.
Any "urgent flip to shadow" scenario tolerates 5 seconds; anything that
doesn't (a runaway strategy) is what the tax guard's daily-cap kill is
for.

## Resolved open questions (Round 2 research 2026-08-24)

Three implementation details defaulted during the first design pass;
all three now RESOLVED by evidence from Freqtrade / QuantConnect /
Alpaca / IBKR / Binance docs (see engram
`architecture/shadow-mode-research-round-2`).

**RQ-1: `cancel_order` semantics in shadow — REVISED**. Initial default
"no-op + warning" is WRONG (Freqtrade pattern from PR #2697). Shadow
cancels MUST transition the order to `CANCELLED` in the persistent
shadow ledger (`hermes-shadow.db`), else stale `OPEN` orders corrupt
the shadow position tracker. Resolved: `ShadowGate.cancel_order(order_id)`
looks up the persisted shadow order, sets `status = CANCELLED`, writes
back, emits `OrderCancelled(shadow=true)`. Instant-fill semantics for
market orders are unchanged — those never enter the CANCELLED path
because they never reach OPEN.

**RQ-2: Limit orders in shadow — RESOLVED as option A**. Freqtrade and
Alpaca both fill limit orders in paper mode "only when they become
marketable" (crossable-at-placement). Resolved: `ShadowGate.place_limit_order()`
checks the current orderbook — if the limit crosses, fill at the limit
price (or better if the book has better prices); else emit
`OrderRejected(reason="unfillable at limit in shadow — MVP1 does not
simulate resting limits")`. The 24h `unfilled_timeout` (D11) is a safety
net for MVP2's resting-limit-book simulator, not active in MVP1's
reject-on-placement path.

**RQ-3: Withdrawal / transfer operations in shadow — CONFIRMED
excluded**. Universal industry pattern (Cryptohopper, Alpaca, IBKR,
Freqtrade all exclude withdraws from paper mode). Resolved: `ShadowGate`
does not implement `withdraw` or `transfer`; they raise
`NotImplementedError` if called. MVP2 revisits with a separate gate
review.

## Consequences

**What this locks in**:

- `HermesConfig` gains one root-level field: `shadow: bool = True`.
  Default is `true` so any new checkout / any deploy without explicit
  opt-out runs in shadow — the "shadow-by-default" invariant.
- `HermesConfig.model_validator` enforces the three-gate rule. Any
  future config field cannot break this because the validator runs
  after all fields are set.
- Every `ExchangePort` implementation MUST be wired through `ShadowGate`
  at composition-root time. The composition root exposes ONE factory
  (`build_exchange(config) -> ExchangePort`) that always returns a
  `ShadowGate(<concrete>)` when shadow, and the raw concrete when live.
  Application code cannot instantiate a live adapter directly (import-
  linter contract forbids `hermes.application → hermes.adapters.exchanges`).
- **Two SQLite files** (D9): the composition root opens `hermes.db` or
  `hermes-shadow.db` based on `shadow` at connection time. Alembic
  migrations run against BOTH files at deploy so schemas stay in sync
  (one migration script, two `env.py` targets).
- **`ClockPort`** (D10): a single-method Protocol (`now() -> datetime`)
  injected into every component that reads current time on the
  execution path. Import-linter contract added: `hermes.adapters.exchanges`
  MUST NOT import `datetime.datetime.now` — grep-enforced by a new
  pre-commit hook.
- `TradeExecuted` event schema is at v2. All new event types default to
  `shadow: bool = False`; MVP2 removes the field once shadow is retired
  (breaking schema bump v3 tracked separately).
- Every notification template branches on `shadow` — the branch lives
  in the template, not the caller.
- `hermes health` grows a `mode` line (SHADOW/LIVE) as its first
  displayed field.
- **Go-live checklist lives in the GH Project board**, not a repo file.
  Operator preference 2026-08-24: single source of truth = GH Project;
  no scattered checklist docs in the repo. Gap 4 (position
  reconciliation) is addressed by the Project "Go-Live Prep" column
  where each checklist item becomes a card (verify API key + base URL,
  three gates set, `hermes health` reports LIVE, `hermes exchange balance`
  snapshot, freeze `hermes-shadow.db`, initialize `hermes.db` from
  live balance, confirm no open shadow positions carry over). This
  ADR references the Project column, does not duplicate its content.
- CI's `contract` tier runs `HERMES_ENV=sandbox HERMES_SHADOW=false`
  against Binance TESTNET to prove the live signing/HTTP path on the
  nightly cron.

**What this forecloses**:

- Cannot skip the ShadowGate in the composition root — any new
  exchange adapter is required to route through it. Enforced by a
  composition-root test that inspects the wired instance's type.
- Cannot ship a "live mode without gates" build — the
  `model_validator` runs unconditionally. A test config that wants to
  test live paths sets all three gates to their prod values or uses
  the sandbox+testnet carveout.
- Cannot introduce hot-reload of `HERMES_SHADOW` — the kill switch is
  a restart. If future ops needs hot-reload, that is a separate ADR
  because it adds a new attack surface (admin socket).
- Cannot silently drop the `shadow` field from downstream consumers —
  the mutation-testing tier's kill matrix will surface any consumer
  that ignores the field where it should tag output.

**What we'll revisit**:

- The `sandbox+testnet` carveout is the current mechanism to allow
  `HERMES_SHADOW=false` outside `prod`. If we ever add a second
  exchange with a real spot testnet (not just futures demo), the
  carveout logic in `model_validator` will need per-venue rules — the
  current single-venue check is a shortcut that works because Binance
  is the only spot-testnet exchange in MVP1.
- If backtest replay reveals that synthesising fills from orderbook
  snapshots (D2) is too slow (each shadow trade issues a real
  `get_orderbook` call), we may cache the snapshot per strategy tick.
  Defer until profiling shows it matters.
- **BNB burn / fee tier drift (MVP2)**: MVP1 hard-fails if BNB balance > 0.
  MVP2 adds the FEE-in-BNB ledger row path AND fetches the actual account
  fee schedule (`GET /sapi/v1/asset/tradeFee`, IP weight 1) at startup +
  daily refresh — so shadow P&L matches what live will pay under the
  real account tier (VIP + BNB discount) instead of the standard 0.10%
  MVP1 assumes.
- **Kill-switch upgrade (MVP2 pre-real-money)**: env-var + restart is
  fine at single-VPS solo-operator MVP1 scale. Before MVP2 puts real
  money on the line, add a SIGHUP-handled admin socket for instant
  shadow-flip AND a deploy-time readback of `HERMES_SHADOW` from the
  running process (Knight Capital 2012 lesson — stale feature flag on
  1-of-8 servers = $440M loss).
- **Divergence detection (MVP2)**: three-tier triangulation (backtest
  vs shadow vs live PnL over the same period) is the accepted way to
  prove the fill model is faithful. MVP1 logs every shadow
  "would-have-filled" event with timestamp + price + qty + fee for
  later analysis; the MVP2 tooling reads these logs and emits
  divergence alerts if shadow P&L drifts > 20% from a hold baseline
  over 30 days.
- MVP2 removes the SHADOW default: `shadow: bool = False`. The three-
  gate rule stays. This is a breaking config change gated by an
  operator-runbook update; not addressed here.

## ADR-003 status

Not modified (AGENTS.md prohibition #10 — ADRs are immutable once
Accepted). This decision-journal entry OPERATIONALISES ADR-003 §01's
"hexagonal boundary between application and exchange adapters" — the
`ShadowGate` is a port-level decorator, not a new architectural layer.
The composition-root factory pattern was already the intended shape;
this entry names the wrapper that lives inside it. ADR-003 §04's
credential surface is unchanged: `HERMES_SHADOW` is a new root-level
env var following the same three-tier storage rule
(`.env.local` / GH Environment / `/etc/hermes.env`) as `HERMES_ENV`
and `HERMES_ALLOW_MAINNET`.
