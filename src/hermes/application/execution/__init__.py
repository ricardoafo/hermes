"""Execution application layer — the one fill kernel backtest and serve share.

Public surface:

- :class:`~hermes.application.execution.kernel.FillKernel` — strategy signal to
  recorded fill
- :class:`~hermes.application.execution.kernel.MarketContext` — per-call market inputs
- :class:`~hermes.application.execution.kernel.Executed`,
  :class:`~hermes.application.execution.kernel.Declined`,
  :class:`~hermes.application.execution.kernel.Halted` — the outcomes

Reference: openspec/changes/backtest-driver-mvp1-phase2/change.md D-2; ADR-011 D-01.
"""

from hermes.application.execution.kernel import (
    Declined,
    Executed,
    ExecutionOutcome,
    FillKernel,
    Halted,
    MarketContext,
)

__all__ = [
    "Declined",
    "Executed",
    "ExecutionOutcome",
    "FillKernel",
    "Halted",
    "MarketContext",
]
