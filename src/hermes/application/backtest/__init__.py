"""Backtest application layer — the bar loop that drives the shared fill kernel.

Public surface:

- :func:`~hermes.application.backtest.replay.replay` — run bars through a
  strategy and the fill kernel
- :class:`~hermes.application.backtest.replay.ExecutionModel` — the
  pre-registration §6.2 execution prices and the synthetic depth
- :class:`~hermes.application.backtest.replay.BacktestRun` — what a run produced

Reference: openspec/archive/backtest-driver-mvp1-phase2/change.md D-1, D-4.
"""

from hermes.application.backtest.replay import BacktestRun, ExecutionModel, replay

__all__ = ["BacktestRun", "ExecutionModel", "replay"]
