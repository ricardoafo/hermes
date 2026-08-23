"""Klines fetcher port — abstract Protocol for OHLCV historical data sources.

Concrete implementations live under :mod:`hermes.adapters.exchanges`. The
first is :mod:`hermes.adapters.exchanges.binance_klines` for Binance
testnet; a Kraken sibling lands in Phase 2 per ADR-004.

The interface is deliberately narrow — a single :meth:`fetch` returning
an iterable — because callers only need "give me the candles for this
window" semantics. Rate-limiting, pagination, retry, and error mapping
are adapter concerns hidden behind the port.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["KlinesFetcherPort"]


@runtime_checkable
class KlinesFetcherPort(Protocol):
    """Contract every historical-klines adapter satisfies.

    Adapters SHOULD:

    - honour exchange rate limits internally (token bucket or equivalent);
    - transparently paginate when ``since -> until`` spans more than one
      API request's worth of candles;
    - convert exchange-native timestamps and numeric precision to UTC
      :class:`datetime` and :class:`~decimal.Decimal` at the port boundary
      so callers never see wire format leakage.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass) on any wire, HTTP-status, or schema failure. Callers are
    the application layer, which converts to
    :class:`returns.result.Result` per code-conventions §2.
    """

    def fetch(
        self,
        *,
        pair: Pair,
        interval: KlineInterval,
        since: datetime,
        until: datetime,
    ) -> Iterable[Kline]:
        """Yield candles for ``pair`` at ``interval`` in ``[since, until)``.

        Args:
            pair: Trading pair.
            interval: Candle-duration bucket.
            since: Inclusive lower bound (UTC-aware). Naive datetimes are
                rejected by the adapter.
            until: Exclusive upper bound (UTC-aware). ``until > since``
                is a precondition; adapters may raise on violation.

        Returns:
            An iterable of :class:`~hermes.domain.market_data.kline.Kline`
            in ascending ``open_time`` order. Empty iterable when the
            window contains no candles (e.g. exchange returned nothing
            for a range prior to the pair's listing).

        Raises:
            hermes.domain.errors.InfraError: On any adapter-level failure.
        """
        ...
