# Change: shadow-mode-foundation

**SDD change**: `shadow-mode-foundation`
**Date**: 2026-08-24
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#175)

Mirror of engram topic key `sdd/shadow-mode-foundation/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Ship the foundational shadow-mode capability that makes MVP1 possible:
`HERMES_SHADOW: bool` root-level config flag + `ShadowGate` middleware
wrapper around every exchange adapter that intercepts order writes
before they touch the wire. Read operations (klines, orderbook, balance)
still hit the exchange so the strategy engine sees real market data;
write operations (`place_market_order`, `place_limit_order`,
`cancel_order`) synthesise realistic fills from the live orderbook,
persist to a dedicated `hermes-shadow.db` SQLite file, and emit the
same `TradeExecuted` events (tagged `shadow=true`) that real fills
would. Real money requires three simultaneous gates
(`SHADOW=false + ENV=prod + ALLOW_MAINNET=yes`), enforced at both
config-load and every write path. Main risk: silent shadow-vs-live
divergence — mitigated by realistic orderbook-walk fill model + logging
every simulated fill for MVP2 divergence-detection tooling.

## Scope

### In

- **Root config field** (`src/hermes/infrastructure/config.py`):
  `HermesConfig.shadow: bool = True` (shadow-by-default invariant).
  Pydantic `model_validator(mode="after")` enforces the three-gate rule.
- **`env_gate.require_live()`** (`src/hermes/infrastructure/env_gate.py`):
  runtime check invoked by every adapter write path; raises
  `EnvGateError` if any gate is not set. Belt-and-suspenders alongside
  the config-time validator.
- **`ClockPort`** (`src/hermes/ports/clock.py`): single-method Protocol
  (`now() -> datetime`). Impls: `WallClock` (`src/hermes/infrastructure/clock.py`)
  for SHADOW + LIVE; `ReplayClock` (`src/hermes/infrastructure/clock.py`)
  for BACKTEST. All adapters that touch current time on the execution
  path receive a `ClockPort` via constructor.
- **`ShadowGate` wrapper**
  (`src/hermes/adapters/exchanges/shadow_gate.py`): implements
  `ExchangePort`, wraps a concrete adapter. Delegates reads verbatim.
  For writes: fetches current orderbook via wrapped adapter, computes
  synthetic fill (slippage from top-of-book walk, standard fee against
  quote asset), persists shadow order + fill to `hermes-shadow.db`,
  emits `TradeExecuted(shadow=true)`. Limit orders that are not
  crossable-at-placement → `OrderRejected` (no resting-limit-book
  simulator in MVP1).
- **Shadow ledger persistence**
  (`src/hermes/adapters/ledger/shadow_repository.py`): dedicated SQLite
  file `hermes-shadow.db`, opened at composition-root time when
  `shadow=True`. Alembic migrations shared with live (`hermes.db`);
  one migration script, two `env.py` targets.
- **`TradeExecuted` event schema v2**
  (`src/hermes/domain/events/trade_executed.py`): add `shadow: bool = False`
  field. Bump `__event_schema_version__ = 2`. Backward-compat migration
  test for serialised v1 events.
- **BNB-burn startup guard**
  (`src/hermes/infrastructure/binance_startup_guard.py`): on Binance
  adapter construction, if account BNB balance > 0 OR
  `HERMES_BINANCE__BNB_BURN_ENABLED=true`, raise `NotImplementedError`
  with pointer to MVP2 issue for BNB-in-BNB FEE ledger row.
- **Notification `SHADOW` tag**
  (`src/hermes/application/messages/telegram.py`,
  `src/hermes/application/messages/slack.py`): every template that
  renders a `TradeExecuted` prepends `SHADOW` prefix + blue/gray colour
  when `event.shadow is True`. Never red.
- **`hermes health` mode header**
  (`src/hermes/entrypoints/cli.py`, `src/hermes/entrypoints/_health.py`):
  first output line reads `mode: SHADOW` (blue/gray) or `mode: LIVE`
  (red). Fails loud when gates are half-configured (e.g.
  `SHADOW=false` in `dev`).
- **Composition-root factory update**
  (`src/hermes/infrastructure/composition_root.py`): existing
  `build_exchange(config) -> ExchangePort` factory now returns
  `ShadowGate(<concrete>)` when `config.shadow is True`, raw concrete
  otherwise. Application code cannot reach the concrete adapter
  directly (import-linter contract already forbids
  `hermes.application → hermes.adapters.exchanges`).
- **Import-linter contract addition**
  (`.importlinter.toml`): `hermes.adapters.exchanges` MUST NOT import
  `datetime.datetime.now` or `time.time()` at module or method level
  — enforce ClockPort usage. Grep-checked by a new pre-commit hook.
- **Env-var wiring**: `.env.local.example` documents `HERMES_SHADOW=true`;
  `deploy/systemd/hermes.env.example` mirrors. Prod default is
  `true` — operator must explicitly set `false` to flip live.
- **Unit + integration + contract tests** (see Test plan below).

### Out

- **Resting-limit-book simulator** — MVP1 rejects uncrossable-at-placement
  limits with `OrderRejected`. Real matching engine deferred to MVP2.
- **`unfilled_timeout` enforcement path** — the 24h global config field
  (`HERMES_SHADOW__UNFILLED_TIMEOUT_HOURS = 24`) exists but is a
  safety net for MVP2's resting-limit-book simulator; MVP1's
  reject-on-placement path never touches it. Follow-up issue when
  MVP2 begins.
- **Per-strategy `unfilled_timeout` override** — YAGNI at zero
  strategies live. Follow-up when 2nd strategy needs a different
  value.
- **BNB-burn / BNB-denominated FEE ledger row** — MVP1 hard-fails at
  BNB balance > 0. Follow-up MVP2 issue for the FEE-in-BNB accounting
  path.
- **Fee schedule fetching** (`GET /sapi/v1/asset/tradeFee`) — MVP1
  hardcodes 0.10% Binance standard. MVP2 fetches actual account tier
  at startup + daily refresh.
- **Hot-reload / admin-socket kill switch** — env-var + `systemctl
  restart` for MVP1. MVP2 pre-real-money adds SIGHUP handler +
  deploy-time readback (Knight Capital lesson).
- **Divergence-detection tooling** — MVP1 logs every shadow fill for
  later analysis; three-tier triangulation (backtest vs shadow vs
  live) is MVP2 scope.
- **Withdraw / transfer support in `ShadowGate`** — raises
  `NotImplementedError`. Universal industry pattern (Freqtrade,
  Alpaca, IBKR all exclude). MVP2 with its own gate review.
- **Web UI shadow-vs-live comparison dashboard** — MVP2.

## Decisions

Full rationale for each decision lives in
`docs/decisions/2026-08-24-shadow-mode-architecture.md` (dual-source
per ADR-009 §6). This section names the decisions, gives a one-line
rationale plus cost of reversal, and defers to the ADR for
alternatives and evidence.

### D-1: `ShadowGate` middleware wrapper (not per-adapter dry-run flag)

- **Decision**: One wrapper implements `ExchangePort`, wraps concrete
  adapters, decides read-vs-write at method level.
- **Alternatives**: per-method `dry_run: bool` param (rejected — caller
  discipline required), separate `PaperExchange` mock (rejected —
  loses real market data on reads).
- **Rationale**: matches Freqtrade / Hummingbot / QuantConnect pattern.
  Application code cannot tell it's in shadow — no branching above the
  adapter.
- **Cost of reversal**: LOW — remove one class, wire concrete adapter
  directly.

### D-2: Realistic synthetic fills from orderbook walk (fee = 0.10% quote, no BNB)

- **Decision**: Fetch live orderbook, walk it for the requested qty,
  apply 0.10% fee against the traded quote asset. Partial fills possible
  if qty exceeds book depth at limit price. Startup guard fails loud if
  BNB balance > 0 OR `BNB_BURN_ENABLED=true`.
- **Alternatives**: placeholder "instant-fill-at-requested-price" stub
  (rejected — hides slippage/partial-fill bugs, retail-grade error),
  fetch fee schedule at startup (deferred to MVP2 — see Out).
- **Rationale**: Round-1 research confirmed real orderbook walk is
  industry standard (Freqtrade, IBKR paper, QuantConnect). BNB-burn
  hard-fail avoids Round-2 Q5 gotcha (`received_qty = qty * (1-fee)` is
  wrong under BNB burn; requires separate FEE-in-BNB ledger row not
  built in MVP1).
- **Cost of reversal**: LOW — swap fill algorithm inside `ShadowGate`.

### D-3: Extend `TradeExecuted` with `shadow: bool = False`, schema v2

- **Decision**: One event type for both modes; downstream consumers
  never branch on the flag, only tag output. Backward-compat migration
  test for v1 events.
- **Alternatives**: separate `ShadowTradeExecuted` event type (rejected
  — forces every consumer to subscribe to both, treat identically = lie).
- **Rationale**: for every consumer above the adapter, shadow IS
  trade-executed, tagged. One event, one code path.
- **Cost of reversal**: MEDIUM — schema bump v3 required to remove
  field.

### D-4: Three-gate rule enforced at config-time AND runtime

- **Decision**: Pydantic `model_validator` at load; `env_gate.require_live()`
  at every adapter write.
- **Alternatives**: config-only (rejected — a future refactor could
  instantiate live adapter directly), runtime-only (rejected — misconfig
  should fail at process start, not first trade).
- **Rationale**: two independent axes, either catches a bug the other
  misses. Mirrors existing credential-storage belt-and-suspenders
  discipline (2026-08-23-credential-storage-layered-by-env.md).
- **Cost of reversal**: LOW — remove either check, other still applies.

### D-5: `SHADOW` prefix in notifications, blue/gray colour

- **Decision**: Explicit `SHADOW` tag prepended to every notification
  rendering a `TradeExecuted` with `shadow=True`. Blue/gray, never red.
- **Alternatives**: colour-only differentiation (rejected — colour
  invisible in push previews), footer note (rejected — buried).
- **Rationale**: operator + on-call see notifications first. Tag is
  visible in Telegram push previews and Slack headers without opening
  the message.
- **Cost of reversal**: LOW — template change.

### D-6: `hermes health` shows mode as first output line

- **Decision**: First line reads `mode: SHADOW` (blue/gray) or `mode:
  LIVE` (red). Fails loud when gates are half-configured.
- **Alternatives**: bury in `hermes config dump` (rejected — operator
  can restart the daemon after a config edit and not realise the flag
  was silently ignored due to a typo).
- **Rationale**: `hermes health` is the operator's first-look
  diagnostic. Red LIVE header is impossible to miss.
- **Cost of reversal**: LOW — one line of CLI code.

### D-7: `sandbox` env + Binance TESTNET is the LIVE code path test

- **Decision**: The `contract` pytest tier runs `HERMES_ENV=sandbox
  HERMES_SHADOW=false` against Binance TESTNET on nightly cron. Never
  against mainnet in CI.
- **Alternatives**: recorded-request replay (rejected — doesn't
  exercise real signing), mock all live paths (rejected — bugs surface
  first at go-live).
- **Rationale**: TESTNET has no real money but requires real HMAC-SHA-256
  signed requests. Full live code path (auth, signing, HTTP, retry) is
  exercised without financial risk.
- **Cost of reversal**: LOW — CI job update.

### D-8: Kill switch = env-var + `systemctl restart`

- **Decision**: Edit `/etc/hermes.env` → set `HERMES_SHADOW=true` →
  `systemctl restart hermes`. No hot-reload.
- **Alternatives**: SIGHUP handler (rejected for MVP1 — adds attack
  surface for a solo operator), admin socket (same).
- **Rationale**: process restart is a durable audit event in the
  systemd journal. Restart latency < 5s on the VPS; acceptable for
  MVP1. MVP2 pre-real-money will add SIGHUP + deploy readback.
- **Cost of reversal**: LOW — add SIGHUP handler when MVP2 needs it.

### D-9: Two separate SQLite files (`hermes.db` + `hermes-shadow.db`)

- **Decision**: Composition root opens `hermes.db` when `shadow=False`,
  `hermes-shadow.db` when `shadow=True`. Alembic migrations run against
  both at deploy.
- **Alternatives**: single table with `shadow BOOL` column (REJECTED
  based on Round-2 research — Freqtrade PR #2697 anti-pattern:
  aggregation footguns, index bloat, unique-constraint collisions,
  migration pain).
- **Rationale**: two files, zero cross-contamination possible. One
  cost: cross-tier reports require a `UNION` — non-cost for MVP1
  where shadow and live reports are separate operator surfaces.
- **Cost of reversal**: MEDIUM — data migration between files if we
  ever want to merge.

### D-10: `ClockPort` abstraction for wall-clock vs replay-clock

- **Decision**: Single-method Protocol (`now() -> datetime`). All
  adapters that read current time on execution paths receive it via
  constructor. `WallClock()` for SHADOW + LIVE; `ReplayClock(bar.ts)`
  for BACKTEST.
- **Alternatives**: `datetime.now()` calls scattered inline (REJECTED
  based on Round-2 Q4 — backtest replay silently produces wrong
  timestamps; unit tests mock time so bugs only surface at real
  historical replay).
- **Rationale**: same execution simulator serves three tiers. Only
  ClockPort injection avoids silent time-model bugs at the
  shadow-backtest boundary.
- **Cost of reversal**: LOW — inline `datetime.now()` again, drop the
  port.

### D-11: 24h global `unfilled_timeout`, per-strategy override deferred

- **Decision**: `HERMES_SHADOW__UNFILLED_TIMEOUT_HOURS = 24` env var,
  no per-strategy override in MVP1.
- **Alternatives**: per-strategy YAML field (deferred YAGNI at zero
  strategies live), no timeout (rejected — stale orders would
  accumulate in MVP2 resting-limit-book simulator).
- **Rationale**: 24h maps to "yesterday's decision" horizon; most
  strategies re-emit fresher signals by then. Per-strategy shim is 5
  lines when 2nd strategy needs it.
- **Cost of reversal**: LOW — add config field + override lookup.

## Requirements

Each requirement is a testable statement. Each acceptance criterion is
a shell-verifiable check.

### R-1: `HERMES_SHADOW` is a first-class root config field, defaults to true

- **A-1.1**: `test tests/unit/infrastructure/test_config_shadow_default.py::test_shadow_defaults_to_true` passes.
- **A-1.2**: `HERMES_SHADOW=false` alone (without `ENV=prod` +
  `ALLOW_MAINNET=yes`) raises `ValidationError` at `HermesConfig()`
  instantiation. Test: `test_config_shadow_false_without_all_gates_fails`.
- **A-1.3**: All three gates set (`SHADOW=false + ENV=prod +
  ALLOW_MAINNET=yes`) loads cleanly. Test:
  `test_config_all_three_gates_loads`.
- **A-1.4**: `.env.local.example` and `deploy/systemd/hermes.env.example`
  both document `HERMES_SHADOW=true`. Check: `grep -q '^HERMES_SHADOW='
  .env.local.example && grep -q '^HERMES_SHADOW=' deploy/systemd/hermes.env.example`.

### R-2: `env_gate.require_live()` is called by every adapter write path

- **A-2.1**: `ShadowGate.place_market_order` calls `env_gate.require_live()`
  when `shadow=false`. Test:
  `test_shadow_gate_calls_require_live_on_write_when_not_shadow`.
- **A-2.2**: `env_gate.require_live()` raises `EnvGateError` when any
  gate missing (matrix test of 7 combinations that fail + 1 that
  passes). Test: `test_env_gate_require_live_matrix`.
- **A-2.3**: import-linter contract `no_datetime_now_in_exchange_adapters`
  passes. Check: `uv run lint-imports` exit 0.

### R-3: `ShadowGate` wraps concrete adapters via composition root

- **A-3.1**: `build_exchange(config with shadow=True)` returns an
  instance of `ShadowGate`. Test:
  `test_composition_root_wires_shadow_gate_when_shadow_true`.
- **A-3.2**: `build_exchange(config with shadow=False, env=prod,
  allow_mainnet=yes)` returns the concrete adapter directly (not
  wrapped). Test:
  `test_composition_root_wires_raw_adapter_when_live`.
- **A-3.3**: `ShadowGate.get_klines` returns the wrapped adapter's
  klines verbatim (read pass-through). Test:
  `test_shadow_gate_delegates_reads_to_wrapped`.

### R-4: `ShadowGate.place_market_order` synthesises realistic fills

- **A-4.1**: Fill price equals the volume-weighted average of the walked
  orderbook levels (property test with Hypothesis). Test:
  `test_shadow_gate_market_order_vwap_matches_orderbook_walk`.
- **A-4.2**: Fee is 0.10% of notional, deducted from received quote
  asset (never from BNB). Test:
  `test_shadow_gate_market_order_fee_deducted_from_quote`.
- **A-4.3**: Order qty exceeding book depth returns a `PartialFill`.
  Test: `test_shadow_gate_market_order_partial_fill_when_qty_exceeds_depth`.
- **A-4.4**: Every shadow fill is persisted to `hermes-shadow.db` with
  `status=FILLED`, `shadow=True`, correct fill price, correct qty.
  Test: `test_shadow_gate_persists_fill_to_shadow_db`.

### R-5: `ShadowGate.place_limit_order` rejects uncrossable limits

- **A-5.1**: Crossable-at-placement limit fills at the limit price (or
  better if book has better prices). Test:
  `test_shadow_gate_limit_order_fills_when_crossable`.
- **A-5.2**: Uncrossable-at-placement limit emits `OrderRejected` with
  reason `"unfillable at limit in shadow — MVP1 does not simulate
  resting limits"`. Test:
  `test_shadow_gate_limit_order_rejects_when_uncrossable`.
