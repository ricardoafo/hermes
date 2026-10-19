"""Trading domain — order request and fill result types.

Public surface:

- :class:`~hermes.domain.trading.order.Order`      — immutable order request
- :class:`~hermes.domain.trading.order.OrderSide`  — BUY / SELL discriminant
- :class:`~hermes.domain.trading.fill.Fill`         — successful fill result
- :class:`~hermes.domain.trading.fill.Rejection`    — rejected order (expected outcome)

These types are consumed by :class:`~hermes.ports.fill_model.FillModelPort`
and produced by every concrete fill implementation — backtest, shadow, and live.
Domain purity: no pydantic, no httpx (enforced by import-linter contracts).
"""

from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide

__all__ = ["Fill", "Order", "OrderSide", "Rejection"]
