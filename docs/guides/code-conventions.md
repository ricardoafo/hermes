# Code Conventions

**Type**: Guide (living document — PR any change)
**Owners**: Code Owner (product owner, sole reviewer)
**Applies to**: everyone (and every AI agent) writing production code in this repo.
**Description**: Design patterns, error handling, immutability, factories, async, performance, polyglot posture, testing, time handling, event schema versioning, feature flags, engine pause, migrations, and public/internal API conventions.

This guide is the code-level counterpart to ADR-003 (architecture). ADRs say what SHAPES the system; this guide says what SHAPES each line of code. When ADRs and this guide disagree, ADRs win — flag the conflict and file a PR to reconcile. When this guide is silent on a decision, ask.

Research and evidence backing every decision here lives in engram at `phase-1-code-conventions-research-2026-08-23`. If a decision feels arbitrary, cross-check the research — most decisions have a real reason.

## 1. Design patterns catalog

The patterns we USE (with hermes examples). Anything not in this list is a candidate for pushback.

- **Hexagonal / Ports & Adapters** (ADR-003 §01) — the meta-pattern. Domain and application layers never import from adapters; ports define contracts, adapters implement. Enforced by `import-linter` (three `forbidden` contracts in `pyproject.toml`).
- **Value Object** — immutable, equality-by-value, no identity. `Money`, `Price`, `Weight`, `Pair`, `Quantity`, `LotId`. Implemented as `@dataclass(frozen=True, slots=True)`. Two Money instances with same amount + currency are equal.
- **Repository** — adapter that presents a domain-level view over persistent storage. `LedgerPort` is the interface; `SqliteLedger` in `adapters/ledger/` is the concrete. Repositories return domain objects, never DTOs.
- **Strategy** — interchangeable algorithms behind a common interface. `SignalEngine` port with `Rebalancer` and `Donchian` implementations. Application layer picks per config; both share the same `emit_signals(...)` shape.
- **Command** — a use case = a command. `RebalanceUC.execute(portfolio, target)` reads inputs, writes events, returns `Result`. One class, one method (`execute`), constructor injection for dependencies.
- **Constructor Dependency Injection** — every use case + adapter receives its ports through `__init__`. No service locator, no global registry, no lazy factory that hides the graph. Composition happens in `entrypoints/`.
- **Adapter** — wraps external system, implements a port. Adapters can raise; domain can't. Naming: `{ExternalSystem}Adapter` (e.g., `BinanceAdapter`, `SlackAdapter`).

**Anti-patterns**: singleton (except via constructor injection of shared instance), service locator, factory method as sole constructor, godlike facades that hide the port graph.

## 2. Error handling — hybrid Result / exceptions

**Domain returns `Result[T, DomainError]`**. Adapters raise typed exceptions from `HermesError` hierarchy. Application layer converts adapter exceptions → domain Result.

Split by layer because:

- Domain errors are EXPECTED OUTCOMES (currency mismatch, insufficient balance, invalid lot). Making them visible in type signatures via `Result` means mypy catches unhandled cases. Critical for a real-money system.
- I/O errors ARE exceptional (network drops, exchange 500s, ledger disk-full). Typed exception hierarchy matches `python-binance` / `ccxt` / `NautilusTrader` — three production financial systems. Traceback stays clean.

**Example (domain)**:

```python
# src/hermes/domain/value_objects/money.py
from returns.result import Result, Failure, Success
from hermes.domain.errors import CurrencyMismatch


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: Currency

    def add(self, other: "Money") -> Result["Money", CurrencyMismatch]:
        if self.currency != other.currency:
            return Failure(CurrencyMismatch(self.currency, other.currency))
        return Success(Money(self.amount + other.amount, self.currency))
```

**Example (adapter)**:

