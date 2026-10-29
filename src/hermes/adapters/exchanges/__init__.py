"""Exchange adapters — concrete implementations of exchange-facing ports.

Public entry points:

- :class:`hermes.adapters.exchanges.binance_klines.BinanceKlinesAdapter` —
  historical klines fetcher (issue #28) targeting Binance testnet.

The shared rate limiter (``hermes.adapters._shared``) is re-exported for
configuration wiring but not intended for direct call from application
code.
"""

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.exchanges.binance_klines import (
    BinanceKlinesAdapter,
    BinanceKlinesConfig,
)

__all__ = [
    "BinanceKlinesAdapter",
    "BinanceKlinesConfig",
    "RateLimitConfig",
    "RateLimiter",
]
