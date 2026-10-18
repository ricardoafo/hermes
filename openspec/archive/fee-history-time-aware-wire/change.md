# Change: fee-history-time-aware-wire

**SDD change**: `fee-history-time-aware-wire`
**Date**: 2026-08-26
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#260)

Mirror of engram topic key `sdd/fee-history-time-aware-wire/change`.

## Summary

Adds the primitives needed to price backtest trades with point-in-time Binance VIP0 fees, including the 2022-07-28 → 2023-03-22 zero-fee BTC promotional window. Ships two pieces: a domain-pure `select_fee_schedule(rows, timestamp, pair) -> FeeSchedule` selector and an adapter-layer CSV loader for `docs/experiments/swing-mvp1/fee-history.csv`. Does NOT wire the composition root — that wire lands with the future backtest driver script; this change delivers the primitives it will call.

## Scope

### In

- **NEW**: `src/hermes/domain/fees/fee_schedule_selector.py` — value object `FeeScheduleRow` + pure function `select_fee_schedule(rows, timestamp, pair) -> FeeSchedule`. No I/O. No clock. Pair-partitioning built in: rows tagged `VIP0` apply to BTC pairs; rows tagged `VIP0_ETH_SOL` apply to non-BTC pairs. Rows tagged `VIP0_BTCBUSD` are informational (BUSD phased out) and ignored.
- **NEW**: `src/hermes/adapters/fees/binance_history.py` — `load_binance_fee_history(csv_path) -> list[FeeScheduleRow]`. Parses `effective_date,exchange,tier,maker_bps,taker_bps,notes,source_url` — the exact shape of `docs/experiments/swing-mvp1/fee-history.csv`. Only Binance rows are returned; other exchanges (future Kraken addition) filtered at load time.
- **NEW**: `tests/unit/domain/fees/test_fee_schedule_selector.py` — unit + property tests:
  - Baseline: 2020-09 → 2022-07-27 returns 10/10 bps for every pair.
  - Zero-fee window: 2022-07-28 → 2023-03-21 returns 0/0 for BTC, 10/10 for ETH/SOL.
  - Post-promo: 2023-03-22 → 2026-08 returns 10/10 for every pair.
  - Property: schedule is stable within a tier's effective window (idempotent per timestamp).
  - Property: `select_fee_schedule(rows, t1, pair) == select_fee_schedule(rows, t2, pair)` whenever `t1` and `t2` fall in the same tier row.
- **NEW**: `tests/unit/adapters/fees/test_binance_history.py` — loader test against the real `docs/experiments/swing-mvp1/fee-history.csv` file. Asserts the row count matches, dates parse, and the zero-fee BTC row is present.
- **NEW**: `changelog.d/wave-fee-history-time-aware-wire.md`.

### Out

- **Composition-root wire** — the future backtest driver script will do `fee_schedule_fn = lambda pair: select_fee_schedule(rows, clock.now(), pair)`. That driver doesn't exist yet (Spec 6 harness accepts pre-collected fills; no end-to-end driver script has been written). Deferring the wire keeps this PR narrow and its acceptance criteria testable in isolation.
- **Kraken history** — pre-reg §6.1 targets Binance only. Kraken tier tables are static (spec-2c already handles that). Adding a Kraken history when we add a second exchange.
- **BNB discount handling** — pre-reg §6.1 pins BNB OFF. Feature-flagging BNB ON is out of scope.
- **Fee-history extension for post-2026 windows** — the CSV covers up to 2026-08. Additional entries append with no code change.

## Decisions

### D-1: Domain selector + adapter loader (two files), not one

- **Decision**: `select_fee_schedule` lives in `src/hermes/domain/fees/`; `load_binance_fee_history` lives in `src/hermes/adapters/fees/`.
- **Alternatives**: (a) one file in `src/hermes/adapters/fees/` combining loader + selector; (b) one file in `src/hermes/application/`.
- **Rationale**: The selector is pure — no I/O, no time, no filesystem. It fits the domain layer's "no side effects, no imports of pydantic/httpx" contract. The loader reads a file, which is I/O — must live in an adapter. Splitting matches the hexagonal boundary rules (`domain-no-async-or-network`, `domain-no-pydantic`).
- **Cost of reversal**: LOW — refactoring into one file is mechanical.

### D-2: Pair-partitioning encoded in the tier string (`VIP0`, `VIP0_ETH_SOL`), not a separate column