```python
# src/hermes/adapters/exchanges/binance_adapter.py
from hermes.domain.errors import NetworkError, ExchangeError


class BinanceAdapter:
    async def get_portfolio(self) -> Portfolio:
        try:
            response = await self._client.get_account()
        except httpx.NetworkError as e:
            raise NetworkError(f"Binance /account: {e}") from e
        if response.status_code >= 500:
            raise ExchangeError(f"Binance 5xx: {response.text}")
        return self._parse_portfolio(response.json())
```

**Application-layer conversion**:

```python
# src/hermes/application/rebalance_uc.py
try:
    portfolio = await self._exchange.get_portfolio()
except NetworkError as e:
    return Failure(TemporaryUnavailable(str(e)))
```

**Hierarchy shape**:

```text
HermesError
├── DomainError
│   ├── CurrencyMismatch
│   ├── InvalidLot
│   └── ... (all Failure() payloads)
└── InfraError
    ├── NetworkError
    ├── ExchangeError
    └── LedgerError
```

**Enforcement**: mypy `--strict` catches unwrapped `Result`. `import-linter` prevents domain from importing adapter exception types.

**Anti-patterns**: bare `except Exception`; catching + re-raising as `HermesError` in domain code (domain should not know about exceptions); `Result` in adapters (adds noise where exception is natural).

Reference: engram `phase-1-code-conventions-research-2026-08-23` § Topic 1.

## 3. Immutability — frozen dataclass everywhere in domain

`@dataclass(frozen=True, slots=True)` for every value object and event dataclass in `src/hermes/domain/`. `slots=True` reduces memory + prevents attribute drift. `frozen=True` means `__hash__` works and mutations raise.

**No pydantic in domain**. Pydantic is I/O framework — belongs at boundaries (config loading via `pydantic-settings`, JSON serialization at adapter edge). Domain must stay dep-light so it's the easiest layer to reason about + test.

**Example**:

```python
@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: Currency
    # Immutable — Money(100, EUR).amount = 200 raises FrozenInstanceError
```

**Application layer**: use dataclasses too. Avoid mutable state on use case instances (`self._counter += 1` = smell; put counter in a domain object).

**Adapters**: pydantic OK for parsing external JSON (schema validation). But parse into domain value objects at method exit; don't leak pydantic types up.

**Enforcement**: mypy catches unfrozen dataclass mutation attempts. Custom import-linter rule could forbid `pydantic` imports in `domain/` (not shipped now; ratchet in later).

## 4. Factory methods vs constructors

**Direct constructor** for known-good inputs: `Money(Decimal("100"), Currency.EUR)`.

**`from_*` classmethods** for parsing external input: `Money.from_str("100.50 EUR")` returns `Result[Money, DomainError]` because parsing can fail.

**Neutral element** constructor: `Money.zero(Currency.EUR)`.

**Rule**: any classmethod that can fail returns `Result`. Any that cannot returns the value directly.

**Naming**:

- `from_str(s)` — parse from string
- `from_dict(d)` — parse from dict (rarely used; adapters usually convert)
- `zero(currency)` — construct neutral element
- `one(currency)` — construct multiplicative identity if meaningful

**Anti-patterns**: giant `Money(**kwargs)` constructors that swallow unknown fields; static factory methods that hide required dependencies.

## 5. Async policy — AnyIO on asyncio

**Domain is sync**. No `async def` in `src/hermes/domain/`.

**Adapters are async** when doing I/O. Use `anyio` (structured concurrency built on asyncio). AnyIO's `TaskGroup` gives Trio-style exception propagation + cancellation. Powers Prefect. Native to Python 3.11+.

**Bridging async adapter → sync domain**: use `anyio.streams.memory.MemoryObjectStream` (bounded queue), or `anyio.to_thread.run_sync` for blocking calls.

**Example**:

