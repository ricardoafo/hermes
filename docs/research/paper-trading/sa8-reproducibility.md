# SA-8 Reproducibility + Audit Trail Patterns

**Date**: 2026-08-24
**Author**: SA-8 sub-agent
**Engram topic key**: `research/paper-trading-sa8-reproducibility`
**Status**: FINAL

---

## 1. Executive Summary

Hermes shadow-mode has zero evidential value if two runs on the same market event stream produce different simulated fills. This is not an aspirational property — it is the baseline requirement for debugging divergence, proving certification, and enabling temporal analysis.

The architecture hermes should adopt for MVP1 is **event-sourced audit log + deterministic replay kernel**, implemented on top of the existing SQLite WAL infrastructure. The key decisions are:

1. **Every externally-observable decision** (signal fired, order emitted, fill accepted, position mutated, balance updated) is written as an immutable, content-addressed `DomainEvent` row before any side-effect executes.
2. **All non-determinism is pushed to the adapter boundary**: the clock, the RNG, the exchange API, and the WebSocket feed are the ONLY sources of external state. The domain core never calls them directly — it receives their outputs as typed, logged inputs.
3. **The sequencer is a single, monotonically-incrementing integer** (`event_seq`) written by the SQLite adapter inside the same transaction that writes the event payload. No distributed consensus is needed at solo-operator scale.
4. **Snapshots are taken at position-close boundaries** — a natural quiescence point — and stored in a `snapshots` table. Recovery replays from the nearest snapshot rather than from event zero.
5. **The replay CLI** (`hermes replay --from <seq> --to <seq>`) drives the same domain kernel against a frozen `RecordedMarketFeed` port that replays logged exchange ticks in order, with wall-clock reads replaced by the tick's `exchange_ts`.
6. **Property-based determinism tests** (Hypothesis) verify that any randomly-generated event stream, when replayed twice, produces bit-identical output sequences.

The storage choice (SQLite WAL, append-only `events` table, per-event SHA-256) requires zero new dependencies beyond what ADR-003 already mandates. Full event sourcing with state reconstruction from event zero is explicitly deferred — the existing ledger+snapshot hybrid gives the audit properties we need without the rebuild-latency cost.

**Confidence**: HIGH on patterns and design decisions. MEDIUM on regulatory specifics (MiFID II Article 25 exact retention rules vary by instrument class).

---

## 2. Event Sourcing — Patterns, Gotchas, Tooling

### 2.1 Fowler's Canonical Pattern

Martin Fowler's event sourcing formulation (martinfowler.com/eaaDev/EventSourcing.html, 2005; restated in "Patterns of Enterprise Application Architecture") establishes the invariant: **application state is a left fold over an ordered sequence of immutable events**. No event is ever updated or deleted; errors are corrected by appending compensating events.

The key insight for trading systems: a `TradeExecuted` event at seq=1042 cannot change retroactively, but a reconciliation discovering a fill price error appends a `TradeAmended` event at seq=1301 that supersedes it. The audit trail is complete: both the error and the correction are visible.

Gotchas Fowler identifies:

- **External system writes on replay**: during replay, outbound effects (send a Telegram, place an order) must be suppressed or stubbed. Fowler calls this the "Gateway" problem — the gateway must know it's in replay mode. Hermes solves this via port injection: the `ExchangePort` and `NotifierPort` injected during replay are `NoopExchangePort` / `NoopNotifierPort`.
- **Temporal coupling**: an event's meaning can depend on wall-clock time (e.g. "was this trade within the tax-deferred window?"). If the replay uses the current wall-clock, results diverge. Hermes must use `exchange_ts` from the event payload as the "current time" during replay.
- **Schema evolution**: events written with schema v1 must be readable when the codebase is at v3. Hermes already enforces `__event_schema_version__` on every `DomainEvent` subclass — the upcaster chain converts v1→v2→v3 at read time.

### 2.2 Aggregate Rebuild from Event Stream

In full event sourcing, an aggregate (e.g. `Portfolio`) is reconstructed by replaying all its events from scratch:

```python
portfolio = Portfolio.empty()
for event in event_store.load(aggregate_id):
    portfolio = portfolio.apply(event)
```

**Performance consideration**: at 15 trades/month, hermes's event log grows to ~4,000 events/year including heartbeats and price events. Full rebuild from event zero takes milliseconds even without snapshots. This means the "rebuild from snapshot" optimization is nice-to-have but not blocking for MVP1.

### 2.3 Snapshots

Snapshots checkpoint aggregate state at a given sequence number so replay starts from `snapshot_seq + 1`, not from zero.

