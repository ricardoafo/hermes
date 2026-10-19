# Change: spec-9-strategy-development-protocol

**SDD change**: `spec-9-strategy-development-protocol`
**Date**: 2026-08-25
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#226)

Mirror of engram topic key `sdd/spec-9-strategy-development-protocol/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Deliver the scientific guardrails that make every subsequent spec's outputs
admissible as pre-certification evidence. Five application-layer modules enforce
the signed pre-registration protocol (`docs/experiments/swing-mvp1/pre-registration-v1.md`):

1. **Pre-registration parser** — typed accessor layer over the YAML frontmatter
   and key sections of the signed document. Single source of truth for constants
   used by all other guardrail modules.
2. **`DataSplitEnforcer`** — wrapper around `MarketDataPort` that raises
   `OOSDataLeakageError` when code reads inside the OOS test window outside
   `certification_mode`. Code-enforced read refusal per §4 discipline gate.
3. **`H1AcceptanceGate`** — evaluates `CertificationResult` against §9 thresholds,
   returning `AcceptanceDecision` with full audit trail of failed criteria.
4. **`KillTriggerEvaluator`** — evaluates running experiment state against §10 kill
   criteria, returning `KillDecision` with reason string.
5. **`LabNotebook` writer** — append-only writer for the lab notebook markdown file,
   enforcing the append-only invariant and computing per-entry SHA256 for integrity.

This spec MUST land before Spec 5 (SwingStrategyAdapter). Without these guardrails,
any strategy output is untrustworthy from a pre-certification evidence standpoint.

## Scope

### In

- `src/hermes/application/strategy_protocol/preregistration.py` — parser
- `src/hermes/application/strategy_protocol/data_split_enforcer.py` — OOS enforcer
- `src/hermes/application/strategy_protocol/h1_acceptance_gate.py` — H1 gate
- `src/hermes/application/strategy_protocol/kill_trigger_evaluator.py` — kill evaluator
- `src/hermes/application/strategy_protocol/lab_notebook.py` — append-only writer
- `src/hermes/application/strategy_protocol/__init__.py` — package init
- `src/hermes/domain/errors.py` — add `OOSDataLeakageError`, `LabNotebookAppendError`
- `tests/unit/application/strategy_protocol/` — full test suite
- `changelog.d/wave-spec-9-strategy-development-protocol.md` — fragment

### Out

- Running the strategy (Spec 5 SwingStrategyAdapter)
- Certification harness UI
- Multi-experiment support — hardcoded `swing-mvp1-v1` only
- Deviation-detection integration into an orchestrator loop
- Full statistical framework (DSR, walk-forward, CPCV) — calculators are Spec 6 scope

## Decisions

### D-1: Parser reads file at instantiation, caches in-memory, never re-reads

- **Decision**: `PreRegistration` reads and parses the YAML frontmatter and section
  headings at `__init__` time. All accessors return pre-parsed typed values.
- **Alternatives**: (a) lazy parsing per accessor — adds complexity without benefit
  since the file is tiny (~350 lines); (b) re-read on every call — pointless I/O.
- **Rationale**: The signed file is immutable (SHA256-anchored). Single parse at
  startup is safe, fast, and easy to test.
- **Cost of reversal**: LOW — add a `reload()` method.

### D-2: `DataSplitEnforcer` uses `contextvars.ContextVar` for certification mode

- **Decision**: `certification_mode` is a `ContextVar[bool]` defaulting to `False`.
  Setting it to `True` (via `with certification_context()`) lifts the OOS read
  refusal for the duration of the `async with` block only.
- **Alternatives**: (a) constructor flag — cannot be toggled per-call; (b) env var —
  too coarse, bypasses the context lifecycle; (c) threading.local — incompatible with
  async I/O (anyio policy).
- **Rationale**: `ContextVar` is the correct tool for request-scoped state in async
  Python. The context manager gives a clean enter/exit lifecycle.
- **Cost of reversal**: LOW — replace ContextVar with a constructor flag.

### D-3: Enforcer wraps `MarketDataPort` as a transparent delegation wrapper

- **Decision**: `DataSplitEnforcer` wraps any `MarketDataPort`-compatible object and
  delegates all calls after timestamp validation. It checks timestamps of every `Bar`,
  `Trade`, and `OrderbookDiff` yielded by the underlying port before passing them to
  the caller.
- **Alternatives**: (a) subclass `MarketDataPort` adapters — violates open/closed
  principle; (b) inject the enforcer as middleware before the port — same as wrapping,
  but less explicit.
- **Rationale**: Wrapping is the standard decorator pattern; it keeps enforcer logic
  out of adapters and makes it testable with a mock inner port.
- **Cost of reversal**: LOW — remove the wrapper.

### D-4: `H1AcceptanceGate` accepts a dataclass `CertificationResult`, not raw floats

- **Decision**: `CertificationResult` is a frozen dataclass carrying all metrics
  needed for §9 evaluation. `H1AcceptanceGate.evaluate()` returns `AcceptanceDecision`.
- **Alternatives**: (a) kwargs-only — no type safety; (b) Pydantic model — adds dep
  not in this stack.
- **Rationale**: Dataclasses are the project idiom for value objects. Frozen ensures
  immutability of the certification snapshot.
- **Cost of reversal**: LOW — convert to dict-based API.

### D-5: `LabNotebook` appends Markdown entries; SHA256 computed per entry

- **Decision**: Each entry is appended with a trailing SHA256 comment line
  `<!-- entry-sha256: <hex> -->`. The SHA256 is computed over the entry text (UTF-8).
  Overwrite is refused: if the last line of the file already matches the entry header,
  raise `LabNotebookAppendError`.
- **Alternatives**: (a) no SHA256 — weaker integrity; (b) SHA256 of full file — grows
  with file; cannot verify individual entries.
- **Rationale**: Per-entry SHA256 allows partial verification and matches the
  event-sourcing principle in §12 of pre-registration-v1.md.
- **Cost of reversal**: LOW — remove the SHA256 line.

## Requirements

### R-1: Parser exposes typed accessors aligned to pre-reg constants

- **A-1.1**: `PreRegistration.data_windows()` returns a typed object with
  `train_start`, `train_end`, `validation_start`, `validation_end`,
  `oos_start`, `oos_end` as `datetime` objects.
- **A-1.2**: `PreRegistration.universe()` returns a sequence of symbols
  (`["BTCUSDT", "ETHUSDT", "SOLUSDT"]` per §3).
- **A-1.3**: `PreRegistration.sizing_config()` returns risk fraction 0.01,
  hard cap fraction 0.05, max concurrent 3, correlation lock 0.80, time stop 90.
- **A-1.4**: `PreRegistration.h1_thresholds()` returns DSR threshold 0.7 and
  secondary metric thresholds per §9.
- **A-1.5**: `PreRegistration.kill_triggers()` returns kill threshold config per §10.

### R-2: `DataSplitEnforcer` blocks OOS reads outside certification mode

- **A-2.1**: Reading a `Bar` with `open_time >= OOS_START` raises
  `OOSDataLeakageError` when `certification_mode` is `False`.
- **A-2.2**: The same read SUCCEEDS when inside a `certification_context()` block.
- **A-2.3**: Reading a `Bar` inside `[train_start, oos_start)` always succeeds
  regardless of certification mode.
- **A-2.4**: `OOSDataLeakageError` includes the attempted timestamp in its message.

### R-3: `H1AcceptanceGate` correctly applies §9 thresholds

- **A-3.1**: DSR ≤ 0.7 → `accepted=False`, `"dsr"` in `failed_criteria`.
- **A-3.2**: Bootstrap CI lower bound ≤ 0 → `accepted=False`, `"bootstrap_ci"` in
  `failed_criteria`.
- **A-3.3**: Max drawdown ≥ 15% → `accepted=False`, `"max_drawdown"` in
  `failed_criteria`.
- **A-3.4**: All thresholds met → `accepted=True`, `failed_criteria` is empty tuple.
- **A-3.5**: Multiple failures reported in a single `AcceptanceDecision`.

### R-4: `KillTriggerEvaluator` fires on §10 conditions

- **A-4.1**: Drawdown ≥ 15% → `KillDecision(triggered=True, reason=...)`.
- **A-4.2**: Days since equity high > 90 → triggered.
- **A-4.3**: SA-9 halt signal → triggered.
- **A-4.4**: No trigger condition → `KillDecision(triggered=False, reason="")`.

### R-5: `LabNotebook` enforces append-only invariant

- **A-5.1**: First write creates the entry with trailing SHA256 comment.
- **A-5.2**: Appending a second entry succeeds (both entries present in file).
- **A-5.3**: Attempting to write an entry with the same header as the last entry
  raises `LabNotebookAppendError`.
- **A-5.4**: SHA256 in the comment matches `hashlib.sha256(entry_text.encode()).hexdigest()`.

## Design

### Package layout

```text
src/hermes/application/strategy_protocol/
├── __init__.py
├── preregistration.py       # PreRegistration parser
├── data_split_enforcer.py   # DataSplitEnforcer + certification_context
├── h1_acceptance_gate.py    # H1AcceptanceGate + CertificationResult + AcceptanceDecision
├── kill_trigger_evaluator.py # KillTriggerEvaluator + KillDecision
└── lab_notebook.py          # LabNotebook writer