```python
# src/hermes/adapters/exchanges/binance_adapter.py
import anyio
from anyio.streams.memory import MemoryObjectSendStream, MemoryObjectReceiveStream


class BinanceAdapter:
    async def stream_klines(
        self,
        pair: Pair,
        sink: MemoryObjectSendStream[Kline],
    ) -> None:
        async with self._ws_client(pair) as ws:
            async for msg in ws:
                kline = self._parse_kline(msg)
                await sink.send(kline)


# Application-layer bridge
async def consume_klines(
    adapter: BinanceAdapter,
    pair: Pair,
    on_kline: Callable[[Kline], None],  # sync callback into domain
) -> None:
    send, recv = anyio.create_memory_object_stream[Kline](max_buffer_size=100)
    async with anyio.create_task_group() as tg:
        tg.start_soon(adapter.stream_klines, pair, send)
        async for kline in recv:
            on_kline(kline)  # sync domain call
```

**Common gotchas**:

1. **Missed messages during disconnect** — no built-in replay. Add a sequence-number check + resync logic per adapter.
2. **`await exchange.close()` mandatory** — connections leak otherwise.
3. **Jittered backoff** — thundering herd if all reconnects fire at the same time after a network blip.
4. **Weight-based rate limits** (Binance) — track weight windows in the adapter, not RPS.
5. **Unshielded task exceptions kill the loop** — every `tg.start_soon` should have its own try/except or be inside a shielded scope for critical work.

**Rate limiting**: adapter tracks Binance's weight-per-minute budget; back off when < 20% remaining. Log warnings.

**Enforcement**: convention only for now. If we ever add lots of adapters, ratchet in a custom ruff rule to catch `time.sleep()` calls in `async def`.

Reference: engram `phase-1-code-conventions-research-2026-08-23` § Topic 2.

## 6. Performance mindset — measured triggers, no vibes

**Correctness first (invariants I-01, I-02); performance is a subordinate constraint.** But we WILL profile.

**Data types by concern**:

- **`Decimal`** — anything reconciled to the ledger (position sizes, cost basis, fees, P&L). Slow (~10-100× vs float) but mandatory. Precision matters.
- **`float` / `numpy`** — indicators, correlations, statistics, backtest replays where a tiny numerical drift is acceptable.
- **`polars`** (Rust core, auto-parallelized) — DataFrame ops when they land (kline aggregation, backtest replay, weekly report). 13.75× join, 10.9× select, 5.9× group-by vs pandas at 1M rows.
- **Convert at domain boundary** — float in signal engine → Decimal when it becomes an order.

**Profilers**:

- **`py-spy`** — default. Sampling, Rust-written, near-zero overhead. Attach to running prod PID: `py-spy record --pid <pid> --duration 60 --output trace.svg`. Keep it in the runner image.
- **`memray`** (Bloomberg) — allocation and fragmentation deep-dives. Not default; grab when memory issues suspected.
- **`austin`** — TUI live view for local dev.
- **`cProfile`** — dev only. Overhead too high for prod.

**Hot-spot triggers** (measured, not vibes — cross when the number crosses):

| Trigger | Escalate to |
| --- | --- |
| Single strategy tick > 10ms | py-spy investigation |
| Rebalance solver > 100ms/call | Rust extension via PyO3 |
| Backtest 1y 1m klines > 30s | polars first → Numba inner loop → PyO3 |
| WebSocket decode > 30% CPU on one core | `orjson` first → PyO3 Rust decoder |
| Working set > 1 GB | memray to find leak / fragmentation |

**Vectorization when data shape justifies**: polars for kline arrays, numpy for signal computations. Don't reach for either until profiling shows the loop is the bottleneck.

**Caching**: `functools.lru_cache` fine for pure functions with bounded key space. NEVER on methods (`self` reference pins the instance). Always set `maxsize` explicitly.

**What we won't do prematurely**: Numba, Cython, mypyc, Nuitka. Add only when a specific hot spot justifies the tooling cost.

Reference: engram `phase-1-code-conventions-research-2026-08-23` § Topic 3.

## 7. Polyglot posture — Rust YES (when triggered), Go NO for hermes