- **A-5.3**: Rejected orders are NOT persisted to `hermes-shadow.db`
  (no phantom OPEN rows). Test:
  `test_shadow_gate_rejected_limits_not_persisted`.

### R-6: `ShadowGate.cancel_order` transitions status in the shadow ledger

- **A-6.1**: Cancelling a filled shadow order raises
  `OrderAlreadyFinal`. Test:
  `test_shadow_gate_cancel_already_filled_raises`.
- **A-6.2**: (MVP2 hook) Cancelling an open shadow order transitions
  its ledger row to `status=CANCELLED` and emits
  `OrderCancelled(shadow=true)`. For MVP1 (no OPEN orders possible
  since all fills are instant), the test path is a fixture-populated
  order. Test:
  `test_shadow_gate_cancel_open_order_sets_cancelled_and_emits_event`.

### R-7: `TradeExecuted` event carries `shadow: bool`, schema v2

- **A-7.1**: `TradeExecuted(...)` has `shadow: bool = False` default;
  omitting it does not change v1-compatible behaviour. Test:
  `test_trade_executed_shadow_defaults_false`.
- **A-7.2**: `__event_schema_version__ == 2`. Check: `python -c 'from
  hermes.domain.events.trade_executed import __event_schema_version__;
  assert __event_schema_version__ == 2'`.
