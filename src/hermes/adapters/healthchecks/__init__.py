"""Healthchecks.io adapter — dead-man's-switch heartbeat monitor.

Re-exports the concrete adapter, its wire config, and the domain
error class raised on ping failure. See :mod:`hermes.adapters.healthchecks.hc`
for the design notes.
"""

from hermes.adapters.healthchecks.hc import HealthchecksAdapter, HealthchecksAdapterConfig
from hermes.domain.errors import HealthchecksError

__all__ = [
    "HealthchecksAdapter",
    "HealthchecksAdapterConfig",
    "HealthchecksError",
]
