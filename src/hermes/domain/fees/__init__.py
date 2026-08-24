"""Fee-schedule domain value objects.

Public surface:

- :class:`FeeTier`      — one row of a volume-tiered fee schedule.
- :class:`FeeSchedule`  — the full tiered schedule for a trading pair.

Both are pure domain values: no pydantic, no httpx, no I/O. Adapters
translate wire responses (Kraken, Binance, ...) into these types at the
port boundary.
"""

from __future__ import annotations

from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier

__all__ = ["FeeSchedule", "FeeTier"]