- **A-7.3**: A serialised v1 event (no `shadow` field) deserialises
  cleanly with `shadow=False`. Test:
  `test_trade_executed_v1_backward_compat`.

### R-8: BNB-burn startup guard fails loud

- **A-8.1**: Binance adapter construction raises `NotImplementedError`
  if `HERMES_BINANCE__BNB_BURN_ENABLED=true`. Test:
  `test_binance_startup_guard_fails_on_bnb_burn_env`.
- **A-8.2**: Binance adapter construction raises `NotImplementedError`
  if account BNB balance > 0 at construction-time REST call. Test:
  `test_binance_startup_guard_fails_on_bnb_balance_nonzero` (respx
  mocks the REST call).
- **A-8.3**: Error message points to a MVP2 follow-up issue for the
  BNB-in-BNB FEE ledger row path.

### R-9: Two SQLite files with shared Alembic schema

- **A-9.1**: `hermes-shadow.db` exists after first shadow write.
  Test: `test_shadow_write_creates_shadow_db_file`.
- **A-9.2**: `hermes.db` is not opened when `shadow=True`. Test:
  `test_shadow_mode_does_not_open_live_db`.
- **A-9.3**: `alembic upgrade head` succeeds against both DB URIs
  when driven by the deploy script. Check:
  `bash deploy/scripts/migrate-both-dbs.sh` exit 0.

