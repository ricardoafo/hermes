"""Risk overlay domain package — SA-9's 8 portfolio-shock rules.

Each sub-module implements one rule from SA-9 §5.5 as a stateless class
satisfying :class:`~hermes.ports.risk_overlay.RiskOverlayPort`.

Public re-exports:

- State types: :class:`~hermes.domain.risk.state.PortfolioState`,
  :class:`~hermes.domain.risk.state.MarketSnapshot`
- Rule classes: :class:`~hermes.domain.risk.correlation_lock.CorrelationLock`,
  :class:`~hermes.domain.risk.spread_spike.SpreadSpike`,
  :class:`~hermes.domain.risk.max_slippage.MaxSlippage`,
  :class:`~hermes.domain.risk.stale_book.StaleBook`,
  :class:`~hermes.domain.risk.depeg_warn.DepegWarn`,
  :class:`~hermes.domain.risk.depeg_halt.DepegHalt`,
  :class:`~hermes.domain.risk.macro_atr_halt.MacroATRHalt`,
  :class:`~hermes.domain.risk.portfolio_drawdown.PortfolioDrawdown`

Reference: spec-8-risk-overlay change.md; SA-9 §5.5.
"""

from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.risk.depeg_halt import DepegHalt
from hermes.domain.risk.depeg_warn import DepegWarn
from hermes.domain.risk.macro_atr_halt import MacroATRHalt
from hermes.domain.risk.max_slippage import MaxSlippage
from hermes.domain.risk.portfolio_drawdown import PortfolioDrawdown
from hermes.domain.risk.spread_spike import SpreadSpike
from hermes.domain.risk.stale_book import StaleBook
from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision

__all__ = [
    "CorrelationLock",
    "DepegHalt",
    "DepegWarn",
    "MacroATRHalt",
    "MarketSnapshot",
    "MaxSlippage",
    "PortfolioDrawdown",
    "PortfolioState",
    "RiskAction",
    "RiskDecision",
    "SpreadSpike",
    "StaleBook",
]
