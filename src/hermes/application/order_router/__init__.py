"""OrderRouter package — T-1 sizing composition + risk overlay + kill triggers.

Public API for the application-layer order routing pipeline:

- :class:`~hermes.application.order_router.router.OrderRouter` — orchestrator.
- :class:`~hermes.application.order_router.router.RoutingInput` — call-time input bundle.
- :class:`~hermes.application.order_router.router.RiskRejected` — risk-blocked outcome.
- :class:`~hermes.application.order_router.router.KillCircuitBreaker` — kill outcome.
- :class:`~hermes.application.order_router.risk_engine.RiskEngine` — rule composer.
- :class:`~hermes.application.order_router.sizing.SizingCalculator` — T-1 calculator.
- :class:`~hermes.application.order_router.sizing.SizingInput` — sizing inputs.

Reference: spec-3-order-router change.md.
"""

from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.order_router.router import (
    KillCircuitBreaker,
    OrderRouter,
    OrderRouterResult,
    RiskRejected,
    RoutingInput,
)
from hermes.application.order_router.sizing import SizingCalculator, SizingInput

__all__ = [
    "KillCircuitBreaker",
    "OrderRouter",
    "OrderRouterResult",
    "RiskEngine",
    "RiskRejected",
    "RoutingInput",
    "SizingCalculator",
    "SizingInput",
]