### R-10: `ClockPort` injected everywhere on the execution path

- **A-10.1**: `ShadowGate` accepts a `clock: ClockPort` constructor
  arg; missing arg raises `TypeError`. Test:
  `test_shadow_gate_requires_clock_port`.
- **A-10.2**: Every emitted `TradeExecuted.timestamp` equals
  `clock.now()` at execute-time. Test:
  `test_shadow_gate_timestamp_comes_from_clock_port` (uses
  `FakeClock(fixed=datetime(2026,1,1,tzinfo=UTC))` — asserts equality).
- **A-10.3**: import-linter contract forbids `datetime.datetime.now`
  and `time.time` inside `src/hermes/adapters/exchanges/`. Check:
  `uv run lint-imports` exit 0.

### R-11: Notifications show `SHADOW` prefix, blue/gray, never red

- **A-11.1**: Telegram template for `TradeExecuted(shadow=True)`
  contains `SHADOW` literal in the tag line. Test:
  `test_telegram_template_shadow_prefix`.
- **A-11.2**: Slack blocks for `TradeExecuted(shadow=True)` use
  Slack colour `#7B8FA1` (blue-gray) or `#B0BEC5`, never `#EF4444`
  (red). Test: `test_slack_template_shadow_colour_never_red`.
- **A-11.3**: Live event (`shadow=False`) with negative P&L uses red
  colour as today (no regression). Test:
  `test_slack_template_live_negative_pnl_still_red`.