**PyO3 Rust extension** for a proven hot spot. Marketcalls quant guidance: "profile, find the 5% consuming 80% of runtime, replace only that with a PyO3 module." Reference: `polars` (Rust core + Python API + pyo3-polars plugins). NautilusTrader shipped as Rust core + Cython + Python control plane. Speedup: 113-154× vs Python, 2× vs Numba, ~100× effort — earn it, don't guess.

**Numba as intermediate step** — 56-135× on pure-numeric hot loops. Cheap to try before reaching for Rust. Best when: pure Python numeric loop with no I/O.

**Go microservice: NO for hermes.** Solo dev + single VPS + hexagonal = zero real benefit vs cost. gRPC schema drift + deploy surface + separate service lifecycle add complexity for nothing. Only justified if we ever grow past solo (team parallelism) or split across multiple VPSes.

**Cython / mypyc**: legacy. New hot spots go PyO3 (memory safety + modern tooling).

**Hexagonal architecture as the enabler**: swapping a Python `Rebalancer` implementation for a Rust PyO3 extension is an ADAPTER change — the port stays the same, use cases don't know. Same for a Rust WS decoder replacing the Python one.

**Anti-patterns**: adding Rust before py-spy proves the hot spot; adding Go microservice because "microservices are good"; premature polyglot = 2× maintenance for imaginary wins.

Reference: engram `phase-1-code-conventions-research-2026-08-23` § Topic 4.

## 8. Testing patterns — property + mutation + contract

**Test-double taxonomy** (choose per adapter type):

- **Fake** — behavior parity, no I/O. Example: `Ledger` fake = in-memory SQLite mirror.
- **Stub** — deterministic return values. Example: `Clock` stub = `freezegun.freeze_time("2026-08-23")`.
- **Mock** — assert side effects. Example: `Notifier` mock = record `notify_urgent()` calls, assert count + args.
- **Spy** — verify order + no interference. Example: `EventStore` spy = observe event emission order without altering.
- **Contract test via `respx`** — real API schemas, mocked wire. Snapshot the PARSED domain object with `syrupy` (not raw JSON — catches parser regressions cleanly).

**Hypothesis strategy composability** — small building blocks composed via `@st.composite`:

```python
# tests/strategies/money.py
from hypothesis import strategies as st


@st.composite
def positive_decimal_strategy(draw, *, max_digits: int = 18, places: int = 8): ...


@st.composite
def money_strategy(draw, *, currency: Currency | None = None) -> Money:
    amount = draw(positive_decimal_strategy())
    cur = currency or draw(st.sampled_from(list(Currency)))
    return Money(amount, cur)
```

Register strategies via `st.register_type_strategy(Money, money_strategy())` so any dataclass field of type `Money` gets free hypothesis coverage.

**Trading-specific property invariants** (must-have — every one of these tests reveals a real bug class):

1. **FIFO cost basis conservation**: after any sequence of buys/sells, `remaining_qty * avg_cost == unrealized_basis`
2. **Currency conservation**: `sum(deltas) == final - initial` for every asset, always
3. **Rebalance idempotence**: `rebalance(rebalance(pf, target), target) == rebalance(pf, target)` up to dust threshold
4. **Fee monotonicity**: adding fees never increases realized P&L
5. **OHLC invariants**: `low ≤ open,close ≤ high`, monotonic timestamps

Reference for #1-#5: Susan Potter's quant-PBT article (URL in engram research).

**Mutation testing (mutmut)** — targets `hermes.domain.**` only. Must-kill mutations for money math:

- `+`/`-` swap on arithmetic
- `>` → `>=` on boundary predicates (fee thresholds, slippage caps, risk limits)
- `*` → `/` typos
- Off-by-one on FIFO lot pop indices

100% kill on `hermes.domain.**` money paths. Survivor at `>=` in a risk-limit predicate = a real fraud slip; treat as SEV-1.

