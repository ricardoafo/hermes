# SA-10 Python Performance + Testing Philosophy for Trading

**Date**: 2026-08-24
**Author**: SA-10 sub-agent
**Engram topic key**: `research/paper-trading-sa10-python-perf-testing`
**Status**: FINAL

---

## 1. Executive Summary

Hermes processes orderbook diffs at ~10 msg/sec per pair on Binance. That is **not a demanding throughput target** for Python 3.14. The GIL, Cython, Rust extensions, and Numba JIT are YAGNI at this scale. The real performance risk is not CPU — it is latency spikes from blocking calls buried in async code, and correctness failures from hidden race conditions in a future multi-pair setup. This report focuses on what actually matters for MVP1 and what must be designed correctly today to avoid expensive rewrites for MVP2.

Performance recommendations (MVP1):

- `asyncio` for WebSocket ingestion and HTTP REST calls. Single event loop, single thread. No threading, no multiprocessing for MVP1. This matches the existing synchronous `EventBus` and single-writer SQLite model documented in ADR-003.
- `asyncio.TaskGroup` (Python 3.11+, available in 3.14) for managing the WS + heartbeat + notifier task lifecycle with structured shutdown.
- Standard `json` with `sort_keys=True` is acceptable for MVP1 event log (already in `sqlite_ledger.py`). `orjson` is a one-line swap that gives ~5x speedup if profiling ever justifies it — add it when you have a benchmark proving the need.
- SQLite WAL mode with batch commits (flush every N events or T seconds) is correct for the append-heavy event log. No switch to Postgres until sustained write rate exceeds ~5,000 rows/sec or read latency under concurrent load becomes measurable — neither applies at 15 trades/month.
- Parquet via `pyarrow` for historical L2 orderbook diff storage when issue #203 lands. Column-oriented reads are 10-100x faster than SQLite row scans for time-range queries on large diff datasets. Zero schema migration cost when adding new columns.

Testing recommendations (MVP1):

- Hypothesis `@given` for 6 non-negotiable invariants: position conservation, balance monotonicity, event sequence monotonicity, portfolio value identity, FIFO lot ordering, and fee non-negativity.
- `hypothesis.stateful` (`RuleBasedStateMachine`) for order lifecycle state machine tests.
- Golden-replay fixtures stored in `tests/fixtures/replays/` as JSONL files, pinned with `syrupy` snapshots. Small (< 200 events) for CI; large replays stored in git-lfs or S3 for weekly runs.
- Chaos injection via `pytest` fixtures: clock skew (already have `freezegun`), WebSocket message reorder/drop, disk-full simulation via `tmpfs` or monkey-patched write. No external chaos tool needed at MVP1 scale.
- Mutation testing with `cosmic-ray` (already in `pyproject.toml`) targeting hot-path modules. Kill-rate target: ≥85% for fill/position/fee modules; ≥70% for infrastructure adapters. Mutation tests run weekly (nightly is too slow for cosmic-ray).
- CI pyramid: unit + property tests on every PR (< 60s target); chaos + replay regression nightly; calibration harness + mutation weekly.

**Confidence**: HIGH on asyncio patterns, SQLite tuning, Hypothesis invariants. MEDIUM on Parquet integration details and cosmic-ray kill-rate targets (depend on actual mutation corpus).

---

## 2. asyncio Patterns

### 2.1 Which concurrency model for which subsystem

| Subsystem | Recommended model | Reason |
| ----------- | ------------------- | -------- |
| WebSocket ingestion (Binance/Kraken diffs) | `asyncio` | I/O-bound; websockets library is async-native; no GIL contention |
| HTTP REST calls (klines, fees, healthchecks) | `asyncio` (already via `httpx`) | Already in place; `httpx.AsyncClient` is the right tool |
| Order routing (simulated exchange write path) | Synchronous, called from async task | Write path is CPU-light and must serialize; calling sync from async is correct |
| Calibration compute (statistics, regression) | `asyncio.to_thread()` for blocking scipy calls | Offload blocking CPU to thread pool without a second process; GIL released during numpy/scipy C extensions |
| Notification (Telegram, Slack) | `asyncio` | Already async-native in `python-telegram-bot` |
| Heartbeat / APScheduler jobs | `asyncio` scheduled task inside the main loop | APScheduler 3.x supports asyncio scheduler; fits the single-loop model |

**Key rule**: the domain core stays synchronous. Async lives only at the adapter edge. The `EventBus` docstring already mandates this ("sub-agents that need to publish from a threaded context must marshal back to the main task first"). This is correct and must not be weakened for paper-trading.

### 2.2 Python 3.11+ asyncio improvements (relevant for hermes 3.14)

Hermes pins `requires-python = ">=3.14,<3.15"`. All 3.11+ improvements are available:

- **`asyncio.TaskGroup`** (PEP 654, Python 3.11): structured concurrency. Tasks in a group are cancelled together if any raises. This is the correct pattern for the WS + heartbeat + health-check task trio. Use it instead of raw `asyncio.gather()`.
- **Exception groups** (`ExceptionGroup`, Python 3.11): `TaskGroup` raises `ExceptionGroup` on multi-task failure. Handle with `except*` syntax.
- **`asyncio.timeout()`** (Python 3.11): replaces `asyncio.wait_for()` boilerplate. Use for WS reconnect timeout.
- **`asyncio.Runner`** (Python 3.11): replaces `asyncio.run()` in entrypoints that need setup/teardown around the event loop. Useful for the CLI entrypoint (`hermes.entrypoints.cli`).
- **Performance**: CPython 3.11 reduced per-task overhead by ~25% (CPython changelog 3.11 release notes). 3.14 adds incremental GC and further startup improvements — no trading-specific changes in 3.12-3.14 asyncio.

### 2.3 `asyncio.TaskGroup` — recommended shutdown pattern

```python
# entrypoints/shadow_engine.py (future)
async def run_shadow_engine(config: HermesConfig) -> None:
    async with asyncio.TaskGroup() as tg:
        tg.create_task(ws_ingest_loop(config))  # receives diffs, feeds consolidator
        tg.create_task(heartbeat_loop(config))  # healthchecks.io pings
        tg.create_task(notifier_flush_loop(config))  # Telegram/Slack drain
    # TaskGroup exits when all tasks complete OR when first task raises.
    # On KeyboardInterrupt: the loop is cancelled, TaskGroup propagates
    # cancellation to all child tasks and waits for them to finish.
```

**Graceful shutdown**: install a `signal.SIGTERM` handler that calls `task_group_scope.cancel()`. The `async with TaskGroup()` block guarantees all child tasks receive `CancelledError` and can do cleanup (flush pending events, close WS connections).