### R-12: `hermes health` shows mode as first output line

- **A-12.1**: `hermes health` first line matches regex
  `^mode: (SHADOW|LIVE)`. Test:
  `test_hermes_health_first_line_is_mode`.
- **A-12.2**: `mode: LIVE` line rendered red (ANSI escape sequence
  present); `mode: SHADOW` rendered blue/gray. Test:
  `test_hermes_health_mode_colour`.
- **A-12.3**: Half-configured (`SHADOW=false` in `dev`) makes
  `hermes health` exit code non-zero and emit an error banner. Test:
  `test_hermes_health_fails_loud_on_half_configured_gates`.

### R-13: Contract-tier nightly runs `SHADOW=false` against Binance TESTNET

- **A-13.1**: `nightly-quality.yml` runs a `contract-tier-live-path`
  job that sets `HERMES_ENV=sandbox HERMES_SHADOW=false`. Check: `grep
  -q 'HERMES_SHADOW=false' .github/workflows/nightly-quality.yml`.
- **A-13.2**: The job passes on a green nightly run against Binance
  TESTNET (post-merge verification, not blocking initial PR).

## Design

Copy-ready content for the apply phase.

### File tree diff

```text
src/hermes/
├── infrastructure/
│   ├── config.py                   [MODIFY: add shadow: bool = True + model_validator]
│   ├── env_gate.py                 [MODIFY: add require_live() function]
│   ├── clock.py                    [NEW: WallClock + ReplayClock impls]
│   ├── composition_root.py         [MODIFY: build_exchange returns ShadowGate when shadow]
│   └── binance_startup_guard.py    [NEW: fail_if_bnb_burn_enabled_or_balance_nonzero]
├── ports/
│   └── clock.py                    [NEW: ClockPort Protocol]
├── adapters/
│   ├── exchanges/
│   │   └── shadow_gate.py          [NEW: ShadowGate(ExchangePort)]
│   └── ledger/
│       └── shadow_repository.py    [NEW: opens hermes-shadow.db]
├── domain/
│   └── events/
│       └── trade_executed.py       [MODIFY: add shadow: bool field, bump schema v2]
├── application/
│   └── messages/
│       ├── telegram.py             [MODIFY: SHADOW tag branch]
│       └── slack.py                [MODIFY: SHADOW tag + colour override]
└── entrypoints/
    ├── cli.py                      [MODIFY: hermes health first line = mode]
    └── _health.py                  [MODIFY: mode field in health registry]

.env.local.example                  [MODIFY: HERMES_SHADOW=true documented]
deploy/systemd/hermes.env.example   [MODIFY: HERMES_SHADOW=true documented]
alembic/env.py                      [MODIFY: read DB URL from HERMES_DB_URL env var — Option 1]
deploy/scripts/migrate-both-dbs.sh  [NEW: alembic upgrade against both DBs, driven by env var]
docs/decisions/2026-08-24-shadow-mode-architecture.md  [ALREADY EXISTS: ADR from prior PR]
.importlinter.toml                  [MODIFY: add no_datetime_now_in_exchange_adapters contract]
.github/workflows/nightly-quality.yml  [MODIFY: add contract-tier-live-path job]
alembic/versions/XXXX_add_shadow_default_to_trade_executed.py  [NEW: no-op migration for schema doc]
```