Reference: engram `phase-1-code-conventions-research-2026-08-23` § Topic 5.

## 9. Time handling — UTC internally, Europe/Madrid at edges

**Every timestamp inside hermes is UTC** (timezone-aware datetime with `tzinfo=UTC`). No naive datetimes anywhere.

**Convert to Europe/Madrid ONLY at output edges**: CLI/TUI display, tax reports (Modelo 100/721), Telegram/Slack messages that mention wall-clock times.

**Helpers in `src/hermes/infrastructure/time.py`**:

- `now_utc() -> datetime` — current instant with UTC tzinfo
- `to_madrid(dt) -> datetime` — convert UTC datetime to Europe/Madrid
- `from_madrid(dt) -> datetime` — convert Europe/Madrid datetime to UTC

**Why UTC internally**: all exchange data is UTC (Binance, Kraken). Spanish tax reports need Madrid. If we're Madrid internally we break exchange interop; if we're UTC internally we convert once per output.

**DST awareness**: Spain switches CET (UTC+1) / CEST (UTC+2) in late March + late October. `to_madrid()` MUST use `zoneinfo.ZoneInfo("Europe/Madrid")` (stdlib since Python 3.9) — never `pytz` (deprecated for new code), never `timedelta(hours=1)` (breaks around DST).

**Example**:

```python
from hermes.infrastructure.time import now_utc, to_madrid

occurred_at = now_utc()  # 2026-08-23T14:22:03.412+00:00
madrid_display = to_madrid(occurred_at)  # 2026-08-23T16:22:03.412+02:00 (CEST)
```

**Enforcement**: convention only. Any `datetime.now()` (naive) in the codebase is a bug — grep pre-commit rule could catch this later.

**Anti-patterns**: naive datetimes, storing Madrid time in the ledger, `datetime.utcnow()` (deprecated; returns naive), `time.time()` as timestamps.

## 10. Event schema versioning + upcaster chain

**Every domain event dataclass has `__event_schema_version__: int` class attribute**. Ledger serializer writes it; deserializer reads it + dispatches through an upcaster chain (`v1 → v2 → v3`).

**Schema changes are NEVER breaking** — always additive with default values or upcaster.

**Example**:

```python
@dataclass(frozen=True, slots=True)
class TradeExecuted(DomainEvent):
    __event_schema_version__: ClassVar[int] = 2  # v2 adds `fee_currency`
    pair: Pair
    qty: Quantity
    price: Price
    fee: Money
    fee_currency: Currency  # NEW in v2; upcaster provides default
```

**Upcaster registry** (`hermes.infrastructure.event_upcasters`):

```python
UPCASTERS: dict[str, dict[int, Callable[[dict], dict]]] = {
    "TradeExecuted": {
        1: lambda d: {**d, "fee_currency": d["fee"]["currency"]},  # v1 → v2
    },
}
```

**Why**: SQLite events table will hold 5+ years of data. Once v1 events are written, v2 events must deserialize them. Establishing the convention now is cheap; retrofitting after 10k rows means writing upcasters + backfilling. Same class of decision as database migrations.

**Rules**:

- Adding a NEW event class → straightforward, no upcaster needed
- Adding a field to existing event → increment version + add upcaster
- Removing a field → NEVER; deprecate + ignore in newer readers
- Renaming a field → NEVER; add new field + upcaster, deprecate old

**Enforcement**: mypy checks that `__event_schema_version__` is set. Test asserts subclass without version override fails at class-definition time (via `__init_subclass__`).

## 11. Feature flags — `TradingMode` enum + strategy toggles

**Global mode**: `TradingMode = Enum("paper", "shadow", "live")` — guarded ONCE at use-case boundary, not sprinkled across domain.

