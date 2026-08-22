"""Application layer — use cases and routing logic.

Imports only from ``hermes.domain`` and ``hermes.ports``; never from
``hermes.adapters`` or ``hermes.infrastructure`` (enforced by
``import-linter`` contract ``application-imports-only-domain-and-ports``).

Reference: code-conventions.md §1 (Ports & Adapters).
"""

from hermes.application.notification_router import EventClass, RouterConfig, route

__all__ = ["EventClass", "RouterConfig", "route"]
