# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)
as set out in [ADR-022](docs/architecture/adr-022-release-versions-and-deploy-cadence.md)
and [ADR-027](docs/architecture/adr-027-zero-major-versions-until-real-capital.md)
(`0.y.z` until a champion trades real money).

Every `feat`, `fix`, `refactor` and `perf` PR adds a fragment under
[`changelog.d/`](changelog.d/README.md), grouped as Added / Changed / Deprecated /
Removed / Fixed / Security. PRs that are pure docs, chore, or CI may skip. A release PR
(`scripts/dev/cut-release.py`) compiles the fragments into a dated section.

## [Unreleased]

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

[Unreleased]: https://github.com/ricardoafo/hermes/commits/main
[1.0.0-shadow.1]: https://github.com/ricardoafo/hermes/releases/tag/v1.0.0-shadow.1