Source: PEP 654 (Python docs, <https://peps.python.org/pep-0654/>), Python 3.11 What's New (<https://docs.python.org/3/whatsnew/3.11.html>).

### 2.4 Common pitfalls

**Blocking calls in async context** — the silent killer. Any synchronous call that blocks the event loop for > 1ms stalls all other tasks. Known risks in hermes:

| Blocking call | Location | Fix |
| --------------- | ---------- | ----- |
| `sqlite3` writes (currently sync) | `SqliteLedger._insert_event` | Accept for MVP1 — SQLite WAL writes are sub-millisecond for small rows. If latency spike becomes measurable, use `asyncio.to_thread(conn.execute, ...)`. |
| `json.dumps` | `_event_to_json` | Non-issue at 10 msg/sec. `orjson` if profiling shows it matters. |
| `time.sleep()` | Rate limiters | Already uses `asyncio.sleep()` in `rate_limiter.py`. |
| `pydantic` model parsing | Config loader | Config loads once at startup. Not on the hot path. |
| `crypto` signature computation | Future signed requests | Offload to `asyncio.to_thread()` if HMAC-SHA256 is called per-request. |

**Event loop starvation**: a tight `while True` loop without an `await` blocks forever. Always `await asyncio.sleep(0)` at the top of every polling loop to yield to the scheduler.

**Source**: Python asyncio docs, "Developing with asyncio" section (<https://docs.python.org/3/library/asyncio-dev.html>).

---

## 3. GIL + Optimization

### 3.1 Where the GIL actually bites

The GIL is released during:

- C extension calls that explicitly release it (numpy, scipy, most I/O)
- `time.sleep()` and `asyncio.sleep()`
- All `socket` I/O operations (including `websockets`)

The GIL is held during:

- Pure Python bytecode execution
- CPython's own reference counting

**For hermes at 10 msg/sec**: the GIL is not a bottleneck. A single 64-byte orderbook diff parsed and dispatched takes < 1 microsecond of GIL-holding time. At 10/sec, that is 10 microseconds/second GIL pressure — negligible.

The GIL becomes relevant only if hermes scales to > 1,000 msg/sec across multiple pairs simultaneously, which is a different architectural tier entirely.

### 3.2 Free-threaded Python 3.13+ (PEP 703)

PEP 703 landed as an opt-in experimental build in Python 3.13 (October 2024). Status as of 3.14:

- The `python3.14t` free-threaded build exists and is installable.
- Extension ecosystem compatibility is partial: numpy, pandas, and most scientific libraries have published free-threaded wheels as of mid-2026, but not all. `websockets`, `httpx`, and `pydantic` are compatible.
- Performance: removing the GIL has a ~5-10% single-threaded overhead cost due to per-object reference count atomics (PEP 703 §Performance).
- **Hermes recommendation**: do NOT adopt the free-threaded build for MVP1 or MVP2. The single-loop asyncio model does not need it, and the ecosystem risk + single-threaded slowdown is not justified. Reassess when the calibration harness needs true CPU parallelism across many pairs.

Source: PEP 703 (<https://peps.python.org/pep-0703/>), CPython 3.14 release notes.

### 3.3 Sub-interpreters (PEP 684, PEP 734)

PEP 684 (3.12) gave each sub-interpreter its own GIL. PEP 734 (3.13) added `interpreters` stdlib module for inter-interpreter communication via shared memory channels.

**Hermes applicability**: none for MVP1 or MVP2. Sub-interpreters have no shared object model — passing domain objects across interpreter boundaries requires serialization. The operational complexity exceeds any benefit at 10 msg/sec. This is relevant only for systems doing true parallel orderbook matching across hundreds of instruments.

### 3.4 Hot path optimization — when to reach for Cython/Rust/Numba

**Decision tree** (YAGNI-first):

```text
Profile shows > 5% CPU in Python bytecode on the hot path?
    NO → Stop. Pure Python is fine.
    YES → Which bottleneck?
        Numeric array operations → try numpy vectorization first
            Still slow? → Numba @njit (no compile step in deployment)
        Custom loop logic (fill matching, lot selection) → Cython
            Need C library integration? → CFFI or ctypes
            Team has Rust? → PyO3 / maturin
```

MVP1 recommendation: pure Python, zero extensions.

Rationale: The fill path at 10 msg/sec processes at most 10 `OrderbookLevel` tuples per message and one lot selection traversal per fill (at most ~5 open lots for swing trading). Python can execute ~10M simple operations/second. The math is straightforward: even with 1,000 operations per message × 10 msg/sec = 10,000 ops/sec — 1,000x below Python's ceiling.

Profile before extending. When to consider:

- **Numba**: if the calibration harness runs Monte Carlo simulations with millions of paths and numpy vectorization is maxed out. VectorBT uses Numba for exactly this reason. Pro: no compiled artifacts. Con: first-call compilation penalty (seconds); not suitable for production hot path.
- **Cython**: if fill matching genuinely shows up in profiles after realistic load testing at 100+ msg/sec per pair. SA-2 noted NautilusTrader migrated entirely to Rust because their Cython build became unmaintainable.
- **PyO3 / maturin**: MVP3+ territory. SA-2 explicitly recommends staying in Python. Only pursue if the calibration harness becomes the system bottleneck and Cython maintenance is a team liability.
- **CFFI / ctypes**: only when integrating with an existing C library (e.g., a third-party orderbook matching engine). Not applicable for hermes's own logic.

**Sources**: VectorBT `portfolio/nb.py` (Numba pattern); NautilusTrader migration history (SA-2); Python benchmark results from <https://pyperf.readthedocs.io>.

---

## 4. Hot Path Patterns

### 4.1 Fill matching

The critical data structure is `Orderbook` — already correctly defined as a `frozen=True, slots=True` dataclass with `tuple[OrderbookLevel, ...]`. `__slots__` eliminates the `__dict__` overhead and reduces memory per instance by ~30% (Python docs, `__slots__`).

**Fill walk pattern** (pure Python, correct):

```python
def walk_book(
    levels: tuple[OrderbookLevel, ...],
    order_qty: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return (filled_qty, vwap_price). O(depth) — typically 5-20 levels."""
    remaining = order_qty
    total_cost = Decimal(0)
    filled = Decimal(0)
    for level in levels:  # tuple iteration is ~15% faster than list (CPython)
        fill_at = min(remaining, level.quantity)
        total_cost += fill_at * level.price
        filled += fill_at
        remaining -= fill_at
        if remaining <= Decimal(0):
            break
    if filled == Decimal(0):
        return Decimal(0), Decimal(0)
    return filled, total_cost / filled
```

**Do not use `Decimal` for hot-path intermediate arithmetic if performance is needed** — `Decimal` operations are 10-50x slower than `float` (Python docs). The hermes code-conventions mandate Decimal everywhere for correctness, which is right for financial values stored and compared. But the fill walk intermediates (`total_cost`, `remaining`) could use `float` internally and convert to `Decimal` only on output if profiling shows a real problem. Flag for later; use Decimal for now.

### 4.2 PnL computation

FIFO lot selection in `domain/lots/selection.py` is O(N) over open lots. At swing-trading scale (N ≤ 20 open lots per asset), this is irrelevant. No optimization needed.

For realized gain: `(sell_price - cost_basis_per_unit) * qty` — three `Decimal` multiplications. Sub-microsecond. No optimization needed.

### 4.3 Event log serialization hot path

Current: `json.dumps(dataclasses.asdict(event), default=_json_default, sort_keys=True)`.

This is correct and fast enough for 10 msg/sec. `dataclasses.asdict` does a deep recursive dict construction — for small events (5-10 fields) it takes ~5-10 microseconds. Acceptable.

If orderbook diff events are logged at 10/sec × 20+ fields, total serialization load is ~1-2ms/sec. Still well within budget.

`orjson` alternative: `orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)`. Native Rust implementation, ~5x faster than stdlib `json`. One-line swap when needed. Note: `orjson` does not call `__json_default__` — needs explicit serializer for `Decimal` and `datetime`. Test before adopting.

---

## 5. Serialization

### 5.1 Format comparison for the event log

| Format | Pros | Cons | Hermes verdict |
| -------- | ------ | ------ | ---------------- |
| `json` + `sort_keys=True` | Already in use; human-readable; `sort_keys` gives deterministic SHA-256 (SA-8 requirement) | Slower than binary; large for floats | **Use for MVP1** — correct and sufficient |
| `orjson` | 5x faster; `OPT_SORT_KEYS` available; handles datetime natively | No `__json_default__`; must handle Decimal explicitly | **Swap-in if profiling shows need** |
| `msgpack` | Binary, compact, ~2x faster than json | Not human-readable; `sort_keys` requires manual canonicalization; no stdlib | Defer — SA-8 requires deterministic SHA-256; msgpack canonicalization is non-trivial |
| `protobuf` | Typed, compact, schema evolution built-in | Requires .proto files; no sort_keys guarantee without field numbering discipline; adds build step | Overkill for solo-operator at current scale |
| `capnproto` | Zero-copy reads; true schema evolution | Complex setup; Python bindings (`pycapnp`) are not well maintained | Not recommended |
| `pickle` | Python-native | Not deterministic across Python versions; security risk on load; cannot be audited as text | Never for event log |

**Decision**: `json` + `sort_keys=True` for MVP1. `orjson` as a drop-in upgrade if benchmarks show > 1% CPU in serialization. Never `pickle` for the audit trail.

### 5.2 Deterministic serialization (SA-8 requirement)

`sort_keys=True` is already in `_event_to_json`. Two additional requirements:

1. **`Decimal` serialization**: always `str(decimal_value)` — never `float(decimal_value)`. Already correct in `_json_default`. `str(Decimal("1.23456789012345678"))` is deterministic across Python versions; `float` conversion introduces platform-dependent rounding.
2. **`datetime` serialization**: always `dt.isoformat()` with UTC offset — already in `_iso_utc`. Never `str(dt)` which varies by Python version.

### 5.3 Schema evolution

The `__event_schema_version__: ClassVar[int]` enforcement on `DomainEvent` is the correct hook. When a field is added/changed:

1. Bump `__event_schema_version__` on the event class.
2. Write a pure function `upcast_v1_to_v2(payload: dict) -> dict` that transforms old payloads.
3. At read time, chain: `read_raw_payload → upcast_v1_to_v2 → upcast_v2_to_v3 → instantiate`.
4. Test the upcaster chain with a `@given` strategy that generates v1 payloads and asserts they produce valid v2+ instances.

**Source**: Martin Fowler, "Event Sourcing" (martinfowler.com/eaaDev/EventSourcing.html); Greg Young, "Simple CQRS" (github.com/gregoryyoung/m-r).

---

## 6. Storage Performance

### 6.1 SQLite WAL mode tuning for the event log

SQLite WAL (Write-Ahead Log) is already implied by ADR-003. Recommended connection pragmas:

```python
# In SqliteLedger.__init__ or a connection factory:
conn.execute("PRAGMA journal_mode=WAL")  # Concurrent reads during write
conn.execute("PRAGMA synchronous=NORMAL")  # fsync on WAL checkpoint only (not every write)
# safe for non-power-failure workloads; use FULL if paranoid
conn.execute(
    "PRAGMA wal_autocheckpoint=1000"
)  # Checkpoint every 1000 pages (default); tune up for write-heavy
conn.execute("PRAGMA cache_size=-65536")  # 64MB page cache (negative = KB)
conn.execute("PRAGMA temp_store=MEMORY")  # Temp tables in RAM
conn.execute("PRAGMA mmap_size=268435456")  # 256MB memory-mapped I/O for reads
```

**Batch commit pattern**: do not commit after every event insert if inserting at high rate. Batch-commit every 100 events or every 500ms, whichever comes first. The existing `SqliteLedger` already groups lot + event into one transaction per trade — this is correct. For the shadow-mode event stream (orderbook diffs), batch them.

```python
# Shadow-mode batch writer pattern:
async def batch_event_writer(
    queue: asyncio.Queue[DomainEvent],
    ledger: SqliteLedger,
    batch_size: int = 100,
    flush_interval_sec: float = 0.5,
) -> None:
    batch: list[DomainEvent] = []
    last_flush = asyncio.get_event_loop().time()
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=flush_interval_sec)
            batch.append(event)
            if len(batch) >= batch_size:
                ledger.flush_batch(batch)
                batch.clear()
                last_flush = asyncio.get_event_loop().time()
        except asyncio.TimeoutError:
            if batch:
                ledger.flush_batch(batch)
                batch.clear()
                last_flush = asyncio.get_event_loop().time()
```

**Source**: SQLite WAL documentation (<https://www.sqlite.org/wal.html>); SQLite pragma reference (<https://www.sqlite.org/pragma.html>); Ben Johnson, "SQLite WAL Mode" (<https://www.sqlite.org/wal.html>).

### 6.2 When to switch from SQLite to Postgres

At hermes's current scale (15 trades/month, ~10 orderbook diff events/sec per pair), SQLite will never be the bottleneck. Concrete thresholds for switching:

| Condition | Postgres needed? |
| ----------- | ----------------- |
| Sustained write rate > 5,000 rows/sec | Yes — SQLite WAL saturates around 10,000 simple inserts/sec; margin buffer at 5,000 |
| Concurrent writers from multiple processes | Yes — SQLite allows only one writer at a time |
| Read latency under load > 10ms consistently | Profile first; add indexes before switching |
| Total DB size > 100GB | Maybe — SQLite handles multi-GB fine; test with your data pattern |
| Multi-machine deployment | Yes |

**For hermes MVP1 and MVP2**: SQLite is correct. No switch.

**Source**: SQLite "When to Use SQLite" (<https://www.sqlite.org/whentouse.html>); Ben Johnson benchmark (<https://github.com/benbjohnson/litestream> — latency numbers in docs).

### 6.3 Parquet for historical L2 orderbook diff storage

When issue #203 (orderbook diff storage) lands, use `pyarrow` + Parquet instead of SQLite for the L2 diff corpus. Rationale:

- **Column-oriented reads**: a time-range query on 10M rows reads only the `timestamp` and `levels` columns, not all columns. 10-100x faster than SQLite row scans for analytical queries.
- **Compression**: Parquet with Snappy or Zstd compresses repetitive L2 data (same price levels repeated across diffs) 5-10x better than SQLite page storage.
- **Schema evolution**: Parquet allows adding nullable columns without migrating existing files. Read old files with new schema — missing columns return null. Map to Python `None` or a sentinel `Decimal("0")`.
- **Interoperability**: `pandas.read_parquet()`, `polars.read_parquet()`, `duckdb.read_parquet()` all work natively. Calibration harness can read the same diff corpus as backtest replay.

**Write pattern**:

```python
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path


# One Parquet file per day per pair: diffs/BTCEUR/2026-08-24.parquet
def write_diff_batch(
    pair: str,
    diffs: list[dict],  # [{ts, bids: [[px, qty],...], asks: [[px, qty],...]}]
    output_dir: Path,
    date_str: str,
) -> None:
    schema = pa.schema(
        [
            ("ts_ns", pa.int64()),
            ("bids_px", pa.list_(pa.decimal128(18, 8))),
            ("bids_qty", pa.list_(pa.decimal128(18, 8))),
            ("asks_px", pa.list_(pa.decimal128(18, 8))),
            ("asks_qty", pa.list_(pa.decimal128(18, 8))),
        ]
    )
    table = pa.Table.from_pylist(diffs, schema=schema)
    out_path = output_dir / pair / f"{date_str}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path, compression="zstd")
```

**Read pattern** (backtest replay):

```python
import pyarrow.dataset as ds

dataset = ds.dataset("diffs/BTCEUR/", format="parquet")
scanner = dataset.scanner(
    filter=(ds.field("ts_ns") >= start_ns) & (ds.field("ts_ns") <= end_ns),
    columns=["ts_ns", "bids_px", "bids_qty", "asks_px", "asks_qty"],
)
for batch in scanner.to_batches():
    # process Arrow RecordBatch
```

**Source**: Apache Arrow Python docs (<https://arrow.apache.org/docs/python/>); Parquet format spec (<https://parquet.apache.org/docs/>); pyarrow Parquet guide (<https://arrow.apache.org/docs/python/parquet.html>).

---

## 7. Concurrency Correctness

### 7.1 Where race conditions hide in trading systems

Race conditions in Python asyncio code are subtler than in threaded systems because only one coroutine runs at a time — but any `await` is a potential preemption point.

Known race patterns in trading systems:

1. **Balance read-modify-write between awaits**: read balance → `await` something → write balance. Another task reads the old balance in between.

   ```python
   # DANGEROUS — two tasks can both read balance=100, both write balance=90
   balance = await get_balance()  # preemption possible here
   balance -= fill_cost
   await set_balance(balance)
   ```

   **Fix**: single-writer pattern. Only the fill processor task modifies balances. All reads go through a `BalancePort` backed by the SQLite single-writer.

2. **Duplicate order IDs from concurrent order generation**: two tasks generate `order_id = ulid.new()` within the same millisecond. ULID uses monotonic time — safe, but verify.

3. **Event sequence gaps from concurrent inserts**: two tasks insert events concurrently without a lock. SQLite serializes writes (WAL allows one writer), but the Python layer must not assume sequence = insert order when batching.

4. **Stale orderbook reference during fill**: the WS ingest task updates `current_orderbook` while the fill task reads it. Even without threads, this is a bug if the fill task `await`s between reading `current_orderbook` and using it.

   ```python
   # DANGEROUS
   book = self._current_orderbook  # snapshot at T
   await asyncio.sleep(0)  # book can change here
   fill_result = self._fill_model.process(book, order)  # uses stale book
   ```

   **Fix**: take a snapshot as an immutable `Orderbook` value object (already frozen in hermes's domain model) and pass it by value. Immutable data eliminates this class entirely.

### 7.2 Single-writer pattern (SA-8 endorsed)

The SQLite single-writer model in ADR-003 is the correct architecture for the fill path:

- One asyncio task owns the fill state machine: receives fill events from a queue, applies them to positions, writes to SQLite, publishes domain events.
- All other tasks are readers or producers that PUT events into the queue and do not touch position state.
- The `EventBus` is synchronous and in-process — this is correct. It runs entirely within the single writer's turn, with no preemption.

```text
WS ingest task ─→ asyncio.Queue[MarketEvent] ─→ FillProcessorTask (single writer)
                                                    ↓
                                            SqliteLedger (single connection)
                                                    ↓
                                            EventBus.publish() (sync fan-out)
                                                    ↓
                         ┌──────────────────────────┤
                    NotifierPort               AuditCollector
```

### 7.3 CSP-style channels vs. shared state

Python 3.14 has no stdlib CSP channels. `asyncio.Queue` is the idiomatic equivalent. Key properties:

- `asyncio.Queue` is NOT thread-safe (Python docs). Only use from a single event loop.
- `asyncio.Queue.put_nowait()` raises `QueueFull` if `maxsize` is set and full — use this as backpressure signal.
- For bounded queues (backpressure): `asyncio.Queue(maxsize=1000)`.

**Do not use `threading.Queue` or `multiprocessing.Queue`** for inter-coroutine communication — they block the event loop on `get()`.

### 7.4 Backpressure patterns for slow consumers

The scenario: WS ingest is fast (10 msg/sec); the fill processor or SQLite write is occasionally slow (e.g., during a snapshot). Without backpressure, the queue grows unboundedly.

**Pattern**: bounded queue + discard-or-pause on overflow.

```python
INGEST_QUEUE: asyncio.Queue[MarketEvent] = asyncio.Queue(maxsize=10_000)


async def ws_ingest_loop(ws_url: str) -> None:
    async with websockets.connect(ws_url) as ws:
        async for message in ws:
            event = parse_market_event(message)
            if INGEST_QUEUE.full():
                # Option A: discard (acceptable for non-critical events like mid-book diffs)
                structlog.get_logger().warning("ingest_queue_full_dropping_event")
                continue
                # Option B: apply_back_pressure by pausing WS reads (not reading = TCP flow control)
                # await INGEST_QUEUE.put(event)  # blocks until space — stalls WS reader
            else:
                INGEST_QUEUE.put_nowait(event)
```

For FILL events (cannot drop): use `await queue.put(event)` — this naturally applies backpressure by stalling the ingest task until the fill processor drains the queue. This is safe in asyncio because the stall releases the event loop to other tasks.

**Source**: Python asyncio Queue docs (<https://docs.python.org/3/library/asyncio-queue.html>); Nathaniel J. Smith, "Notes on structured concurrency" (<https://vorpus.org/blog/notes-on-structured-concurrency/>).

---

## 8. Property-Based Invariants

### 8.1 The complete invariant catalogue for hermes

These invariants must hold for ALL inputs, ALL orderings, ALL edge cases. Each should have a dedicated Hypothesis `@given` test annotated `# SEV-1: <rubric row>` where applicable.

Category 1: Position conservation

| ID | Invariant | Rubric |
| ---- | ----------- | -------- |
| INV-01 | `sum(lot.remaining_qty for lot in open_lots) == portfolio.position_qty` for every asset | SEV-1: F2 analogue — position integrity |
| INV-02 | After any sequence of `open_lot` + `consume_lot` operations, total consumed qty ≤ total opened qty per asset | SEV-1: position conservation |
| INV-03 | `lot.remaining_qty ≤ lot.original_qty` always; `remaining_qty ≥ 0` always | SEV-1: no phantom inventory |

Category 2: Balance monotonicity

| ID | Invariant | Rubric |
| ---- | ----------- | -------- |
| INV-04 | Cash balance never goes negative (after any sequence of fills and fees) | SEV-1: financial integrity |
| INV-05 | `new_balance = old_balance - fill_cost - commission`: algebraically exact (Decimal, no float drift) | SEV-1: S6 analogue — precision |
| INV-06 | Fee is always ≥ 0; commission rate × notional = exact fee (no rounding in wrong direction) | SEV-2: F1 analogue |

Category 3: Event sequence monotonicity

| ID | Invariant | Rubric |
| ---- | ----------- | -------- |
| INV-07 | `event_seq` is strictly monotonically increasing (no duplicates, no gaps except on crash recovery) | SEV-1: audit integrity |
| INV-08 | `event.occurred_at` is non-decreasing within a single session (per SA-8: clock monotonicity via `ClockPort`) | SEV-2: replay determinism |
| INV-09 | `payload_sha256` of the same event, serialized twice, is identical (deterministic serialization) | SEV-1: SA-8 requirement |

Category 4: Portfolio value identity

| ID     | Invariant                                                                                               | Rubric                     |
|--------|---------------------------------------------------------------------------------------------------------|----------------------------|
| INV-10 | `portfolio_value = cash_balance + sum(position_qty * mark_price for each asset)` holds after every fill | SEV-1: accounting identity |
| INV-11 | After all lots consumed for an asset: `position_qty == 0` and no open lots remain for that asset        | SEV-1: lot lifecycle       |

Category 5: FIFO lot ordering

| ID     | Invariant                                                                                                     | Rubric                            |
|--------|---------------------------------------------------------------------------------------------------------------|-----------------------------------|
| INV-12 | `select_lots_fifo` always returns lots in `opened_at ASC, lot_id ASC` order                                   | SEV-1: FIFO tax compliance — AEAT |
| INV-13 | Oldest lot is always consumed first; a younger lot is never touched while an older lot has remaining quantity | SEV-1: FIFO invariant             |

Category 6: Fill correctness

| ID | Invariant | Rubric |
| ---- | ----------- | -------- |
| INV-14 | `fill.filled_qty ≤ order.qty` always | SEV-1: overfill protection |
| INV-15 | `fill.fill_price > 0` always when `fill.filled_qty > 0` | SEV-1: nonsensical fill price |
| INV-16 | For a BUY market order: `fill.fill_price ≥ best_ask` (no fill below ask) | SEV-2: fill price realism |
| INV-17 | For a SELL market order: `fill.fill_price ≤ best_bid` (no fill above bid) | SEV-2: fill price realism |

### 8.2 Hypothesis strategies for realistic orderbook + trade events

```python
# tests/strategies/orderbook.py
from hypothesis import strategies as st
from decimal import Decimal
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.value_objects.pair import Pair


@st.composite
def orderbook_levels(draw, n_levels: int = 10) -> tuple[OrderbookLevel, ...]:
    """Generate a sorted tuple of OrderbookLevels."""
    base_price = draw(st.decimals(min_value="0.01", max_value="100000.00", places=8))
    prices = sorted(
        {base_price + Decimal(str(draw(st.floats(0, 100)))) for _ in range(n_levels)},
        reverse=True,  # bids descending
    )
    return tuple(
        OrderbookLevel(price=p, quantity=Decimal(str(draw(st.floats(0.001, 10.0))))) for p in prices
    )


@st.composite
def valid_orderbook(draw, pair: Pair | None = None) -> Orderbook:
    """Generate a valid crossed-free Orderbook."""
    if pair is None:
        pair = draw(st.from_type(Pair))
    n = draw(st.integers(1, 20))
    # Generate bids around a mid price, asks above it
    mid = draw(st.decimals(min_value="100", max_value="50000", places=2))
    spread = draw(st.decimals(min_value="0.01", max_value="10", places=2))
    bid_levels = tuple(
        OrderbookLevel(
            price=mid - spread * Decimal(str(i + 1)),
            quantity=Decimal(str(draw(st.floats(0.001, 5.0)))),
        )
        for i in range(n)
    )
    ask_levels = tuple(
        OrderbookLevel(
            price=mid + spread * Decimal(str(i + 1)),
            quantity=Decimal(str(draw(st.floats(0.001, 5.0)))),
        )
        for i in range(n)
    )
    from datetime import datetime, UTC

    return Orderbook(
        pair=pair,
        bids=bid_levels,
        asks=ask_levels,
        snapshot_time=datetime.now(UTC),
    )
```

### 8.3 `hypothesis.stateful` for order lifecycle

```python
# tests/unit/domain/test_fill_state_machine.py
from hypothesis.stateful import RuleBasedStateMachine, rule, initialize, invariant


class FillStateMachine(RuleBasedStateMachine):
    """
    Models the order → fill → position state machine.
    Verifies position conservation and balance non-negativity across
    arbitrary sequences of buys and sells.
    """

    @initialize()
    def setup(self) -> None:
        self.cash_balance = Decimal("10000")
        self.open_lots: list[Lot] = []
        self.position_qty = Decimal("0")

    @rule(
        qty=st.decimals(min_value="0.001", max_value="1.0", places=8),
        price=st.decimals(min_value="100", max_value="50000", places=2),
    )
    def buy(self, qty: Decimal, price: Decimal) -> None:
        cost = qty * price * Decimal("1.001")  # 0.1% taker fee
        if cost > self.cash_balance:
            return  # insufficient funds — skip
        self.cash_balance -= cost
        lot = open_lot(qty, price)
        self.open_lots.append(lot)
        self.position_qty += qty

    @rule(
        qty=st.decimals(min_value="0.001", max_value="1.0", places=8),
        price=st.decimals(min_value="100", max_value="50000", places=2),
    )
    def sell(self, qty: Decimal, price: Decimal) -> None:
        sellable = min(qty, self.position_qty)
        if sellable <= 0:
            return
        proceeds = sellable * price * Decimal("0.999")  # 0.1% taker fee
        self.cash_balance += proceeds
        self.position_qty -= sellable
        consume_lots_fifo(self.open_lots, sellable)

    @invariant()
    def balance_non_negative(self) -> None:
        assert self.cash_balance >= Decimal("0"), f"Negative balance: {self.cash_balance}"

    @invariant()
    def position_matches_lots(self) -> None:
        lot_qty = sum(l.remaining_qty.amount for l in self.open_lots)
        assert lot_qty == self.position_qty, (
            f"Lot qty {lot_qty} != position qty {self.position_qty}"
        )


TestFillStateMachine = FillStateMachine.TestCase
```

**Source**: Hypothesis stateful testing docs (<https://hypothesis.readthedocs.io/en/latest/stateful.html>); David MacIver, "Finding More Bugs with Less Code" (hypothesis.works/articles/).

---

## 9. Chaos Testing Patterns

### 9.1 Network jitter injection

WebSocket delay, reorder, and duplicate injection using a proxy coroutine:

```python
# tests/chaos/ws_chaos_proxy.py
import asyncio, random
from collections import deque


class ChaosWebSocketProxy:
    """Wraps a websockets connection and injects network chaos."""

    def __init__(
        self,
        ws,
        delay_range_ms: tuple[float, float] = (0, 50),
        drop_probability: float = 0.01,
        duplicate_probability: float = 0.005,
        reorder_window: int = 5,
    ):
        self._ws = ws
        self._delay_range = delay_range_ms
        self._drop_p = drop_probability
        self._dup_p = duplicate_probability
        self._reorder_window = reorder_window
        self._buffer: deque[tuple[float, bytes]] = deque()

    async def __aiter__(self):
        async for message in self._ws:
            # Drop
            if random.random() < self._drop_p:
                continue
            # Duplicate
            if random.random() < self._dup_p:
                yield message  # emit immediately
            # Delay + reorder
            delay = random.uniform(*self._delay_range) / 1000
            release_at = asyncio.get_event_loop().time() + delay
            self._buffer.append((release_at, message))
            while self._buffer and self._buffer[0][0] <= asyncio.get_event_loop().time():
                _, msg = self._buffer.popleft()
                yield msg
```

**What to test**:

- Out-of-order orderbook diffs — the ingest layer must handle sequence number gaps (Binance sends `U` / `u` sequence fields for exactly this).
- Duplicate messages — idempotency of event processing.
- 500ms delay spike — does the fill processor stall? Does backpressure trigger correctly?

### 9.2 Clock skew injection

Already have `freezegun` in dev dependencies. Use it to test `ClockPort` defense:

```python
# tests/chaos/test_clock_skew.py
from freezegun import freeze_time
from hypothesis import given, settings
from hypothesis import strategies as st


@given(skew_seconds=st.floats(min_value=-86400, max_value=86400))
def test_clock_skew_does_not_affect_event_ordering(skew_seconds: float) -> None:
    """event_seq must be strictly monotonic regardless of wall-clock jumps."""
    with freeze_time(auto_tick_seconds=skew_seconds):
        # Generate N events through the fill processor
        events = generate_fill_events(n=50)
        seqs = [e.event_seq for e in events]
        assert seqs == sorted(seqs), "event_seq not monotonic under clock skew"
        assert len(set(seqs)) == len(seqs), "duplicate event_seq under clock skew"
```

### 9.3 Disk-full during event log write

Use `tmp_path` fixture + monkey-patch to simulate `sqlite3.OperationalError: database or disk is full`:

```python
# tests/chaos/test_disk_full.py
def test_event_log_write_failure_rolls_back(monkeypatch, tmp_path) -> None:
    """If the event INSERT fails, the transaction must roll back — no partial state."""
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    ledger = SqliteLedger(conn)

    call_count = 0
    original_execute = conn.execute

    def failing_execute(sql, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if "INSERT INTO events" in sql and call_count > 2:
            raise sqlite3.OperationalError("database or disk is full")
        return original_execute(sql, *args, **kwargs)

    monkeypatch.setattr(conn, "execute", failing_execute)

    lot = make_test_lot()
    event = make_test_trade_executed_event()

    with pytest.raises(LedgerError):
        ledger.open_lot(lot, event)

    # Verify: lot table must also be empty (transaction rolled back)
    rows = conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
    assert rows == 0, "partial lot insertion survived failed event write"
```

### 9.4 Partial fill sequences that never complete

Property test: an order submitted but never filled (no matching book levels) must NOT advance position or modify cash balance.

```python
@given(orderbook=valid_orderbook_with_no_crossing_levels())
def test_unfilled_order_is_idempotent(orderbook: Orderbook) -> None:
    order = make_limit_order(price=Decimal("0.001"))  # price below any ask
    result = fill_model.process_fill(MarketDataContext(orderbook=orderbook, order=order))
    assert result.filled_qty == Decimal("0")
    assert result.commission == Decimal("0")
```

### 9.5 Duplicate exchange responses

Idempotency under duplicate messages — critical for WebSocket reconnect scenarios:

```python
def test_duplicate_fill_event_is_idempotent(ledger: SqliteLedger) -> None:
    """Inserting the same event twice must raise, not silently double-count."""
    event = make_fill_event(correlation_id="test-ulid-001")
    ledger.append_event(event)

    # Second insert with same correlation_id should raise or be safely ignored
    # depending on the idempotency contract. Verify position state is unchanged.
    position_before = get_position(ledger, "BTC")
    try:
        ledger.append_event(event)
    except LedgerError:
        pass  # Expected if the ledger enforces uniqueness

    position_after = get_position(ledger, "BTC")
    assert position_before == position_after, "Duplicate event mutated position"
```

---

## 10. Golden-Replay Fixtures

### 10.1 Design

A golden-replay fixture is a captured event sequence + expected output snapshot. The contract:

1. Feed the fixture through the domain engine.
2. Assert that the output events (domain events emitted, final positions, final balances) are bit-identical to the stored snapshot.
3. Any change to `FillModelPort`, `LotSelection`, or `FeeModel` that changes this output is a breaking change and MUST update the snapshot intentionally.

**Fixture format** (JSONL — one JSON object per line, deterministic per SA-8):

```jsonl
{"type": "MarketEvent", "ts_ns": 1724448000000000000, "pair": "BTCEUR", "bids": [[...]], "asks": [[...]]}
{"type": "OrderRequest", "ts_ns": 1724448001000000000, "side": "BUY", "qty": "0.01", "type": "MARKET"}
{"type": "FillResult", "ts_ns": 1724448001100000000, "filled_qty": "0.01", "fill_price": "43250.50", "commission": "0.43"}
```

### 10.2 Storage and versioning

**Small fixtures (< 200 events)**: store in `tests/fixtures/replays/` as `.jsonl` files committed to git. These run on every PR.

**Large fixtures (> 200 events, e.g., full 30-day shadow replay)**: store in git-lfs with a `.lfs` pointer file, or in an S3 bucket referenced by a manifest file. Download during nightly/weekly CI runs only. Never download in PR CI.

**Fixture versioning**: include a schema version header in each JSONL file:

```jsonl
{"fixture_schema_version": 1, "created_at": "2026-08-24", "description": "BTCEUR 1-hour shadow replay 2026-08-01"}
```

When `FillModelPort` changes its serialization format, bump `fixture_schema_version` and provide a migration script. Old fixtures must be re-generated or explicitly marked as `legacy` (excluded from replay tests, retained for archaeology).

### 10.3 Snapshot management with syrupy

`syrupy` (already in `pyproject.toml`) supports snapshot testing with human-readable diffs. Use for golden-replay output:

```python
# tests/integration/test_golden_replay.py
from syrupy.assertion import SnapshotAssertion


def test_btceur_1h_replay(snapshot: SnapshotAssertion) -> None:
    """Replay the BTCEUR 1-hour fixture and assert bit-identical output."""
    events = load_fixture("tests/fixtures/replays/btceur_1h.jsonl")
    engine = build_replay_engine()
    output = engine.run(events)
    assert output == snapshot  # syrupy compares and updates on --snapshot-update
```

Run `pytest --snapshot-update` intentionally when changing fill logic. The diff is human-readable and reviewable.

**Source**: syrupy docs (<https://github.com/syrupy-project/syrupy>); Martin Fowler, "Approval Testing" (martinfowler.com/bliki/ApprovalTest.html).

### 10.4 Regression prevention on FillModel changes

Add a mandatory CI check: if any file under `src/hermes/domain/` or `src/hermes/adapters/` is modified, run the full golden-replay suite. If any snapshot breaks, require `--snapshot-update` with a PR comment explaining why the fill behavior changed. This makes behavioral regressions visible in PR review.

---

## 11. Mutation Testing

### 11.1 Tool: cosmic-ray (already in pyproject.toml)

`cosmic-ray` is the correct choice for Python 3.14 (uses `parso` for AST mutation). It generates and runs one mutant per test run, which is expensive but precise.

### 11.2 Catchable vs. equivalent mutations

**Catchable** (high-value targets):

- Arithmetic operators on financial values: `+` → `-`, `*` → `/`, `//` → `//+1`
- Comparison operators in lot selection: `<` → `<=`, `>` → `>=` in FIFO ordering
- Boolean conditions in fill logic: `qty > 0` → `qty >= 0`, `is_maker` flipped
- Missing boundary checks: removal of `if remaining <= 0: break` in fill walk
- Off-by-one in sequence numbering

**Equivalent mutations** (unkillable — do not count against kill rate):

- Changing log message strings
- Altering docstring content
- Cosmetic reordering of dictionary keys in test fixtures
- Changing `__repr__` implementations

**Source**: Papadakis et al., "Mutation Testing Advances: An Analysis and Survey" (ScienceDirect, 2019); cosmic-ray docs (<https://cosmic-ray.readthedocs.io/>).

### 11.3 Kill-rate targets per module

| Module | Kill-rate target | Justification |
| -------- | ----------------- | --------------- |
| `domain/lots/selection.py` | ≥ 90% | FIFO ordering is AEAT-compliance-critical (SEV-1: F2 analogue) |
| `domain/lots/lot_consumption.py` | ≥ 90% | Consumption arithmetic — direct PnL impact |
| `domain/fees/fee_schedule.py` | ≥ 85% | Fee calculation — financial output (SA-5 target) |
| Future `domain/fill_model.py` | ≥ 85% | Core fill accuracy |
| `adapters/ledger/sqlite_ledger.py` | ≥ 80% | Transaction boundary correctness |
| `domain/events/event_bus.py` | ≥ 75% | Failure isolation logic |
| `infrastructure/config/` | ≥ 70% | Validation rules |
| `adapters/exchanges/` | ≥ 60% | I/O adapters — harder to kill (network mocking) |

**SA-5 context**: SA-5 set ≥85% for the calibration harness. Extend that target to fill/fee/lot modules which are equally critical.

### 11.4 Cost/benefit: mutation testing hot paths only

Full cosmic-ray on all source files is impractical for daily CI — generating and running 10,000 mutants takes hours. Recommended scope:

- **Weekly**: run mutation on `domain/lots/`, `domain/fees/`, future `domain/fill_model.py`. These are the highest-value modules.
- **On every PR touching these modules**: run a targeted subset using `cosmic-ray` `--filter` on changed files only. GitHub Actions `paths` trigger.
- **Never** run mutation on infrastructure glue code (config loaders, CLI wiring) on every PR — cost exceeds benefit.

---

## 12. Fuzz Testing

### 12.1 WebSocket message parser

The Binance diff message parser is the highest-value fuzz target: it receives uncontrolled bytes from the network. A malformed message must never crash the process, corrupt state, or bypass validation.

**Hypothesis fuzzing** (preferred over atheris for Python-native code):

```python
# tests/fuzz/test_ws_parser_fuzz.py
from hypothesis import given, settings
from hypothesis import strategies as st


@given(raw_message=st.binary(max_size=10_000))
@settings(max_examples=10_000, deadline=None)
def test_ws_parser_never_raises_on_arbitrary_bytes(raw_message: bytes) -> None:
    """The parser must handle any input without crashing."""
    try:
        result = parse_binance_diff_message(raw_message)
        # If parsing succeeds, result must be a valid MarketEvent or None
        if result is not None:
            assert isinstance(result, MarketEvent)
    except ValueError, json.JSONDecodeError, UnicodeDecodeError:
        pass  # Expected — parser can reject malformed input
    except Exception as e:
        pytest.fail(f"Parser raised unexpected exception on {raw_message!r}: {e}")
```

**atheris** (Google's coverage-guided fuzzer for Python) earns its keep when you need mutation-guided exploration of the input space with coverage feedback. Atheris requires a compiled C extension and a seed corpus. Use it for a weekly fuzz campaign, not in PR CI.

```python
# fuzz/fuzz_ws_parser.py — atheris entry point
import atheris, sys


def TestOneInput(data):
    with atheris.instrument_func(parse_binance_diff_message):
        try:
            parse_binance_diff_message(data)
        except ValueError, json.JSONDecodeError, UnicodeDecodeError:
            pass


atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
```

**Source**: atheris docs (<https://github.com/google/atheris>); Hypothesis fuzzing integration (<https://hypothesis.readthedocs.io/en/latest/details.html#use-with-external-fuzzers>).

### 12.2 Config loader fuzzing

The YAML config loader parses user-controlled input at startup. A malformed config must fail fast with a `ConfigError`, never cause undefined behavior.

```python
@given(yaml_content=st.text(min_size=0, max_size=5_000))
def test_config_loader_never_crashes_on_arbitrary_yaml(yaml_content: str) -> None:
    try:
        parse_config(yaml_content)
    except ConfigError, ValueError, pydantic.ValidationError:
        pass  # Expected rejection
    except Exception as e:
        pytest.fail(f"Config loader raised unexpected exception: {e}")
```

**Where fuzz testing earns its keep** in hermes:

1. WebSocket message parser (external bytes) — HIGH value
2. Config loader (user-controlled YAML/env vars) — MEDIUM value
3. Event deserializer / upcaster chain (reading old DB payloads) — MEDIUM value
4. Internal domain logic (already covered by property tests with domain-valid inputs) — LOW marginal value

---

## 13. CI Pyramid Economics

### 13.1 Three-tier model

```text
Weekly     ┌───────────────────────────────────────────────────────┐
           │ Calibration harness vs. recorded shadow data          │
           │ Full mutation run (cosmic-ray all hot-path modules)    │
           │ Large replay fixtures (git-lfs or S3)                  │
           │ atheris fuzz campaigns (24h CPU budget)                │
           └───────────────────────────────────────────────────────┘
Nightly    ┌───────────────────────────────────────────────────────┐
           │ Chaos test suite (clock skew, WS drop, disk full)     │
           │ Golden-replay regression (all small fixtures)          │
           │ Contract tests (Binance/Kraken public APIs)            │
           │ Property tests with large `max_examples` (10,000)     │
           │ Mutation on changed modules (paths trigger)            │
           └───────────────────────────────────────────────────────┘
Every PR   ┌───────────────────────────────────────────────────────┐
           │ Unit tests (pytest, fast, deterministic) — < 30s      │
           │ Property tests (hypothesis default 100 examples) — < 30s │
           │ Import-linter contracts                                │
           │ Type checking (mypy)                                   │
           │ Linting (ruff)                                         │
           │ Coverage report (≥ 90% line, ≥ 85% branch target)     │
           │ Snapshot tests (syrupy) on changed modules             │
           └───────────────────────────────────────────────────────┘
```

### 13.2 PR tier constraints (< 60s target)

- `pytest -x -q --timeout=60`: fail fast on first failure, 60s per test hard limit (already configured).
- `pytest -n auto` (pytest-xdist): parallel across CPU cores for unit tests. Do NOT parallelize tests that write to the same SQLite file.
- Exclude chaos tests, contract tests, large fixtures from PR: `pytest -m "not chaos and not contract and not slow"`.
- Hypothesis: `@settings(max_examples=100)` in PR CI; override to `max_examples=10_000` in nightly via `HYPOTHESIS_MAX_EXAMPLES` env var.

### 13.3 Nightly tier

- `pytest -m chaos` — all chaos injection tests.
- `pytest -m replay` — golden-replay suite with small fixtures.
- `pytest -m contract` — Binance/Kraken public API contract tests (need network access).
- `cosmic-ray run --filter "domain/lots domain/fees"` — targeted mutation on highest-value modules.
- Target: nightly run completes in < 30 minutes.

### 13.4 Weekly tier

- Full calibration harness: download shadow-mode recorded data, replay, compute calibration metrics (SA-5 protocol), fail if drift > threshold.
- Full mutation run: `cosmic-ray run src/hermes/domain/` — all domain modules. May take 2-4 hours; run in a dedicated GitHub Actions self-hosted runner.
- Fuzz campaigns: `atheris` on WS parser and config loader with 24h wall-clock budget and seed corpus from previous week's run.
- Replay large fixtures from git-lfs/S3.

### 13.5 CI tooling recommendations

- Use `pytest-timeout` (already installed) with `timeout_method = "thread"` for Windows compatibility (already in pyproject.toml).
- Use `pytest-xdist` (already installed) for parallel unit tests. Set `-n 4` or `-n auto`.
- Mark test tiers with `@pytest.mark.chaos`, `@pytest.mark.replay`, `@pytest.mark.contract`, `@pytest.mark.slow`.
- Store Hypothesis databases in `.hypothesis/` (gitignored) but share them across CI runs via cache (GitHub Actions `actions/cache` on `.hypothesis/`). Sharing the example database means nightly runs benefit from failures found in previous PR runs.

**Source**: Hypothesis CI docs (<https://hypothesis.readthedocs.io/en/latest/details.html#use-in-ci>); pytest-xdist docs (<https://pytest-xdist.readthedocs.io/>); cosmic-ray CI integration (<https://cosmic-ray.readthedocs.io/en/latest/workflow.html>).

---

## 14. Confidence Summary

| Topic | Confidence | Basis |
| ------- | ----------- | ------- |
| asyncio for WS ingest | H | Industry standard; Python docs; hermes existing httpx/anyio pattern |
| TaskGroup for shutdown | H | PEP 654; Python 3.14 availability confirmed |
| No Cython/Rust for MVP1 | H | YAGNI at 10 msg/sec; load math in §3.4 |
| GIL non-issue at current scale | H | GIL math in §3.1; benchmarks from pyperf |
| Free-threaded Python 3.13+ — defer | H | PEP 703 §Performance overhead; ecosystem gaps |
| orjson as optional swap | H | orjson benchmarks (<https://github.com/ijl/orjson#benchmark>) show 5x vs json |
| SQLite WAL pragmas | H | SQLite official documentation |
| SQLite → Postgres threshold | M | Threshold from SQLite docs + Ben Johnson benchmarks; hermes-specific verification needed |
| Parquet for L2 diffs | H | pyarrow docs; column-oriented read benchmarks |
| INV-01 through INV-17 invariants | H | Synthesized from SA-2 fill model analysis + accounting principles |
| Hypothesis stateful testing | H | hypothesis.readthedocs.io; David MacIver's published work |
| Chaos fixture patterns | M | Patterns derived from general resilience testing; hermes-specific validation needed |
| cosmic-ray kill-rate targets | M | SA-5 precedent (≥85%) + mutation literature; actual rates depend on test corpus |
| atheris for WS parser | M | Atheris is production-ready (Google internal use); Python 3.14 compatibility unverified |
| CI pyramid timing targets | M | Targets based on similar-scale projects; actual numbers need measurement |

---

## 15. Citations

| # | Reference | URL / Source |
| --- | ----------- | -------------- |
| 1 | PEP 654 — Exception Groups and ExceptionGroup | <https://peps.python.org/pep-0654/> |
| 2 | PEP 703 — Making the Global Interpreter Lock Optional in CPython | <https://peps.python.org/pep-0703/> |
| 3 | PEP 684 — A Per-Interpreter GIL | <https://peps.python.org/pep-0684/> |
| 4 | PEP 734 — Multiple Interpreters in the Stdlib | <https://peps.python.org/pep-0734/> |
| 5 | Python 3.11 What's New | <https://docs.python.org/3/whatsnew/3.11.html> |
| 6 | Python asyncio — Developing with asyncio | <https://docs.python.org/3/library/asyncio-dev.html> |
| 7 | Python asyncio.Queue docs | <https://docs.python.org/3/library/asyncio-queue.html> |
| 8 | Python `__slots__` documentation | <https://docs.python.org/3/reference/datamodel.html#slots> |
| 9 | SQLite WAL documentation | <https://www.sqlite.org/wal.html> |
| 10 | SQLite PRAGMA reference | <https://www.sqlite.org/pragma.html> |
| 11 | SQLite "When to Use SQLite" | <https://www.sqlite.org/whentouse.html> |
| 12 | orjson benchmarks | <https://github.com/ijl/orjson#benchmark> |
| 13 | Apache Arrow Python docs | <https://arrow.apache.org/docs/python/> |
| 14 | Parquet format specification | <https://parquet.apache.org/docs/> |
| 15 | pyarrow Parquet guide | <https://arrow.apache.org/docs/python/parquet.html> |
| 16 | Hypothesis stateful testing | <https://hypothesis.readthedocs.io/en/latest/stateful.html> |
| 17 | Hypothesis CI / max_examples configuration | <https://hypothesis.readthedocs.io/en/latest/details.html> |
| 18 | David MacIver, "Finding More Bugs with Less Code" | <https://hypothesis.works/articles/> |
| 19 | cosmic-ray mutation testing docs | <https://cosmic-ray.readthedocs.io/> |
| 20 | cosmic-ray CI workflow | <https://cosmic-ray.readthedocs.io/en/latest/workflow.html> |
| 21 | Papadakis et al., "Mutation Testing Advances: An Analysis and Survey" | ScienceDirect 2019, Information and Software Technology Vol. 116 |
| 22 | atheris Python fuzzer (Google) | <https://github.com/google/atheris> |
| 23 | Nathaniel J. Smith, "Notes on structured concurrency" | <https://vorpus.org/blog/notes-on-structured-concurrency/> |
| 24 | Martin Fowler, "Event Sourcing" | <https://martinfowler.com/eaaDev/EventSourcing.html> |
| 25 | Martin Fowler, "Approval Testing" | <https://martinfowler.com/bliki/ApprovalTest.html> |
| 26 | syrupy snapshot testing | <https://github.com/syrupy-project/syrupy> |
| 27 | pyperf Python benchmarking | <https://pyperf.readthedocs.io/> |
| 28 | pytest-xdist parallel testing | <https://pytest-xdist.readthedocs.io/> |
| 29 | litestream (SQLite replication + latency reference) | <https://github.com/benbjohnson/litestream> |
| 30 | SA-2 (hermes engram): fill engines + wire-up patterns | engram #519 |
| 31 | SA-8 (hermes engram): reproducibility + audit trail patterns | engram #521 |
| 32 | ADR-003 (hermes): single-writer SQLite model | `docs/decisions/` |