tests/unit/application/strategy_protocol/
├── __init__.py
├── test_preregistration.py
├── test_data_split_enforcer.py
├── test_h1_acceptance_gate.py
├── test_kill_trigger_evaluator.py
└── test_lab_notebook.py
```

### Key dataclasses

```python
# h1_acceptance_gate.py
@dataclass(frozen=True, slots=True)
class CertificationResult:
    dsr: float
    bootstrap_ci_lower: float
    sharpe: float
    max_drawdown: float
    ulcer_index: float
    recovery_days: float
    win_rate: float
    oos_trade_count: int


@dataclass(frozen=True, slots=True)
class AcceptanceDecision:
    accepted: bool
    failed_criteria: tuple[str, ...]


# kill_trigger_evaluator.py
@dataclass(frozen=True, slots=True)
class ExperimentState:
    max_drawdown: float
    days_since_equity_high: float
    sa9_halt: bool
    rolling_30d_sharpe: float | None = None


@dataclass(frozen=True, slots=True)
class KillDecision:
    triggered: bool
    reason: str
```

### OOS boundary enforcement

```python
# data_split_enforcer.py
_certification_mode: ContextVar[bool] = ContextVar("certification_mode", default=False)


@contextmanager
def certification_context() -> Iterator[None]:
    token = _certification_mode.set(True)
    try:
        yield
    finally:
        _certification_mode.reset(token)
