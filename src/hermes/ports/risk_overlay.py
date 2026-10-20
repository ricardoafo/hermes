"""Risk overlay port — abstract Protocol for portfolio-shock rule implementations.

Each of the 8 SA-9 risk rules (RO-01 through RO-08) implements this Protocol.
Callers iterate over all registered rules, call ``evaluate`` on each, and
take the worst outcome (HALT > WARN > OK).

Implementations live in :mod:`hermes.domain.risk`.

Re-exports :class:`~hermes.domain.risk.types.RiskAction` and
:class:`~hermes.domain.risk.types.RiskDecision` from the domain layer so
callers only need one import path.

Reference: SA-9 §5.5 — risk overlay; spec-8-risk-overlay change.md.
"""

from typing import Protocol, runtime_checkable

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision

__all__ = [
    "RiskAction",
    "RiskDecision",
    "RiskOverlayPort",
]


@runtime_checkable
class RiskOverlayPort(Protocol):
    """Minimum contract every risk-overlay rule implementation must satisfy.

    Any object with a compliant :meth:`evaluate` method is a valid
    ``RiskOverlayPort`` — no inheritance required (structural typing via
    :class:`typing.Protocol`). :func:`~typing.runtime_checkable` enables
    ``isinstance`` checks in conformance tests.

    Each rule receives the full :class:`~hermes.domain.risk.state.PortfolioState`
    and :class:`~hermes.domain.risk.state.MarketSnapshot` and returns a
    :class:`~hermes.domain.risk.types.RiskDecision`. Rules MUST be stateless
    — all state lives in the inputs.

    Reference: spec-8-risk-overlay change.md § Design.
    """

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Evaluate the risk rule against current state and market data.

        Args:
            portfolio_state: Current portfolio positions, correlations, and
                drawdown information.
            market_snapshot: Current market-microstructure data for the pair
                under evaluation.

        Returns:
            A :class:`~hermes.domain.risk.types.RiskDecision` with the rule's
            verdict and reason.
        """
        ...
