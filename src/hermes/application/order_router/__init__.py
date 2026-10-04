"""OrderRouter package — T-1 sizing composition + risk overlay + kill triggers.

Public API for the application-layer order routing pipeline:

- :class:`~hermes.application.order_router.router.OrderRouter` — orchestrator.
- :class:`~hermes.application.order_router.router.RoutingInput` — call-time input bundle.
- :class:`~hermes.application.order_router.router.RiskRejected` — risk-blocked outcome.
- :class:`~hermes.application.order_router.router.KillCircuitBreaker` — kill outcome.
- :class:`~hermes.application.order_router.risk_engine.RiskEngine` — rule composer.
- :class:`~hermes.application.order_router.sizing.SizingCalculator` — pre-reg §5.4 sizing.
- :class:`~hermes.application.order_router.sizing.SizingPolicy` — configured sizing limits.
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
from hermes.application.order_router.sizing import SizingCalculator, SizingInput, SizingPolicy

__all__ = [
    "KillCircuitBreaker",
    "OrderRouter",
    "OrderRouterResult",
    "RiskEngine",
    "RiskRejected",
    "RoutingInput",
    "SizingCalculator",
    "SizingInput",
    "SizingPolicy",
]