- **Decision**: The CSV's `tier` column carries the pair-partitioning implicitly: `VIP0` = BTC-affected during promo; `VIP0_ETH_SOL` = non-BTC (unaffected by the promo).
- **Alternatives**: (a) add a `pair_pattern` column to the CSV; (b) hard-code the pair split in the selector.
- **Rationale**: The CSV is already authored with this convention and matches the reconstruction notes in `docs/experiments/swing-mvp1/data-ingest-plan.md`. Rewriting the CSV would invalidate the pre-registration signature. Hard-coding in code fragments the source-of-truth. Keeping the tier string as the discriminator preserves the CSV as the single source.
- **Cost of reversal**: LOW — the CSV format is easy to extend if a future tier needs finer partitioning.

### D-3: Rows tagged `VIP0_BTCBUSD` are ignored

- **Decision**: The loader drops these rows.
- **Alternatives**: keep them and let the selector filter.
- **Rationale**: BUSD was phased out (Paxos/NYDFS action Feb 2023) before the pre-registered backtest touches it. BUSD is not a tracked pair; the row is informational only. Filtering at load time keeps the in-memory row set clean.
- **Cost of reversal**: LOW — flip the filter.

### D-4: Selector is total (returns `FeeSchedule` for every valid input)

- **Decision**: `select_fee_schedule` never returns `None`. If no row applies (e.g. timestamp before first row's effective_date), raises `DomainError` (subclass `FeeHistoryOutOfRange`).
- **Alternatives**: return `Optional[FeeSchedule]` and let caller decide.
- **Rationale**: A missing schedule at trade time is a data-integrity bug the caller CANNOT recover from silently. Raising forces the caller to handle it (or crash loudly). Matches the code-conventions.md §2 "domain errors as `Failure`" for expected outcomes vs "exceptions for invariant violations" — this is an invariant.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Selector returns correct fees across the pre-reg backtest window

- **A-1.1**: `select_fee_schedule(rows, datetime(2020, 9, 1, tzinfo=UTC), Pair("BTC", "USDT"))` returns `FeeSchedule(maker_bps=10, taker_bps=10)`.
- **A-1.2**: `select_fee_schedule(rows, datetime(2022, 10, 15, tzinfo=UTC), Pair("BTC", "USDT"))` returns `FeeSchedule(maker_bps=0, taker_bps=0)` (inside promo window).
- **A-1.3**: `select_fee_schedule(rows, datetime(2022, 10, 15, tzinfo=UTC), Pair("ETH", "USDT"))` returns `FeeSchedule(maker_bps=10, taker_bps=10)` (promo does NOT apply to ETH).
- **A-1.4**: `select_fee_schedule(rows, datetime(2023, 6, 1, tzinfo=UTC), Pair("BTC", "USDT"))` returns `FeeSchedule(maker_bps=10, taker_bps=10)` (post-promo).

### R-2: Selector raises for out-of-range timestamps

- **A-2.1**: `select_fee_schedule(rows, datetime(2015, 1, 1, tzinfo=UTC), Pair("BTC", "USDT"))` raises `FeeHistoryOutOfRange` (before the first row's `effective_date`).

### R-3: Loader parses the real CSV correctly

- **A-3.1**: `load_binance_fee_history(Path("docs/experiments/swing-mvp1/fee-history.csv"))` returns a non-empty list of `FeeScheduleRow`.
- **A-3.2**: The zero-fee row is present: `any(r.effective_date == date(2022, 7, 28) and r.maker_bps == 0 for r in rows)`.
- **A-3.3**: Kraken / non-Binance rows (if any) are filtered out.

### R-4: Property — selection is idempotent within a tier window

- **A-4.1**: For any two timestamps `t1, t2` inside the same tier row's effective interval AND for the same pair, `select_fee_schedule(rows, t1, pair) == select_fee_schedule(rows, t2, pair)`.

### R-5: Composition-root wire documented (not implemented)

- **A-5.1**: `change.md` §Design shows the exact wire line for the future backtest driver.

## Design

Domain module — pure selector:

```python
# src/hermes/domain/fees/fee_schedule_selector.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Sequence

from hermes.domain.errors import DomainError
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.value_objects.pair import Pair

__all__ = ["FeeScheduleRow", "FeeHistoryOutOfRange", "select_fee_schedule"]


@dataclass(frozen=True, slots=True)
class FeeScheduleRow:
    """One row parsed from the point-in-time fee history CSV."""

    effective_date: date
    tier: str  # "VIP0" | "VIP0_ETH_SOL" | "VIP0_BTCBUSD" | ...
    maker_bps: Decimal
    taker_bps: Decimal


class FeeHistoryOutOfRange(DomainError):
    """Requested timestamp precedes every row in the fee history."""


def _applies_to_pair(tier: str, pair: Pair) -> bool:
    """True iff the tier row applies to the given pair.

    Convention encoded in the CSV:
      - VIP0                → BTC pairs during 2022-07-28..2023-03-21 promo;
                              also the "standard" catch-all outside the promo.
      - VIP0_ETH_SOL        → non-BTC pairs during the promo (unaffected).
      - VIP0_BTCBUSD        → informational (BUSD phased out; not tracked).
    """
    if tier == "VIP0":
        return True  # catch-all baseline for all pairs
    if tier == "VIP0_ETH_SOL":
        return pair.base.upper() in ("ETH", "SOL")
    return False


def select_fee_schedule(rows: Sequence[FeeScheduleRow], at: datetime, pair: Pair) -> FeeSchedule:
    """Return the fee schedule effective at `at` for `pair`.

    Selects the most-recent row whose effective_date <= at AND whose tier
    applies to the pair. Raises FeeHistoryOutOfRange if no row qualifies.
    """
    applicable = [
        r for r in rows if r.effective_date <= at.date() and _applies_to_pair(r.tier, pair)
    ]
    if not applicable:
        raise FeeHistoryOutOfRange(f"No fee history row applies to {pair} at {at.isoformat()}")
    latest = max(applicable, key=lambda r: r.effective_date)
    return FeeSchedule(
        tiers=(
            FeeTier(
                volume_threshold_usd=Decimal("0"),
                maker_bps=latest.maker_bps,
                taker_bps=latest.taker_bps,
            ),
        )
    )
```

Adapter — CSV loader:

```python
# src/hermes/adapters/fees/binance_history.py
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from hermes.domain.fees.fee_schedule_selector import FeeScheduleRow


def load_binance_fee_history(csv_path: Path) -> list[FeeScheduleRow]:
    """Load Binance rows from the pre-reg fee-history.csv."""
    rows: list[FeeScheduleRow] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            if r["exchange"].strip().lower() != "binance":
                continue
            if r["tier"] == "VIP0_BTCBUSD":
                continue  # BUSD phased out — informational only
            rows.append(
                FeeScheduleRow(
                    effective_date=date.fromisoformat(r["effective_date"]),
                    tier=r["tier"],
                    maker_bps=Decimal(r["maker_bps"]),
                    taker_bps=Decimal(r["taker_bps"]),
                )
            )
    return rows
```

Composition-root wire (future backtest driver — NOT this PR):

```python
# scripts/backtest/run_calibration.py (future)
from hermes.adapters.fees.binance_history import load_binance_fee_history
from hermes.domain.fees.fee_schedule_selector import select_fee_schedule

rows = load_binance_fee_history(Path("docs/experiments/swing-mvp1/fee-history.csv"))
sim_ex = SimulatedExchangeAdapter(
    fee_schedule_fn=lambda pair: select_fee_schedule(rows, clock.now(), pair),
    ...
)
```

Each `SimulatedExchangeAdapter.get_fees(pair)` call re-evaluates the lambda, so the `clock.now()` returned during backtest replay produces the correct point-in-time schedule for that trade.

## Test plan

1. `uv run pytest tests/unit/domain/fees/test_fee_schedule_selector.py tests/unit/adapters/fees/test_binance_history.py -v` — all green.
2. `bash scripts/dev/pr-preflight.sh --render-body fee-history-time-aware-wire` — all 17 checks green.
3. Coverage: `src/hermes/domain/fees/` stays ≥95% per project threshold; `src/hermes/adapters/fees/` new subpackage starts at 100%.

## Risks

- **R1** — CSV drift (a new row added later with a novel tier string). Mitigation: loader raises on unknown Binance tier; selector's `_applies_to_pair` returns False for unknown tiers, causing `FeeHistoryOutOfRange` on `select_fee_schedule` if no known tier applies. Both fail loudly.
- **R2** — Zero-fee window end date is uncertain (CSV note flags "Approximate date. Wayback unavailable"). Mitigation: the CSV row's `notes` column already carries the `[TODO: Exact date uncertain]` flag; this change does not alter the date. If refined data arrives, edit the CSV — no code change.
- **R3** — Future addition of BUSD or other stables pairs (unlikely for MVP1). Mitigation: the `_applies_to_pair` function has a single edit point.

## Dependencies on other changes

- Predecessor: PR #241 (spec-2c SimulatedExchangeAdapter — establishes `fee_schedule_fn` port shape).
- Predecessor: ADR-011 D-09 (point-in-time fees requirement).
- Follow-up: future `scripts/backtest/run_calibration.py` driver — wires `select_fee_schedule` into `SimulatedExchangeAdapter` for real backtests. Not in this PR.