**Note**: `GO_LIVE_CHECKLIST.md` (originally scoped as
`docs/guides/operator-runbook-go-live.md`) was dropped from this
change on operator's 2026-08-24 direction: "single source of truth =
GH Project; no scattered checklist docs in the repo." Gap 4 (position
reconciliation on go-live) is addressed by cards in the GH Project
"Go-Live Prep" column instead of a repo file. The ADR carries the
reference; no repo doc mirrors it.

**Alembic double-target — Option 1 (env-var-driven URL)**: single
`alembic/env.py`. Reads `HERMES_DB_URL` env var at migration time.
Deploy invokes `alembic upgrade head` twice per release:
`HERMES_DB_URL=sqlite:///hermes.db alembic upgrade head` then
`HERMES_DB_URL=sqlite:///hermes-shadow.db alembic upgrade head`. One
`env.py`, one script, matches Freqtrade's own pattern for the same
problem. Wrapped in `deploy/scripts/migrate-both-dbs.sh` so the deploy
pipeline calls one command.

### Key interface stubs

```python
# src/hermes/ports/clock.py
from typing import Protocol
from datetime import datetime


class ClockPort(Protocol):
    def now(self) -> datetime: ...


# src/hermes/infrastructure/clock.py
from datetime import UTC, datetime
from dataclasses import dataclass


class WallClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC)


@dataclass
class ReplayClock:
    _current: datetime

    def now(self) -> datetime:
        return self._current

    def advance(self, to: datetime) -> None:
        self._current = to


# src/hermes/adapters/exchanges/shadow_gate.py
# VENUE-AGNOSTIC. Wraps any ExchangePort implementation (Binance today,
# Kraken if we swap venues). All venue-specific concerns (fee schedule,
# BNB-burn guard, pair-symbol translation) live in the wrapped concrete
# adapter or its per-venue config — never inside ShadowGate.
from dataclasses import dataclass
from hermes.ports.exchange import ExchangePort
from hermes.ports.clock import ClockPort
from hermes.ports.fees import FeeSchedulePort  # from Kraken PR #193
from hermes.adapters.ledger.shadow_repository import ShadowRepository


@dataclass
class ShadowGate(ExchangePort):
    wrapped: ExchangePort  # e.g. BinanceAdapter or KrakenAdapter
    fee_schedule: FeeSchedulePort  # per-venue fees; NEVER hardcoded in ShadowGate
    clock: ClockPort
    shadow_repo: ShadowRepository

    # Read pass-through
    def get_klines(self, *args, **kwargs):
        return self.wrapped.get_klines(*args, **kwargs)

    def get_orderbook(self, *args, **kwargs):
        return self.wrapped.get_orderbook(*args, **kwargs)

    def get_balance(self, *args, **kwargs):
        return self.wrapped.get_balance(*args, **kwargs)

    # Write intercept
    def place_market_order(self, pair, side, qty) -> OrderResult:
        book = self.wrapped.get_orderbook(pair)
        fees = self.fee_schedule.get_fees(
            pair
        )  # per-venue: 0.10% Binance, 0.26% Kraken taker, etc.
        fill = self._walk_book(book, side, qty, fees.taker)  # (avg_price, filled_qty, fee_in_quote)
        result = OrderResult(
            order_id=f"SHD-{uuid4()}",
            fill_price=fill.avg_price,
            filled_qty=fill.filled_qty,
            fee=fill.fee_in_quote,
            timestamp=self.clock.now(),
            shadow=True,
        )
        self.shadow_repo.persist_fill(result)
        return result

    # ... place_limit_order, cancel_order similar
```

