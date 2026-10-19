"""Live risk input port: what RO-02, the depeg rules and the recovery kill read before real money.

Shadow runs what the backtest runs (#514, the shadow set), so these three
inputs stay neutral there. With real money (``allow_mainnet``) serve hands the
executor an implementation of this port and the rules act on real values
(#1158). The port carries plain numbers, so the decision modules never import
the record-only risk input store.

Reference: pre-registration v1 §5.5 (RO-02, RO-05, RO-06), §10 (recovery
kill); code-conventions.md §5 (Ports as Protocols); GitHub issue #1158.
"""

from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from hermes.domain.value_objects.pair import Pair

__all__ = ["LiveRiskInputsPort"]


@runtime_checkable
class LiveRiskInputsPort(Protocol):
    """Contract for the live-phase risk and kill inputs.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass) when a store cannot be read.
    """

    def median_spread_1h(self, pair: Pair, now: datetime) -> Decimal | None:
        """Return the pair's 1-hour median spread at ``now``, or ``None`` without a sample."""
        ...

    def stable_price(self, now: datetime) -> Decimal | None:
        """Return the stablecoin's USD price at ``now``, or ``None`` without a fresh reading."""
        ...

    def days_since_equity_high(self, now: datetime) -> float:
        """Return the days since the equity high the §10 recovery kill counts, or 0.0."""
        ...
