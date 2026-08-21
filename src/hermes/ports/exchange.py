"""Exchange port — abstract Protocol for exchange adapters.

Implementations live in `hermes.adapters.exchanges`. Concrete choice
(binance-connector vs ccxt) is deferred until ADR-004 resolves.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ExchangePort(Protocol):
    """Minimum contract every exchange adapter must satisfy.

    Methods to be added in Phase 1 alongside first adapter implementation.
    """
