# Change: spec-1-domain-aggregates

**SDD change**: `spec-1-domain-aggregates`
**Date**: 2026-08-25
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/spec-1-domain-aggregates/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

This change delivers the two foundational additions that every subsequent paper-trading spec (Specs
2-9) depend on: a `StrategyId` value object that makes every `Lot` aggregate bucket-aware from day
1, and `event_seq` + `payload_sha256` fields on `DomainEvent` that enable the event-sourcing hash
chain required for deterministic replay (SA-8). The architectural pick is BLAKE2b-256 (truncated to
32 bytes / 64 hex chars) for hashing — faster than SHA-256, same security margin for our use-case,
and cited in SA-8 as the recommended algorithm. The main risk is that adding fields to `DomainEvent`
is a backward-compat break for all existing event tests; every call site in `tests/unit/domain/events/`
must be updated to supply `event_seq` and `payload_sha256`.

## Scope

### In

- **New value object** `src/hermes/domain/value_objects/strategy_id.py` — `StrategyId`, string-typed
  wrapper, non-empty, printable-ASCII-safe, immutable `frozen=True, slots=True` dataclass. Follows
  the idiom of `LotId`, `Money`, etc.
- **`Lot` field addition** `src/hermes/domain/lots/lot.py` — add `strategy_id: StrategyId` as a
  mandatory field (no default). The `Lot.open` factory signature updated accordingly. All existing
  `Lot` construction call sites updated; MVP1 call sites use `StrategyId("swing")`.