**Per-strategy toggles**: `config/strategies.yaml` has `enabled: true/false` per strategy. Config loading (issue #103) reads them.

**Default mode is PAPER** (I-03 invariant: every write path defaults dry-run).

**Example**:

```python
# src/hermes/application/rebalance_uc.py
class RebalanceUC:
    def __init__(
        self,
        *,
        mode: TradingMode,
        exchange: ExchangePort,
        ledger: LedgerPort,
    ) -> None:
        self._mode = mode
        self._exchange = exchange
        self._ledger = ledger

    async def execute(
        self, portfolio: Portfolio, target: TargetWeights
    ) -> Result[Decision, DomainError]:
        decision = self._decide(portfolio, target)  # pure domain
        if self._mode is TradingMode.LIVE:
            await self._exchange.place_orders(decision.orders)
        elif self._mode is TradingMode.SHADOW:
            await self._log_shadow(decision.orders)  # would-have-been
        # PAPER: no external calls
        await self._ledger.record(decision.event)
        return Success(decision)
```

**Composition rule**: `TradingMode` check happens ONCE, at the use-case entry. Domain functions never check mode.

**Enforcement**: convention. If we ever see `if mode ==` in `hermes.domain.*` → bug.

**Anti-patterns**: `if config.mode == "live"` scattered in domain; boolean `is_live` flag (three states, not two — use enum); global `PAUSED` flag.

## 12. Engine pause — `PausableUseCase` mixin

**ADR-010 CVE playbook mandates `hermes engine pause`**. Making pause a first-class use-case concept means every trading path automatically inherits pause capability.

**Mixin**:

```python
# src/hermes/application/base.py
class PausableUseCase:
    _paused: bool
    _pause_reason: str | None = None

    def pause(self, *, reason: str) -> None:
        self._paused = True
        self._pause_reason = reason
        # Emits EnginePaused domain event

    def resume(self) -> None:
        self._paused = False
        self._pause_reason = None
        # Emits EngineResumed domain event

    def _check_not_paused(self) -> Result[None, EnginePaused]:
        if self._paused:
            return Failure(EnginePaused(self._pause_reason or "unknown"))
        return Success(None)
```

**Use case usage**:

```python
class RebalanceUC(PausableUseCase):
    async def execute(self, ...) -> Result[Decision, DomainError]:
        if err := self._check_not_paused():
            return err  # short-circuit if paused
        # ... rest of logic
```

**Event**: `EnginePaused(reason)` and `EngineResumed()` are domain events; ledger records them; notification router sends Telegram alert on both transitions.

**Rules**:

- Pause is per-use-case, not global (allows pausing Rebalancer while Signal engine keeps computing)
- Pause always has a reason string (for audit trail)
- Resume without prior pause is a no-op
- CLI: `hermes engine pause --reason "cve-2026-N"` and `hermes engine resume`

## 13. Schema migrations — forward-only, numbered SQL files

**`src/hermes/adapters/ledger/migrations/`** holds the ledger's migrations as
SQL files, named by their UTC creation time (`YYYYMMDDTHHMM_<slug>.sql`; the
older `0001` to `0006` files keep their numbers and sort first):

```text
migrations/
├── 0001_initial_schema.sql
├── ...
├── 20261004T0300_portfolio_marks.sql
└── ...
```

**Rules** ([ADR-006](../architecture/adr-006-versioning-strategy.md) §2):

- Forward-only: no down-migrations. Rolling back means restoring the snapshot
  taken just before the migration, then fixing forward with a new migration.
- Never edit an applied migration. Add a new one.
- Each migration runs in one transaction that the runner opens, so a file
  never writes `BEGIN`, `COMMIT` or `SAVEPOINT`;
  `scripts/dev/migration-sql-check.py` enforces the name and this rule.

How migrations are applied, which ones need `hermes migrate`, and how to roll
one back are in [ledger-migrations.md](ledger-migrations.md).

## 14. Additional cross-phase conventions

### 14a. Public vs internal API

- **Names starting with `_`** are internal to their module. Don't import them elsewhere.
- **Every package `__init__.py`** defines `__all__` listing intended-public names.
- Ruff enforces `F401` (unused imports). Custom rule for `_prefix` violations at package boundary — landing when we hit the first violation.

Standard Python idiom. Zero-cost convention now saves grepping "what's internal" later.

### 14b. LLM adapter safety (Phase 3 shape, decided now)

LLM Supervisor (Phase 3 #34) will use MCP. Read-only enforcement is STRUCTURAL:

- LLM adapters receive a `ReadOnlyLedgerView` port — this port has no write methods, so structurally cannot mutate the ledger.
- Prompts are sanitized at the MCP boundary (`hermes.adapters.llm.mcp_sanitizer`) — allowlist of allowed data shapes; unknown fields dropped.
- Every LLM call is logged with full prompt + response to `llm_audit` table (per ADR-003 §03; gated behind Phase 3 flag today).

**Why decided now**: sets the port shape that all reads will use, so LLM adapters slotting in don't require domain refactor.

### 14c. Report generation — CSV / PDF / XML pattern

Every report is:

1. **Dataclass** representing the report structure (e.g., `WeeklyRebalanceReport(rows=[...])`)
2. **Serializer function** per output format (`to_csv(report) -> str`, `to_pdf(report) -> bytes`, `to_xml(report) -> str`)
3. **Snapshot test with `syrupy`** locks the expected output shape

Applies uniformly: weekly rebalance report (Phase 2 #33), monthly reconciliation (Phase 3 #35), Modelo 100/721 tax filings (Phase 3 #36).

**Rules**:

- Reports use `Decimal` for all money math (no float drift in tax filings)
- Reports use `to_madrid()` for all display times (Spanish tax = Madrid clock)
- Serializers are pure functions (no I/O; adapters do the file write)

### 14d. CLI UX conventions

- **Subcommand naming**: verb-noun (`hermes engine pause`, `hermes ledger verify`, `hermes report weekly`). Avoid single-word commands.
- **Output default**: pretty (human-readable with `rich`). `--json` flag for machine-readable.
- **Exit codes**: 0 success, 1 general error, 2 usage error, 64 data error, 65 no data. Match sysexits.h where possible.
- **Long output paginated**: `hermes ledger show` for many rows should page (via `rich.pager`).
- **Every command has `--help` with examples**.

### 14e. Configuration reload — restart required, no hot-reload

Config changes require `systemctl restart hermes-*.service`. No hot-reload complexity.

**Why**: hot-reload is a source of hairy bugs (partial reload, config-in-flight during trade). Restart is 500ms; trading is paused during restart anyway (via systemd). Total cost negligible.

**Runbook implication**: `hermes engine pause` → edit config → `systemctl restart hermes-*` → verify → `hermes engine resume`.

## Where this guide lives in the ecosystem

- **ADR-003** — architecture (hexagonal layers, invariants)
- **ADR-007** — testing strategy (tiers, coverage thresholds, mutation testing)
- **ADR-008** — observability strategy (structlog, journald, correlation IDs)
- **ADR-009** — documentation strategy (this guide is a `docs/guides/` surface)
- **`AGENTS.md`** — project-tracker hygiene, PR flow rules
- **`docs/guides/dev-workflow.md`** — git/gh discipline
- **`docs/guides/prose-style.md`** — prose conventions (comments, docstrings, PR bodies)

## Explicitly deferred (add when triggered)

- **Multi-tenancy patterns** — solo dev per ADR-003; skip until family/friends aggregate accounts happen
- **Multi-exchange registry pattern** — 2 exchanges (Binance + Kraken cold) don't need one; simple `if adapter == "primary"` fine
- **API stability tiers** (experimental / stable / deprecated) — solo project, single consumer; skip until team expansion
- **Hot config reload** — no compelling use case
- **Feature-flag lifecycle** — flag deprecation SLA needed only when we have many flags
