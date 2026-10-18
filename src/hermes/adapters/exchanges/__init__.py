"""Exchange adapters — concrete implementations of exchange-facing ports.

Public entry points:

- :class:`hermes.adapters.exchanges.binance_klines.BinanceKlinesAdapter` —
  historical klines fetcher (issue #28) targeting Binance testnet.
- :class:`hermes.adapters.exchanges.simulated.SimulatedExchangeAdapter` —
  composition-root-swappable ``ExchangePort`` for backtest and shadow modes
  (spec-2c, issue #238).

The shared rate limiter (``hermes.adapters._shared``) is re-exported for
configuration wiring but not intended for direct call from application
code.
"""

from __future__ import annotations

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.exchanges.binance_klines import (
    BinanceKlinesAdapter,
    BinanceKlinesConfig,
)
from hermes.adapters.exchanges.simulated import SimulatedExchangeAdapter

__all__ = [
    "BinanceKlinesAdapter",
    "BinanceKlinesConfig",
    "RateLimitConfig",
    "RateLimiter",
    "SimulatedExchangeAdapter",
]