- **`DomainEvent` field additions** `src/hermes/domain/events/base.py` — add `event_seq: int`
  (monotonic per-aggregate sequence, set by the caller) and `payload_sha256: str` (BLAKE2b-256 hex
  digest of the event's canonical JSON payload, auto-computed at construction time).
- **`payload_sha256` computation** is automatic: `__post_init__` on `DomainEvent` computes the hash
  from all fields except `payload_sha256` itself, serialized to JSON with `sort_keys=True` and
  `default=str`. Callers never supply it manually — it's derived.
- **All ~10 existing event subclasses** updated to include `event_seq` in their construction call
  sites (in tests), with existing business fields unchanged.
- **Tests updated**: all existing event tests supply `event_seq` in their fixtures.
- **New tests added**:
  - `tests/unit/domain/value_objects/test_strategy_id.py` — unit tests for `StrategyId`.
  - `tests/unit/domain/events/test_domain_event_hash.py` — property tests for hash determinism.
  - `tests/unit/domain/lots/test_lot.py` — `strategy_id` field tests added to existing file.

### Out

- Kelly f\* estimator (Spec 5).
- `Order`, `Fill`, `Position`, `Balance`, `Portfolio` aggregates (Spec 1 full scope, deferred to
  follow-up PRs — this PR delivers the two foundational additions the briefs describes).
- Port/adapter changes beyond test call-site fixes.
- Migrations of persisted events (no persisted events yet — SQLite ledger stores lots/trades, not
  domain events as event-sourced records).
- ULID-based `OrderId`, `FillId` (Spec 1 full scope follow-up).

## Decisions

### D-1: `StrategyId` shape — plain string wrapper, printable ASCII, non-empty

- **Decision**: `StrategyId` is a `frozen=True, slots=True` dataclass wrapping a single `value: str`
  field. Validation: non-empty, all characters in `string.printable`, max 64 chars.
- **Alternatives**: (a) `StrEnum` — would restrict values to a finite set, preventing MVP2 dynamic
  strategy registration. (b) bare `str` — no domain protection, leaks across layer boundaries.
- **Rationale**: Matches the pattern established by `LotId` (wrapped validated string). The
  printable-ASCII constraint is conservative and safe; it allows `"swing"`, `"balancer"`,
  `"swing-v2"` while rejecting control characters and non-ASCII that would corrupt log lines.
  `StrEnum` is ruled out by SA-6's finding that NautilusTrader uses `StrategyId` as a first-class
  domain value (not a config enum) to support N-strategy registration at runtime.
- **Cost of reversal**: LOW — only `StrategyId` consumers need updating; no persistence format
  affected (no persisted events yet).

### D-2: Hash algorithm — BLAKE2b-256 (hashlib built-in, 64-char hex)

- **Decision**: `payload_sha256` uses `hashlib.blake2b(digest_size=32)` (BLAKE2b with 32-byte /
  256-bit output), hex-encoded to 64 characters. The field is named `payload_sha256` for
  documentation compatibility with SA-8 and the pre-registration, but the actual algorithm is
  BLAKE2b-256.
- **Alternatives**: (a) SHA-256 (`hashlib.sha256`) — same output length, slower (no hardware
  acceleration on Python's `hashlib.sha256` vs `hashlib.blake2b`). (b) SHA3-256 — marginally
  slower, no advantage for our tamper-detection use case.
- **Rationale**: SA-8 explicitly cites BLAKE2 as the recommended algorithm for the event hash chain.
  BLAKE2b is in Python's standard `hashlib` (no new dependency). Output is 64 hex chars — fits the
  `≤ 64 chars` constraint in the spec brief. Speed advantage over SHA-256 on large event streams is
  material in replay scenarios.
- **Cost of reversal**: MEDIUM — changing algorithm changes all stored hash values, requiring a
  migration of any persisted `payload_sha256` column. No persistence exists yet, so reversal is
  LOW now but MEDIUM once Spec 4 persists events.

### D-3: `event_seq` semantics — caller-supplied, base validates non-negative

- **Decision**: `event_seq: int` is supplied by the caller (the aggregate or use-case that emits the
  event). `DomainEvent.__post_init__` validates it is ≥ 0. The base class does NOT enforce
  monotonicity — that is the caller's responsibility (an aggregate's internal counter).
- **Alternatives**: (a) auto-increment via a module-level counter — not thread-safe, not
  replay-safe. (b) enforce monotonicity in the base — impossible without state, violates frozen
  dataclass semantics.
- **Rationale**: SA-8 §Architecture #3 states `event_seq = SQLite rowid` — the database assigns the
  final sequence number at persist time. The domain object carries a logical sequence that the caller
  owns. The base validates the field is valid (non-negative) without owning the sequencing policy.
- **Cost of reversal**: LOW — adding stricter validation later is additive.

## Requirements

### R-1: `StrategyId` value object

- **A-1.1**: `uv run python -c "from hermes.domain.value_objects.strategy_id import StrategyId; StrategyId('swing')"` exits 0.
- **A-1.2**: `uv run python -c "from hermes.domain.value_objects.strategy_id import StrategyId; StrategyId('')"` exits non-zero (raises `ValueError`).
- **A-1.3**: `uv run python -c "from hermes.domain.value_objects.strategy_id import StrategyId; a = StrategyId('swing'); b = StrategyId('swing'); assert a == b and hash(a) == hash(b)"` exits 0.
- **A-1.4**: `uv run python -c "from hermes.domain.value_objects.strategy_id import StrategyId; s = StrategyId('swing'); s.value = 'changed'"` exits non-zero (frozen dataclass).

### R-2: `Lot` carries `strategy_id`

- **A-2.1**: `uv run python -c "from hermes.domain.lots.lot import Lot; import inspect; assert 'strategy_id' in inspect.signature(Lot).parameters"` exits 0.
- **A-2.2**: `uv run python -c "from hermes.domain.lots.lot import Lot; Lot(lot_id=None, asset=None, original_qty=None, remaining_qty=None, cost_basis_per_unit=None, opened_at=None)"` exits non-zero (missing `strategy_id` raises `TypeError`).
- **A-2.3**: `uv run pytest tests/unit/domain/lots/test_lot.py -x -q` exits 0.

### R-3: `DomainEvent` carries `event_seq` and `payload_sha256`

- **A-3.1**: `uv run python -c "from hermes.domain.events.base import DomainEvent; import inspect; p = inspect.signature(DomainEvent).parameters; assert 'event_seq' in p and 'payload_sha256' in p"` exits 0.
- **A-3.2**: `uv run python -c "from hermes.domain.events.trade_executed import TradeExecuted; help(TradeExecuted)"` exits 0 (subclasses still work).
- **A-3.3**: `uv run pytest tests/unit/domain/events/ -x -q` exits 0.

### R-4: `payload_sha256` is deterministic

- **A-4.1**: `uv run pytest tests/unit/domain/events/test_domain_event_hash.py -x -q` exits 0.
- **A-4.2**: Two constructions of the same event with identical fields produce identical `payload_sha256`.
- **A-4.3**: Changing any field produces a different `payload_sha256`.

### R-5: All existing tests pass

- **A-5.1**: `uv run pytest tests/unit/domain/ -x -q` exits 0.
- **A-5.2**: `uv run mypy --strict src/hermes` exits 0.

## Design

### `StrategyId` value object

```python
# src/hermes/domain/value_objects/strategy_id.py
from __future__ import annotations

import string
from dataclasses import dataclass

__all__ = ["StrategyId"]

_MAX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class StrategyId:
    """Immutable strategy identifier.

    A non-empty, printable-ASCII string of at most 64 characters that
    names a trading strategy bucket. Follows the value-object idiom of
    LotId and Money — wraps a validated primitive so strategy identity
    cannot be accidentally confused with arbitrary strings at the type
    level.

    Examples: ``"swing"``, ``"balancer"``, ``"swing-v2"``.

    Reference: SYNTHESIS §1 (bucket-aware aggregates), SA-6 (strategy_id
    as first-class domain field), spec-1-domain-aggregates D-1.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("StrategyId.value must not be empty")
        if len(self.value) > _MAX_LENGTH:
            raise ValueError(
                f"StrategyId.value must be at most {_MAX_LENGTH} characters, "
                f"got {len(self.value)}: {self.value!r}"
            )
        invalid = [c for c in self.value if c not in string.printable or c in string.whitespace]
        if invalid:
            raise ValueError(
                f"StrategyId.value contains non-printable or whitespace characters: "
                f"{invalid!r}"
            )

    def __str__(self) -> str:
        return self.value
```

### `Lot` field addition

Add `strategy_id: StrategyId` as the last field (before `opened_at` to preserve field ordering) in
`Lot` and update `Lot.open` factory signature:

```python
# In Lot dataclass fields — add after cost_basis_per_unit:
strategy_id: StrategyId
```

Update `Lot.open` factory:

```python
@classmethod
def open(
    cls,
    *,
    lot_id: LotId,
    asset: Currency,
    qty: Quantity,
    cost_basis_per_unit: Money,
    strategy_id: StrategyId,
    opened_at: datetime,
) -> Result[Lot, DomainError]:
    ...
```

### `DomainEvent` field additions

```python
# In DomainEvent dataclass — add after correlation_id:
event_seq: int
# payload_sha256 is COMPUTED by __post_init__ — set via object.__setattr__
# because the dataclass is frozen. Field declared with default to allow the
# post_init to set it:
payload_sha256: str = dataclasses.field(default="", init=False)

def __post_init__(self) -> None:
    if self.event_seq < 0:
        raise ValueError(
            f"DomainEvent.event_seq must be >= 0, got {self.event_seq}"
        )
    # Compute hash from all fields except payload_sha256 itself.
    payload = {
        k: v
        for k, v in dataclasses.asdict(self).items()
        if k != "payload_sha256"
    }
    digest = hashlib.blake2b(
        json.dumps(payload, sort_keys=True, default=str).encode(),
        digest_size=32,
    ).hexdigest()
    object.__setattr__(self, "payload_sha256", digest)
```

> Note: `payload_sha256` is declared `init=False` with a default of `""`. Because the dataclass is
> `frozen=True`, `__post_init__` must use `object.__setattr__` to write the computed value —
> this is the standard Python pattern for derived fields on frozen dataclasses.

### File tree (changes / additions)

```text
src/hermes/domain/value_objects/strategy_id.py      [NEW]
src/hermes/domain/lots/lot.py                        [AMEND — add strategy_id field + factory arg]
src/hermes/domain/events/base.py                     [AMEND — add event_seq + payload_sha256]
tests/unit/domain/value_objects/test_strategy_id.py  [NEW]
tests/unit/domain/events/test_domain_event_hash.py   [NEW]
tests/unit/domain/lots/test_lot.py                   [AMEND — update fixtures + new tests]
tests/unit/domain/events/test_base.py                [AMEND — update fixtures]
tests/unit/domain/events/test_trade_executed.py      [AMEND — update fixtures]
tests/unit/domain/events/test_anomaly_detected.py    [AMEND]
tests/unit/domain/events/test_lot_closed.py          [AMEND]
tests/unit/domain/events/test_balance_low.py         [AMEND]
tests/unit/domain/events/test_trade_rejected.py      [AMEND]
tests/unit/domain/events/test_rebalance_triggered.py [AMEND]
tests/unit/domain/events/test_daily_trade_digest.py  [AMEND]
tests/unit/domain/events/test_strategy_paused.py     [AMEND]
tests/unit/domain/events/test_strategy_resumed.py    [AMEND]
openspec/changes/spec-1-domain-aggregates/change.md  [NEW]
openspec/changes/spec-1-domain-aggregates/issue.md   [NEW]
```

## Test plan

1. Every acceptance criterion command in §Requirements exits 0.
2. `uv run pytest tests/unit/domain/ -x -q` → all green.
3. `uv run mypy --strict src/hermes` → 0 errors.
4. `bash scripts/dev/pr-preflight.sh` → all checks green.
5. New test files:
   - `test_strategy_id.py`: constructor validation (empty, too long, whitespace, non-printable,
     valid), equality, hashability, `__str__`, frozen immutability.
   - `test_domain_event_hash.py`: determinism (same fields → same hash), sensitivity (any field
     change → different hash), `event_seq` negative → ValueError.

## Risks

- **Backward-compat break on `DomainEvent`**: Adding two new required fields (`event_seq`,
  `payload_sha256`) to the base class breaks every existing event test that constructs events
  without those fields. Mitigation: `payload_sha256` is `init=False` (auto-computed), so callers
  only need to add `event_seq`. All ~10 event test files updated in this PR.
- **`frozen=True` + computed field via `object.__setattr__`**: This is the standard CPython
  pattern for derived fields on frozen dataclasses but it bypasses the slot descriptor. Mitigation:
  `payload_sha256` is NOT declared in `__slots__` via `slots=True` — we drop `slots=True` from
  `DomainEvent` (but keep it on all subclasses) to allow `object.__setattr__` to work. Subclasses
  that use `slots=True` inherit the slot from the parent class (`payload_sha256` is a regular
  instance attribute, not a slot). Alternatively, remove `slots=True` from the base only.
- **`dataclasses.asdict` and `DomainEvent` subclasses**: `dataclasses.asdict` recursively
  converts nested dataclasses. This is correct behavior for our use case but means the hash
  includes all nested value objects serialized through `str`. The `default=str` fallback in
  `json.dumps` handles any non-JSON-serializable types (datetimes, Decimals, enums).

## Tasks

- [x] T-1: Create `src/hermes/domain/value_objects/strategy_id.py` with `StrategyId` value object.
- [x] T-2: Update `src/hermes/domain/lots/lot.py` — add `strategy_id: StrategyId` field +
  update `Lot.open` factory signature.
- [x] T-3: Update `src/hermes/domain/events/base.py` — add `event_seq: int` +
  `payload_sha256: str` (computed) to `DomainEvent`.
- [x] T-4: Update all ~10 event subclass test files to include `event_seq` in fixtures.
- [x] T-5: Update `tests/unit/domain/lots/test_lot.py` — add `strategy_id` to all `Lot`
  constructions + add new `strategy_id`-specific tests.
- [x] T-6: Create `tests/unit/domain/value_objects/test_strategy_id.py`.
- [x] T-7: Create `tests/unit/domain/events/test_domain_event_hash.py`.
- [x] T-8: Run `uv run pytest tests/unit/domain/ -x` until green.
- [x] T-9: Run `uv run mypy --strict src/hermes` until 0 errors.
- [x] T-10: Run `bash scripts/dev/pr-preflight.sh` until all checks pass.

## Dependencies on other changes

- None — this change is the foundational layer. All other specs (2-9) depend on it but this
  change has no upstream dependencies.