### `HermesConfig.model_validator` for three-gate rule

```python
# src/hermes/infrastructure/config.py
from typing import Self, Literal
from pydantic import BaseSettings, model_validator


class HermesConfig(BaseSettings):
    env: Literal["dev", "sandbox", "prod"] = "dev"
    shadow: bool = True
    allow_mainnet: bool = False
    # ... other fields

    @model_validator(mode="after")
    def _live_trading_three_gates(self) -> Self:
        if not self.shadow:
            gates_ok = self.env == "prod" and self.allow_mainnet
            sandbox_testnet_carveout = self.env == "sandbox"  # for CI live-path tests
            if not (gates_ok or sandbox_testnet_carveout):
                raise ValueError(
                    "HERMES_SHADOW=false requires either "
                    "(HERMES_ENV=prod AND HERMES_ALLOW_MAINNET=yes) "
                    "or (HERMES_ENV=sandbox for TESTNET contract tests). "
                    f"Got env={self.env!r}, allow_mainnet={self.allow_mainnet}."
                )
        return self
```

### `env_gate.require_live()`

```python
# src/hermes/infrastructure/env_gate.py
class EnvGateError(RuntimeError):
    pass


def require_live(config: HermesConfig) -> None:
    """Runtime check invoked by every adapter write path."""
    if config.shadow:
        raise EnvGateError("write path invoked but HERMES_SHADOW=true")
    if config.env == "sandbox":
        return  # TESTNET carveout — signed requests OK
    if config.env != "prod" or not config.allow_mainnet:
        raise EnvGateError(
            f"live write requires env=prod+allow_mainnet=yes, "
            f"got env={config.env!r}, allow_mainnet={config.allow_mainnet}"
        )
```

### Go-live checklist location