```

## Test plan

1. **Pre-registration parser** — property tests: roundtrip typed values against
   known constants from pre-registration-v1.md frontmatter; test all five accessors.
2. **DataSplitEnforcer** — parametrized matrix: bar in OOS raises/passes per mode;
   bar in train always passes; bar exactly at OOS boundary raises.
3. **H1AcceptanceGate** — hypothesis-driven property test: given any
   `CertificationResult` where DSR < 0.7, `accepted` must be False; exhaustive
   threshold matrix for all §9 conditions.
4. **KillTriggerEvaluator** — kill matrix: each trigger condition independently,
   combined conditions, no-trigger path.
5. **LabNotebook** — append-only invariant (second write with same header raises);
   SHA256 chain verified entry by entry; multi-entry round-trip.

## Risks

- **Risk 1 — Pre-reg file moved or renamed**. Parser hardcodes the path relative to
  project root. Mitigation: raise `FileNotFoundError` with a clear message at init
  time. This is intentional — if the signed file moves, something is wrong.
- **Risk 2 — `contextvars` not propagated across thread boundaries**. Enforcer uses
  `ContextVar`, which does NOT propagate to threads spawned by `asyncio.to_thread`
  unless the context is explicitly copied. Mitigation: document in docstring; for
  MVP1 all I/O is async-native so this is a future concern.
- **Risk 3 — LabNotebook grows unboundedly**. The file is append-only by design;
  no pruning mechanism. Mitigation: acceptable for MVP1 (tens of entries over months).

## Dependencies on other changes

- **Spec 4a (`spec-4a-clock-port`)** — `ClockPort` imported by `DataSplitEnforcer`
  for testable time injection. Already merged.
- **Spec 4b (`spec-4b-market-data-port`)** — `MarketDataPort` wrapped by enforcer.
  Already merged.
- **`src/hermes/domain/errors.py`** — `OOSDataLeakageError`, `LabNotebookAppendError`
  added in this change.
- **`docs/experiments/swing-mvp1/pre-registration-v1.md`** — read-only input;
  MUST NOT be modified (SIGNED).

## Tasks

- [x] T-1: Create openspec change.md + issue.md
- [ ] T-2: Add `OOSDataLeakageError` + `LabNotebookAppendError` to `domain/errors.py`
- [ ] T-3: Implement `preregistration.py`
- [ ] T-4: Implement `data_split_enforcer.py`
- [ ] T-5: Implement `h1_acceptance_gate.py`
- [ ] T-6: Implement `kill_trigger_evaluator.py`
- [ ] T-7: Implement `lab_notebook.py`
- [ ] T-8: Write test suite (5 test modules)
- [ ] T-9: Add `changelog.d` fragment
- [ ] T-10: `pr-preflight.sh` green + open PR