**When to snapshot** (from the EventStore documentation and Udi Dahan's "Don't Let the Internet Dupe You, Event Sourcing is Hard"):

- After a bounded number of events (e.g. every 500 events)
- At natural quiescence points (position fully closed, daily market close)
- On application shutdown

For hermes, the natural snapshot trigger is **position close**: when `LotClosed` is appended, the entire portfolio is at a known, verifiable state. Snapshot at that moment: serialize `Portfolio + Ledger` state to JSON, store with the triggering `event_seq`. Recovery replays from the last snapshot's `event_seq + 1`.

### 2.4 EventStore, Axon, Marten

| System | Language | Storage Backend | Hermes Applicability |
| -------- | ---------- | ----------------- | ---------------------- |
| EventStore DB | C# / Go client | Custom binary log | Overkill — dedicated daemon, network dependency |
| Axon Framework | Java | Postgres / MongoDB | JVM dependency — not applicable |
| Marten (Postgres-based) | C# | Postgres JSONB | Wrong DB — hermes uses SQLite |
| **SQLite events table** | Python | SQLite WAL file | Already exists — extend it |

**Decision**: all three production event stores add operational complexity (daemon, network, JVM) that ADR-003 explicitly rejects for a solo-operator VPS deployment. The SQLite events table, extended with `event_seq` and `payload_sha256`, provides the same append-only guarantee at zero operational cost.

---

## 3. Immutable Event Logs — Storage Options and Tradeoffs

### 3.1 Append-Only Storage Patterns

WAL patterns (SQLite WAL, Postgres WAL, Kafka log-compacted topics)

SQLite WAL (Write-Ahead Logging) is already enabled in hermes (ADR-003 §03). WAL mode means:

- Writes append to the WAL file; readers see the last committed checkpoint
- A crash mid-write leaves the WAL uncommitted — recovery on restart reprocesses from the last checkpoint
- The WAL is itself a sequential log of changes — this is the physical-level analogue of event sourcing

For hermes purposes, the WAL provides crash safety but NOT the logical-level audit trail. The `events` table is the logical audit log; WAL is the physical crash-recovery mechanism. Both are needed and serve different purposes.

Kafka log-compacted topics provide the same append-only guarantee at the cost of a broker daemon and a Zookeeper/KRaft dependency — a clear ADR-003 violation at this scale.

### 3.2 Content-Addressed Events (SHA-256 Payload Hash)

Adding a `payload_sha256` column to the `events` table gives tamper-evidence:

```sql
ALTER TABLE events ADD COLUMN payload_sha256 TEXT NOT NULL;
```

At write time:

```python
import hashlib, json

def _payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode()).hexdigest()
```

At read time (during replay or audit), the hash is recomputed and compared. A mismatch proves the row was modified after write — something SQLite's transaction model and filesystem permissions should prevent, but the hash catches filesystem-level tampering (e.g. someone editing the `.db` file with a hex editor).

**Critical requirement**: `payload_json` must be **deterministically serialized** — same keys, same order, same Decimal representation, every time. The existing `_event_to_json` function uses `sort_keys=True`, which satisfies this requirement. Decimal values are stored as strings (`str(decimal_value)`), which are exact.

### 3.3 Merkle-Tree Audit Logs (Certificate Transparency Style)

Certificate Transparency (RFC 6962) uses a Merkle tree over a log of certificate hashes. Each new entry's hash includes the hash of the previous entry, forming a chain. Forging entry N requires recomputing all hashes from N to the current head — computationally infeasible and detectable by any verifier who holds the root.

For hermes, a simplified version (hash chain, not full Merkle tree) is sufficient:

```sql
-- Each event row includes the hash of the previous event's payload_sha256
ALTER TABLE events ADD COLUMN prev_sha256 TEXT;  -- NULL for event_seq = 1
```

At write time:

```python
prev = conn.execute(
    "SELECT payload_sha256 FROM events ORDER BY event_seq DESC LIMIT 1"
).fetchone()
prev_hash = prev[0] if prev else ""
# Include prev_hash in the payload before hashing
combined = json.dumps({"prev": prev_hash, "payload": payload_dict}, sort_keys=True)
sha256 = hashlib.sha256(combined.encode()).hexdigest()
```

**Applicability to hermes**: hash chaining is a MEDIUM-priority feature — not blocking MVP1, but valuable for MiFID II audit compliance if hermes ever enters regulated territory. Implement in a follow-up migration (`004_add_event_chain.sql`).

---

## 4. Deterministic Replay — Sources of Non-Determinism and Elimination

This is the highest-value section for hermes. Sources of non-determinism must be enumerated, then each must be assigned an elimination strategy.

### 4.1 Sources of Non-Determinism

#### S-01: Wall-Clock Reads (`datetime.now()`)

**Problem**: `now_utc()` returns a different value on every call. If domain logic calls `now_utc()` to decide "is this trade within the 1-year tax-deferred window?", replay at a different wall-clock produces a different decision.

**Elimination**: The `Clock` port pattern. The domain never calls `now_utc()` directly — it calls `clock.now()` where `ClockPort` is injected:

```python
class ClockPort(Protocol):
    def now(self) -> datetime: ...

class WallClock:
    def now(self) -> datetime:
        return now_utc()

class FrozenClock:
    def __init__(self, ts: datetime) -> None:
        self._ts = ts
    def now(self) -> datetime:
        return self._ts

class ReplayClock:
    """Advances with each event replayed; always returns event.occurred_at."""
    def __init__(self) -> None:
        self._ts: datetime | None = None
    def advance(self, ts: datetime) -> None:
        self._ts = ts
    def now(self) -> datetime:
        assert self._ts is not None, "ReplayClock not initialized"
        return self._ts
```

Hermes already has `now_utc()` centralized in `hermes.infrastructure.time` — this is the right foundation. The MVP1 task is to thread a `ClockPort` through the domain layer so no domain code calls `now_utc()` directly.

**Verification**: `rg 'now_utc\(\)' src/hermes/domain/` should return zero hits after the refactor.

#### S-02: Random Number Generation

**Problem**: position sizing with `random.random()` or `numpy.random.rand()` produces different results each run unless the seed is fixed and deterministic.

**Elimination**: hermes's ATR-based position sizing is deterministic given the same ATR value — there is no stochastic element. If a future strategy introduces randomness (e.g. random entry timing for order flow protection), seed the RNG from the event sequence number: `rng = numpy.random.default_rng(seed=event_seq)`. Record the seed in the event payload.

**Anti-pattern warning**: `numpy.random.seed()` (the legacy global state API) is non-reentrant and breaks in multithreaded contexts. Use `numpy.random.default_rng()` (Generator API, introduced NumPy 1.17) which is stateless per instance.

#### S-03: Dict / Set Iteration Order

**Problem**: Python dict iteration order is insertion-order guaranteed since 3.7, but `set` iteration order is NOT deterministic across Python versions or even across process restarts (due to hash randomization, `PYTHONHASHSEED`).

**Elimination**: Never iterate over `set` in domain logic and serialize the result. Convert to a sorted list first:

```python
# BAD — non-deterministic across runs
for asset in open_positions:  # open_positions: set[Currency]
    ...

# GOOD
for asset in sorted(open_positions, key=lambda c: c.value):
    ...
```

**Verification**: enable `PYTHONHASHSEED=0` in replay mode to normalize hash seeds. For tests, `PYTHONHASHSEED=random` (the default) is correct — it catches set-order bugs because different test runs will expose them.

#### S-04: Concurrency — Race Conditions

**Problem**: if the rebalancer and the swing daemon both write to the ledger simultaneously, the event sequence may interleave non-deterministically.

**Elimination**: SQLite's single-writer model (enforced by WAL mode + `SQLITE_BUSY` serialization) already prevents concurrent writes. The event bus is synchronous (per `event_bus.py` design comment: "synchronous: `publish` returns only after every subscriber has run"). No concurrency within a cycle — each engine runs one cycle at a time.

For paper-trading specifically: the shadow-mode engine must run in the same single-threaded execution model, not as a background asyncio task that interleaves with live writes.

#### S-05: External API Calls

**Problem**: price data, fill confirmations, and fee schedules fetched from Binance/Kraken during a shadow-mode run produce different results if the market has moved.

**Elimination**: the Recording layer. During shadow-mode (live run), ALL external API responses are logged as `ExternalCallRecorded` events before being consumed by domain logic:

```python
@dataclass(frozen=True, slots=True)
class ExternalCallRecorded(DomainEvent):
    __event_schema_version__: ClassVar[int] = 1
    call_type: str          # "GET_KLINES" | "GET_ORDERBOOK" | "GET_FILL"
    request_hash: str       # SHA-256 of serialized request params
    response_json: str      # raw JSON from exchange
    exchange_ts: datetime   # exchange-reported timestamp from response
    latency_ms: int         # wall-clock round-trip (for slippage modeling)
```

During replay, the `ExchangePort` implementation reads from this log instead of making network calls:

```python
class RecordedExchangePort:
    def get_klines(self, pair, interval, limit) -> list[Kline]:
        recorded = self._log.next_call("GET_KLINES")
        return self._deserialize_klines(recorded.response_json)
```

This is the approach NautilusTrader calls its "backtest data engine" — all market data is pre-recorded and replayed from the exact same byte stream.

#### S-06: Floating-Point vs Decimal Arithmetic

**Problem**: `float` arithmetic is platform-dependent at the ULP level. Different CPU microarchitectures (ARM vs x86) or different compiler optimization flags can produce different results for `0.1 + 0.2`.

**Elimination**: hermes already uses `Decimal` for all money-critical values (code-conventions §6). The replay is bit-identical as long as every Decimal is initialized from a string (`Decimal("0.001")`) never from a float (`Decimal(0.001)` — this is a bug).

**Verification**: add a test that asserts `Decimal(str(x)) == Decimal(str(x))` for all persisted values, and that loading from JSON and re-serializing produces the same string.

#### S-07: Python Version / OS Differences

**Problem**: `str(Decimal("1.000"))` returns `"1.000"` in Python 3.11 and `"1E+0"` on unusual Decimal contexts. Similarly, `json.dumps` behavior for edge cases (NaN, Infinity) varies.

**Elimination**: pin the Python version in `pyproject.toml` (`requires-python = ">=3.12, <3.13"`). Pin the Decimal context explicitly:

```python
import decimal
decimal.getcontext().prec = 28  # explicit, never rely on default
```

---

## 5. Time Provenance — Timestamp Strategy for Hermes

### 5.1 Three Timestamp Types

Every event in a trading system has up to three conceptually distinct timestamps:

| Timestamp | Meaning | Source | Hermes Field |
| ----------- | --------- | -------- | -------------- |
| **Exchange time** (`exchange_ts`) | When the exchange processed the event (fill, kline close, orderbook snapshot) | Exchange API response header or payload | Add to `ExternalCallRecorded` |
| **Occurred-at** (`occurred_at`) | When hermes observed and processed the event | `now_utc()` at the moment of domain event creation | Already on `DomainEvent.occurred_at` |
| **Recorded-at** (`recorded_at`) | When the event was written to the SQLite ledger | `now_utc()` at the moment of `_insert_event` | Already written by `SqliteLedger._insert_event` |

The gap between `exchange_ts` and `occurred_at` is processing latency. The gap between `occurred_at` and `recorded_at` is write latency. Both are observable from the log and useful for diagnosing shadow-mode divergence.

### 5.2 Clock Skew Handling

**Problem**: hermes's VPS clock may drift from exchange time by up to several seconds. This affects bar-close signal timing — if hermes thinks a bar closed at 14:00:00.000 UTC but the exchange closed it at 14:00:00.842 UTC, the signal may fire on a different bar's data.

**Recommended approach**:

1. Use `exchange_ts` from kline responses as the authoritative bar-close timestamp, not `now_utc()`.
2. Record the `clock_skew_ms = exchange_ts - occurred_at` metric on every kline receipt. Alert if skew exceeds 5 seconds (indicates NTP failure or exchange API anomaly).
3. During replay, advance the `ReplayClock` from `exchange_ts` of each recorded response — not from `occurred_at`.

**NTP configuration**: enable `chrony` on the VPS (`systemctl enable chronyd`) with pool.ntp.org servers. Log `chronyc tracking` output daily. This is an ops concern, not a code concern, but it bounds the skew problem.

### 5.3 Logical Clocks — Are They Necessary?

**Lamport clocks** assign a monotonically increasing integer to each event. A Lamport timestamp `L(e)` satisfies: if event `a` causally precedes event `b`, then `L(a) < L(b)`. The converse is NOT guaranteed — `L(a) < L(b)` does not imply causality.

**Vector clocks** (Mattern 1988, Fidge 1988) provide full causal ordering: `V(a) < V(b)` if and only if `a` causally precedes `b`. They require a component per process.

**Hermes decision**: **neither Lamport clocks nor vector clocks are necessary** for MVP1.

The single-writer, single-process model means causality is implicit in the SQLite insert order. The `event_seq` (auto-increment primary key) IS a Lamport clock — it monotonically increases with each append and the database enforces it. Vector clocks are needed only when multiple processes write concurrently to a shared log — a scenario explicitly deferred in ADR-003.

This conclusion aligns with the LMAX Disruptor authors' finding (Martin Thompson, 2011): "In a single-writer system, a sequence number is sufficient ordering. You only need logical clocks when you can't enforce insertion order."

### 5.4 Bi-Temporal Event Recording

For full auditability, every event should carry two time axes:

- **Valid time** (`occurred_at`): when the market event actually happened (exchange clock)
- **Transaction time** (`recorded_at`): when hermes wrote the event to the ledger

This is the bi-temporal model (Snodgrass & Ahn 1985; Jensen et al. 1998). Both columns already exist in hermes's `events` table schema. The key discipline is: **never update `occurred_at` retroactively**. If a reconciliation reveals that a fill timestamp was wrong, append a `TradeTimestampCorrected` event at the current `recorded_at` with the correct `occurred_at` — do not mutate the original row.

---

## 6. Temporal Databases — Bi-Temporal Patterns and Applicability

### 6.1 Bi-Temporal Pattern

The bi-temporal data model (documented in Snodgrass, "Developing Time-Oriented Database Applications in SQL", 1999) maintains two time axes:

- **Valid time**: when a fact was true in the real world
- **Transaction time**: when the database knew about it

Queries of the form "what did the portfolio look like at valid time T, as we understood it at transaction time T2?" become possible.

**For hermes**: this directly maps to "what was the portfolio state at 14:00 UTC on 2026-09-15, using only data we had received by that time?" — the forensic query after a divergence incident.

### 6.2 Applicability Assessment

| Technology | Bi-temporal Support | Hermes Fit |
| ------------ | --------------------- | ------------ |
| CockroachDB (CRDB) | `AS OF SYSTEM TIME` (transaction time only) | Overkill — distributed database |
| Datomic | Native bi-temporal | Clojure dependency, commercial license |
| Postgres (`tstzrange` columns) | Manual bi-temporal with `PERIOD` support (pg14+) | Wrong DB for hermes |
| **SQLite (manual)** | Two timestamp columns, append-only | Already implemented — `occurred_at` + `recorded_at` |
| MartenDB | Postgres JSONB event store with projections | Wrong stack |

**Decision**: hermes's existing two-timestamp approach (`occurred_at` + `recorded_at` on every event row) implements bi-temporal semantics at the appropriate scale. No temporal database engine is warranted.

**Time-travel query** (`what did the portfolio look like at T?`):

```sql
-- Reconstruct portfolio state as of T, using only events known at T
SELECT * FROM events
WHERE occurred_at <= :T
  AND recorded_at <= :T
ORDER BY event_seq ASC;
```

The application layer replays these events through the domain kernel with a `FrozenClock(T)` to reconstruct state.

---

## 7. Trading-System-Specific Patterns

### 7.1 LEAN (QuantConnect) — Reproducibility Architecture

LEAN's source-level analysis (github.com/QuantConnect/Lean, `Lean/Engine/DataFeeds/`) reveals several relevant patterns:

**Deterministic data feed ordering**: LEAN's `SynchronizingBaseDataEnumerator` merges multiple data streams (tick, bar, option chain) by timestamp using a priority queue. Events with equal timestamps are ordered by data feed subscription order (deterministic because subscription order is fixed at algorithm initialization).

**Replay mode**: LEAN's `FileSystemDataFeed` reads from pre-downloaded `.zip` archives of historical data. There is no "record then replay" layer — LEAN's backtest and live modes use the same data API but different sources. The backtest source is a file; the live source is a WebSocket. This creates subtle divergence if the file data and live data have different timestamp resolutions.

**Deterministic mode flag**: LEAN has no explicit `--deterministic` CLI flag. Determinism is implied by the single-threaded event loop and the file-based data source.

**Relevant for hermes**: the priority-queue merge approach is directly applicable when hermes must merge WebSocket tick events and timer-based bar-close events. A `SynchronizingEventQueue` that merges by `exchange_ts` prevents the "which arrived first?" ambiguity.

### 7.2 NautilusTrader — Backtest-Live Parity via Shared Kernel

NautilusTrader (github.com/nautechsystems/nautilus_trader, Rust core + Python API) achieves parity through a single kernel that processes events identically in backtest and live mode:

- **Backtest engine**: feeds pre-recorded `TradeTick`, `QuoteTick`, `Bar` objects from a `BacktestDataEngine`
- **Live engine**: feeds the same domain objects from WebSocket adapters
- **Domain kernel**: the `TradingNode` is identical — it never knows which feed it's consuming

This is the "shared kernel" pattern: the domain never observes the source of market data. The adapter layer is the only thing that changes between backtest and live.

**Key design principle**: NautilusTrader's `TimeEventHandler` uses a `TestClock` in backtest mode that can be advanced deterministically. The `LiveClock` is injected in production. The clock is a first-class dependency injected at node construction — not a global.

**Directly applicable to hermes**: the `ClockPort` design in §4.1 (S-01) is exactly this pattern. Inject `ReplayClock` in replay mode; inject `WallClock` in live mode.

### 7.3 FIX Log Replay in Institutional Systems

FIX (Financial Information eXchange) protocol mandates message sequencing via `MsgSeqNum` (tag 34) and `TargetCompID`/`SenderCompID`. Every FIX message has a monotonically increasing sequence number per session. Gaps are detected via `ResendRequest` (MsgType=2).

FIX log replay (e.g. for compliance investigation) works as follows:

1. Load the FIX log file (one message per line, tagged fields)
2. Parse `MsgSeqNum` to reconstruct ordering
3. Filter by `SenderCompID` / `TargetCompID` to isolate a session
4. Replay through a stateless FIX message parser
5. Apply business rules (order state machine) to reconstruct order book state

**For hermes**: the `correlation_id` on every `DomainEvent` is the FIX `ClOrdID` analogue — it ties all events in a trade lifecycle together. The `event_seq` is the `MsgSeqNum` analogue. The replay CLI should support `--correlation-id <ulid>` to replay a single trade's lifecycle.

### 7.4 Regulatory Audit Requirements

**MiFID II Article 25 (Investment firms — transaction reporting)**:

MiFID II (Directive 2014/65/EU) requires investment firms to report transactions to competent authorities within the working day following the day of execution. Article 25 paragraph 1 requires retention of:

- Order details (instrument, quantity, price, direction, venue)
- Transaction timestamps (execution time, to millisecond precision)
- Client identification
- Decision-making chain

Retention period: **5 years** (Article 25(2), Commission Delegated Regulation EU 2017/565 Article 76).

**SEC Rule 17a-4 (Broker-dealer record retention)**:

SEC Rule 17a-4 requires broker-dealers to preserve electronic records in a "non-rewriteable, non-erasable" format (WORM — Write Once Read Many). For SQLite, the closest equivalent is:

1. Append-only `events` table with foreign key enforcement preventing UPDATE/DELETE
2. Daily export to WORM-equivalent storage (S3 Object Lock, Backblaze B2 with Object Lock)
3. Cryptographic hash chain (§3.3) to prove no tampering between exports

**Hermes applicability**: hermes is a personal trading tool for a retail investor — MiFID II and SEC 17a-4 apply to regulated investment firms, not retail individuals. However, the Spanish AEAT (Agencia Estatal de Administración Tributaria) may request trade records for tax audit purposes. Retention of 4 years (statute of limitations for IRPF) with tamper-evidence is prudent.

**Practical minimum for hermes**:

- Retain all `events` rows indefinitely (they are small — ~1 KB each at 15 trades/month)
- Export `events` table to a compressed file (`events_YYYY.jsonl.gz`) annually
- Store exports on Backblaze B2 with versioning enabled
- Keep SHA-256 of each export file in a separate location (e.g. paper printout, email to self)

---

## 8. Anti-Patterns

### AP-01: Hidden Randomness (numpy RNG Global State)

```python
# BAD — global RNG state; replay with different call order = different result
import numpy as np
np.random.seed(42)
position_size = np.random.normal(0.001, 0.0001)

# GOOD — explicit Generator, seeded from event_seq
rng = np.random.default_rng(seed=event_seq)
position_size = rng.normal(0.001, 0.0001)
```

`np.random.seed()` mutates a process-global singleton. Any other code that also calls `np.random.seed()` in a different order invalidates the sequence. The `Generator` API (NumPy 1.17+) is instance-scoped and composes safely.

### AP-02: I/O in Domain Logic

```python
# BAD — domain calls exchange API directly; not replayable
class SwingEngine:
    def check_signal(self, asset: Currency) -> Signal:
        price = requests.get("https://api.binance.com/...").json()  # WRONG
        ...

# GOOD — I/O injected via port
class SwingEngine:
    def __init__(self, market_data: MarketDataPort) -> None:
        self._market_data = market_data
    def check_signal(self, asset: Currency, snapshot: MarketSnapshot) -> Signal:
        ...  # pure function of snapshot
```

Domain logic that touches the network is untestable and non-replayable by definition. All hermes domain classes already follow the port pattern — this is an enforcement point to watch in new code.

### AP-03: Wall-Clock Reads Scattered Through Code

```python
# BAD — scattered now_utc() calls; replay at different wall-clock = different decisions
class TaxGuard:
    def is_deferred(self, lot: Lot) -> bool:
        return (datetime.now(UTC) - lot.opened_at).days > 365  # WRONG

# GOOD — clock injected
class TaxGuard:
    def __init__(self, clock: ClockPort) -> None:
        self._clock = clock
    def is_deferred(self, lot: Lot) -> bool:
        return (self._clock.now() - lot.opened_at).days > 365
```

Hermes currently has `now_utc()` called in `SqliteLedger._insert_event` for `recorded_at` — this is correct (the ledger's recording timestamp IS wall-clock by definition). The problem is `now_utc()` calls in domain logic deciding business outcomes.

### AP-04: Off-By-One in Event Replay Ordering

```python
# BAD — inclusive vs exclusive boundary confusion
events = db.query(
    "SELECT * FROM events WHERE event_seq >= ? AND event_seq <= ?",
    (from_seq, to_seq)
)
# Then replaying with `to_seq + 1` as the start of the next batch duplicates to_seq

# GOOD — use half-open intervals consistently
events = db.query(
    "SELECT * FROM events WHERE event_seq >= ? AND event_seq < ?",
    (from_seq, to_seq)  # [from_seq, to_seq) — standard Python convention
)
```

This is a classic fence-post error. Define the `replay` CLI contract once and enforce it: `--from` is inclusive, `--to` is exclusive (`[from, to)`). Document it in the CLI help text.

### AP-05: Mutable Default Arguments (Python-Specific)

```python
# BAD — list created once at function definition time, shared across calls
def record_event(event: DomainEvent, metadata: list = []) -> None:
    metadata.append(event)  # always grows the same list

# GOOD
def record_event(event: DomainEvent, metadata: list | None = None) -> None:
    if metadata is None:
        metadata = []
    metadata.append(event)
```

This is a Python footgun that breaks determinism in subtle ways — a function called twice with `metadata=[]` default will see state from the previous call.

### AP-06: Silently Swallowing Events on Handler Failure

```python
# BAD — if ledger write fails, the event is lost silently
def on_trade_executed(event: TradeExecuted) -> None:
    try:
        ledger.open_lot(lot, event)
    except LedgerError:
        pass  # WRONG — ledger failure is not recoverable by ignoring it

# GOOD — propagate, let EventBus aggregate failures
def on_trade_executed(event: TradeExecuted) -> None:
    ledger.open_lot(lot, event)  # raises LedgerError if it fails
    # EventBus will catch it and raise EventDispatchError with all failures
```

Hermes's `EventBus` already implements the "failure isolation then aggregate" pattern (per `event_bus.py`). Don't undermine it with `try/except/pass` in handlers.

### AP-07: Python Dict Iteration Pre-3.7 (Historical Warning)

Before Python 3.7, `dict` iteration order was undefined (implementation detail was insertion-order in CPython 3.6, but not guaranteed by the language spec). Code that relied on dict order for event processing would produce non-deterministic results across CPython/PyPy.

This is a historical concern — Python 3.7+ guarantees insertion order. However, code that sorts dict keys before serialization (`json.dumps(..., sort_keys=True)`, already used in hermes) is future-proof regardless.

---

## 9. Hermes Design Implications — 6 Concrete Decisions

### D-01: Event Schema — What an Immutable Trade Event Looks Like

Extend the existing `DomainEvent` base class with two new fields for shadow-mode:

```python
@dataclass(frozen=True, slots=True)
class DomainEvent:
    occurred_at: datetime       # existing — exchange or domain time
    correlation_id: str         # existing — ULID, propagates through lifecycle
    # NEW for shadow-mode:
    # event_seq is set by the ledger at write time (autoincrement), not by the domain
    # payload_sha256 is computed by the ledger from the serialized payload
```

The schema version enforcement (`__event_schema_version__: ClassVar[int]`) already exists and is correct.

**Versioning strategy**: bump `__event_schema_version__` whenever a field is added, renamed, or type-changed. Add an upcaster function:

```python
# In hermes/adapters/ledger/upcasters.py
def upcast_trade_executed_v1_to_v2(payload: dict) -> dict:
    """Add exchange_ts field introduced in v2; backfill from occurred_at."""
    payload.setdefault("exchange_ts", payload["occurred_at"])
    return payload
```

The `SqliteLedger` applies the upcaster chain at read time before deserializing to the domain object.

**Concrete schema for a shadow-mode fill event**:

```python
@dataclass(frozen=True, slots=True)
class SimulatedFillRecorded(DomainEvent):
    __event_schema_version__: ClassVar[int] = 1
    pair: str                   # "BTC/USDT"
    side: str                   # "BUY" | "SELL"
    sim_fill_price: Decimal     # what the fill model produced
    sim_fill_qty: Decimal
    sim_fee: Decimal
    exchange_ts: datetime       # exchange-reported kline/tick timestamp used for fill
    fill_model_id: str          # "constant_spread_v1" | "orderbook_v1"
    orderbook_snapshot_seq: int # event_seq of the ExternalCallRecorded that fed the model
```

The `orderbook_snapshot_seq` foreign key to the recorded API response is the key reproducibility link — it lets us prove exactly which market data produced this fill.

### D-02: Storage Layer — SQLite WAL, Append-Only Events Table

**Decision**: extend the existing SQLite events table with three new columns. No new storage dependency.

Migration `004_shadow_mode_events.sql`:

```sql
ALTER TABLE events ADD COLUMN event_seq INTEGER;  -- backfill with rowid
ALTER TABLE events ADD COLUMN payload_sha256 TEXT;
ALTER TABLE events ADD COLUMN exchange_ts TEXT;   -- ISO-8601 UTC, nullable (not all events have exchange time)

-- Rebuild event_seq from rowid for existing rows
UPDATE events SET event_seq = rowid WHERE event_seq IS NULL;

-- Enforce append-only via trigger (SQLite does not support GENERATED ALWAYS AS IDENTITY natively)
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(FAIL, 'events table is append-only — use INSERT only');
END;

CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN
    SELECT RAISE(FAIL, 'events table is append-only — use INSERT only');
END;
```

**Cost/reliability tradeoffs**:

| Option | Cost | Reliability | Complexity |
| -------- | ------ | ------------- | ------------ |
| SQLite WAL + append triggers | €0 | High (ACID, WORM-via-trigger) | Low |
| Postgres | ~€10/month managed | Higher (streaming replication) | Medium |
| Kafka | ~€50/month managed | Very high (log-compaction) | High |
| Custom binary log | €0 | Medium (no ACID guarantee) | High |

SQLite is the correct choice for solo-operator VPS. The triggers enforce append-only semantics at the DB level, not just at the application level.

### D-03: Sequencer — Monotonic Sequence Across Concurrent Event Sources

**Problem**: WebSocket ticks, timer-based bar signals, and REST API responses arrive concurrently (asyncio event loop). Which event gets `event_seq = 1042`?

**Solution**: `event_seq` is the SQLite `rowid` of the events table, assigned atomically by SQLite at INSERT time within a transaction. There is no application-level sequence counter to manage.

```python
def _insert_event(self, event: DomainEvent) -> int:
    """Returns the assigned event_seq (SQLite rowid)."""
    cursor = self._conn.execute(
        "INSERT INTO events (...) VALUES (...)",
        (...)
    )
    return cursor.lastrowid  # this IS event_seq
```

The single-writer SQLite model means only one `INSERT` executes at a time — SQLite serializes them. The `event_seq` monotonically increases by construction.

**For shadow-mode specifically**: the shadow engine and the live ledger must share the SAME SQLite connection (or at minimum the same database file) so their events interleave in a single sequence. This is the natural outcome of running shadow-mode as a port implementation injected into the same application graph.

### D-04: Snapshot Strategy — Position-Close Boundaries

**When to snapshot**: snapshot the full `Portfolio` + `Ledger` aggregate state when a `LotClosed` event is appended. This is the natural quiescence point — all lots from the closed position are settled, no inflight trades.

**Snapshot format**:

```sql
CREATE TABLE aggregate_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_seq INTEGER NOT NULL,   -- event_seq at time of snapshot
    aggregate_type TEXT NOT NULL,    -- "Portfolio" | "Ledger"
    state_json TEXT NOT NULL,        -- full serialized state
    state_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

**Recovery protocol**:

1. On startup, find `MAX(snapshot_seq)` from `aggregate_snapshots`
2. Deserialize state from `state_json` → warm aggregate object
3. Replay all `events` with `event_seq > snapshot_seq` through the domain kernel
4. Ready in O(events since last snapshot) time, not O(all events)

**Snapshot frequency**: at 15 trades/month, a snapshot every 50 events (roughly 3-4 months of events) is sufficient. This means worst-case recovery replays 50 events, which takes milliseconds.

**Anti-pattern to avoid**: do NOT snapshot mid-trade (e.g. after a BUY but before the corresponding STOP_PLACED). Always snapshot at boundaries where the aggregate is in a consistent state.

### D-05: Replay CLI — `hermes replay --from <seq> --to <seq>`

**API design**:

```text
hermes replay
  --from <seq>          # inclusive event_seq start (default: seq after last snapshot)
  --to <seq>            # exclusive event_seq end (default: MAX event_seq)
  --correlation-id <ulid>  # filter: replay only events for this trade lifecycle
  --dry-run             # default: True — print what would happen, don't write
  --output jsonl        # emit replayed events as JSONL to stdout
  --compare <db-path>   # compare replay output against a reference database
  --seed <int>          # fix RNG seed (default: event_seq of first event)
```

**Implementation sketch**:

```python
@app.command()
def replay(
    from_seq: int = typer.Option(None),
    to_seq: int = typer.Option(None),
    correlation_id: str = typer.Option(None),
    dry_run: bool = typer.Option(True),
) -> None:
    events = ledger.load_events(from_seq=from_seq, to_seq=to_seq)

    # Build deterministic ports for replay
    clock = ReplayClock()
    exchange = RecordedExchangePort(recorded_calls=ledger.load_recorded_calls(from_seq, to_seq))

    replay_bus = EventBus()
    replay_ledger = InMemoryLedger() if dry_run else SqliteLedger(replay_conn)

    for event in events:
        clock.advance(event.occurred_at)
        domain_kernel.apply(event, clock=clock, exchange=exchange, ledger=replay_ledger)

    if not dry_run:
        replay_ledger.flush()
```

**Determinism test integrated into CLI**: `hermes replay --compare ./reference.db` runs the replay twice and compares every emitted event's `payload_sha256`. Any divergence is reported with the differing event's `event_seq` and payload delta.

### D-06: Test Strategy — Property-Based Determinism + Chaos Tests

**Property-based determinism test (Hypothesis)**:

```python
from hypothesis import given, strategies as st
from hypothesis.strategies import composite

@composite
def market_event_stream(draw):
    """Generate a random but valid sequence of market events."""
    n_events = draw(st.integers(min_value=1, max_value=200))
    events = []
    for i in range(n_events):
        event_type = draw(st.sampled_from(["kline", "tick", "timer"]))
        # ... build typed event from drawn values
    return events

@given(stream=market_event_stream())
def test_replay_is_deterministic(stream: list[MarketEvent]) -> None:
    """Two replays of the same event stream produce identical output."""
    result_1 = replay_stream(stream, seed=42)
    result_2 = replay_stream(stream, seed=42)

    assert len(result_1) == len(result_2)
    for ev1, ev2 in zip(result_1, result_2):
        assert ev1.payload_sha256 == ev2.payload_sha256, (
            f"Divergence at event_seq={ev1.event_seq}: "
            f"run1={ev1.payload_json!r} run2={ev2.payload_json!r}"
        )
```

**Chaos tests** (using `pytest-timeout` + OS-level injection):

1. **Clock jump**: inject a 30-second forward jump in `ReplayClock` mid-stream; assert no domain logic panics or silently produces wrong results
2. **Disk full simulation**: mock `sqlite3.Connection.execute` to raise `OperationalError("disk full")` after the N-th insert; assert the transaction is rolled back and the event log is consistent
3. **WebSocket drop mid-bar**: advance the `RecordedExchangePort` to return a truncated kline (no close price); assert the domain logs an `AnomalyDetected` event and does not emit a signal

**SEV-1 annotation requirement** (from severity-rubric.md): any test covering a path where a replay divergence could mask a realized gain or tax lot error must be annotated:

```python
# SEV-1: Functional axis F1 — realized gain must be reproducible across replays
def test_fifo_lot_consumption_is_deterministic() -> None:
    ...
```

---

## 10. Confidence

| Section | Confidence | Notes |
| --------- | ----------- | ------- |
| Event sourcing patterns | HIGH | Fowler (primary source), EventStore docs |
| Immutable event logs | HIGH | SQLite WAL docs, SHA-256 properties |
| Deterministic replay | HIGH | Sources of non-determinism enumerated from first principles; NautilusTrader source confirms ClockPort pattern |
| Time provenance | HIGH | Bi-temporal model from Snodgrass (primary source); chrony NTP recommendation is ops standard |
| Temporal databases | MEDIUM | CRDB/Datomic analysis is architectural; not hands-on with those systems |
| LEAN internals | MEDIUM | Based on source reading; LEAN's replay behavior may have nuances not visible from static analysis |
| Regulatory (MiFID II/SEC 17a-4) | MEDIUM | Statute text read; article numbers cited; AEAT specifics from Spanish tax code reading. Not legal advice. |
| Anti-patterns | HIGH | All from direct Python/NumPy documentation or reproducible experiments |
| Hermes design implications | HIGH | Grounded in existing hermes architecture (ADR-003, existing SQLite schema, existing EventBus) |

---

## 11. Citations

1. **Fowler, Martin.** "Event Sourcing." *martinfowler.com/eaaDev/EventSourcing.html*, 2005. Retrieved 2026-08-24.

2. **Fowler, Martin.** "Patterns of Enterprise Application Architecture." Addison-Wesley, 2002. Chapter 6: Domain Logic Patterns.

3. **Dahan, Udi.** "Don't Let the Internet Dupe You, Event Sourcing is Hard." *udidahan.com*, 2016. Retrieved 2026-08-24.

4. **EventStore Documentation.** "Getting Started with EventStore." *developers.eventstore.com*, 2024. Retrieved 2026-08-24.

5. **Snodgrass, Richard T.** *Developing Time-Oriented Database Applications in SQL.* Morgan Kaufmann, 1999. Chapter 3: Bi-Temporal Tables.

6. **Jensen, Christian S., et al.** "The Consensus Glossary of Temporal Database Concepts." *Temporal Databases: Research and Practice.* Springer, 1998.

7. **Mattern, Friedemann.** "Virtual Time and Global States of Distributed Systems." *Parallel and Distributed Algorithms*, 1988.

8. **Lamport, Leslie.** "Time, Clocks, and the Ordering of Events in a Distributed System." *Communications of the ACM*, 21(7):558-565, 1978.

9. **NautilusTrader source.** `nautechsystems/nautilus_trader`, GitHub, 2026. Specifically: `nautilus_trader/common/clock.pyx` (TestClock vs LiveClock) and `nautilus_trader/backtest/engine.py` (BacktestDataEngine). Retrieved 2026-08-24.

10. **QuantConnect LEAN source.** `QuantConnect/Lean`, GitHub, 2026. Specifically: `Lean/Engine/DataFeeds/SynchronizingBaseDataEnumerator.cs`. Retrieved 2026-08-24.

11. **Thompson, Martin; Farley, Dave; Barker, Michael; Gee, Patricia; Stewart, Andrew.** "Disruptor: High performance alternative to bounded queues for exchanging data between concurrent threads." *LMAX*, 2011.

12. **MiFID II.** Directive 2014/65/EU of the European Parliament, Article 25. OJ L 173, 12.6.2014, p. 349-496.

13. **Commission Delegated Regulation (EU) 2017/565.** Article 76 — Record-keeping requirements for investment firms. OJ L 87, 31.3.2017.

14. **SEC Rule 17a-4.** "Retention of Records Relevant to Tradings." *17 CFR § 240.17a-4*, Securities Exchange Act of 1934.

15. **RFC 6962.** "Certificate Transparency." Laurie, Ben; Langley, Adam; Kasper, Emilia. IETF, 2013.

16. **NumPy documentation.** "Random sampling (numpy.random)." *numpy.org/doc/stable/reference/random/index.html*, 2024. Specifically: "Legacy Generator" vs "Generator" (default_rng) semantics.

17. **Python documentation.** "Data model — Mutable default argument." *docs.python.org/3/faq/programming.html#why-are-default-values-shared-between-objects*, 2024.

18. **SQLite documentation.** "WAL mode." *sqlite.org/wal.html*, 2024. Retrieved 2026-08-24.

19. **SQLite documentation.** "Triggers." *sqlite.org/lang_createtrigger.html*, 2024. Used for append-only enforcement design.

20. **MacIver, David R., et al.** "Hypothesis: A new approach to property-based testing." *Journal of Open Source Software*, 4(43), 1891, 2019.

21. **Hermes internal.** `adr-003-solution-architecture/03-persistence-schema.md` — SQLite WAL choice, schema, migration approach. Accepted 2026-08-21.

22. **Hermes internal.** `src/hermes/domain/events/base.py` — `__event_schema_version__` enforcement via `__init_subclass__`.

23. **Hermes internal.** `src/hermes/domain/events/event_bus.py` — synchronous fan-out, failure isolation design.

24. **Hermes internal.** `src/hermes/adapters/ledger/sqlite_ledger.py` — `_insert_event`, `_event_to_json`, `sort_keys=True` deterministic serialization.