Per operator direction 2026-08-24, the go-live checklist is NOT a
repo file. It lives as cards in the **GH Project "Go-Live Prep"
column**, so the checklist evolves via the same board that tracks all
other MVP1 work. Suggested initial cards (operator populates the
Project at their preferred cadence — not this PR's responsibility):

1. Confirm mainnet API key + secret in `/etc/hermes.env`
2. Confirm base URL is mainnet (`api.binance.com` or the Kraken
   equivalent — venue-agnostic depending on which exchange is wired)
3. Confirm all three gates: `HERMES_SHADOW=false`,
   `HERMES_ENV=prod`, `HERMES_ALLOW_MAINNET=yes`
4. `sudo systemctl restart hermes`; wait 10s; `hermes health` first
   line reads `mode: LIVE` (red)
5. `hermes exchange balance` returns non-zero live balance; snapshot
6. Freeze `hermes-shadow.db` (backup + move aside)
7. Initialize `hermes.db` ledger from live balance snapshot
8. Explicit reminder: shadow positions do NOT carry over — strategy
   state resets at go-live cold start

## Test plan

1. Every A-x.y acceptance criterion above has a matching test.
2. `bash scripts/dev/pr-preflight.sh` → 13/13 green.
3. New import-linter contract passes: `uv run lint-imports` exit 0.
4. Coverage floor met on new code (≥ 90% per repo default; ≥ 95% on
   `src/hermes/domain/events/trade_executed.py` per domain-tier
   convention).
5. Mutation-testing kill rate on `src/hermes/adapters/exchanges/shadow_gate.py`
   ≥ 70% (weekly cron will verify post-merge; not blocking initial PR).
6. Manual smoke test on dev machine: `HERMES_SHADOW=true hermes
   run-once` executes a paper trade end-to-end (signal → shadow gate
   → shadow ledger row → Telegram SHADOW notification).
7. Nightly contract-tier post-merge: `HERMES_ENV=sandbox
   HERMES_SHADOW=false` against Binance TESTNET green on the first
   nightly run.

## Risks

- **Risk 1 — Shadow/live divergence hidden by fill model drift**. The
  orderbook-walk fill model at MVP1 doesn't account for latency,
  queue position, or realistic slippage in fast markets. Shadow P&L
  may over- or under-state live. Mitigation: log every shadow fill
  with full context (timestamp, orderbook snapshot, walked levels,
  fee) so MVP2 divergence-detection tooling can back-analyse. Accept
  the fidelity limit for MVP1; document it in the ADR's "revisit"
  section.
- **Risk 2 — Alembic double-target complexity**. Running migrations
  against both `hermes.db` and `hermes-shadow.db` at deploy adds
  operational surface. Mitigation: single migration script, two
  `env.py` invocations wrapped by `deploy/scripts/migrate-both-dbs.sh`.
  If either fails, deploy aborts. Test: manual deploy dry-run before
  merge.
- **Risk 3 — Import-linter false positive on `ClockPort` contract**.
  Existing adapter code may have `datetime.now()` calls that are NOT
  on the execution path (e.g. logging correlation IDs). Mitigation:
  add explicit exceptions to the contract for known non-execution
  paths; grep the codebase pre-merge and either refactor to use
  ClockPort or add an exception with a comment.
- **Risk 4 — BNB balance detection race**. Startup guard calls
  `get_balance()` for BNB — if the exchange returns transient error,
  guard would incorrectly PASS. Mitigation: retry 3× with backoff;
  fail loud on all-3-fail (fail closed, not open).
- **Risk 5 — Test flakiness on `ShadowGate.get_orderbook` calls**.
  Every shadow write does a real HTTP call to Binance for orderbook.
  Unit tests must mock the wrapped adapter; contract tests will hit
  the real testnet endpoint. Mitigation: `respx` fixtures for unit,
  `@pytest.mark.contract` gate for real calls.

## Dependencies on other changes

- **ADR-003**: not modified — this change operationalises §01
  (hexagonal boundary) and §04 (config surface) without breaking
  either.
- **ADR-009 §6 dual-source rule**: this change dogfoods the rule —
  both this `openspec/changes/shadow-mode-foundation/change.md` AND
  `docs/decisions/2026-08-24-shadow-mode-architecture.md` exist as
  companion artifacts.
- **#134 (SQLite ledger)**: prerequisite for the `hermes-shadow.db`
  path. Status: merged. Shadow repository extends the same schema.
- **#193 (Kraken public adapter)**: independent — but shadow-mode
  wrapper must eventually wrap Kraken too. Follow-up issue after this
  ships.
- **#148 (Binance testnet trading)**: BLOCKS on this change — the
  testnet trading path will be wired THROUGH `ShadowGate` from day
  one (sandbox+testnet carveout).
- **#25 (paper-trading engine)**, **#26 (backtest harness)**: BLOCK
  on this change — both consume the same `ShadowGate` pattern.
- **#194 (DoR/DoD enforcement)**: independent — proposal awaits
  operator review. This change dogfoods the openspec-first
  discipline in advance of the enforcement landing.
- **External settings changes**: none. All config lives in existing
  env-var surfaces.
