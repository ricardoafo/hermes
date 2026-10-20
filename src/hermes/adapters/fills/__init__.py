"""Fill adapters — concrete implementations of FillModelPort.

Public entry points:

- :class:`hermes.adapters.fills.orderbook_walk.OrderbookWalkFillModel` —
  orderbook-walk fill model for backtest and shadow phases (Spec 2b).

Reference: spec-2b-orderbook-walk-fill-model/change.md.
"""

from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel

__all__ = ["OrderbookWalkFillModel"]
