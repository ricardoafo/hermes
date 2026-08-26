"""Bar consolidator — produces closed OHLCV bars from a trade tick stream.

:class:`BarConsolidator` consumes an iterable of
:class:`~hermes.domain.market_data.trade.Trade` ticks and emits closed
:class:`~hermes.domain.market_data.bar.Bar` objects at the requested interval.

Design decisions (spec-4c D-3):

- **No lookahead**: a bar emitted for window ``[open_time, close_time)``
  contains only trades with ``trade_time < close_time``. Any trade with
  ``trade_time >= close_time`` belongs to the next window. The invariant
  is enforced by the consolidation loop — it never touches a future window
  until the current one closes.
- **Deterministic close_time**: ``close_time = open_time + interval_duration - 1ms``.
  This matches Binance Vision klines semantics (close_time is inclusive, 1ms
  before the next open_time). The ``Bar`` domain object requires
  ``close_time > open_time``; the consolidator guarantees this.
- **Empty windows**: if no trades arrive in a window, no bar is emitted.
  Callers that need zero-volume filler bars can post-process the output.
- **Volume aggregation**: ``bar.volume = sum(trade.quantity for trade in window)``.
- **OHLCV**: ``open`` is the price of the first trade; ``close`` is the price
  of the last trade; ``high`` / ``low`` are the per-window max/min prices.
- **Interval support**: any :class:`~hermes.domain.market_data.kline.KlineInterval`
  value supported by :func:`interval_to_timedelta`. Currently: ``M1``,
  ``M5``, ``H1``, ``H4``, ``D1``.
- **Synchronous**: the consolidator is a pure-Python generator — callers
  decide whether to consume it synchronously or wrap it in an async generator.

Reference: spec-4c-market-data-adapters change.md D-3, SA-4 P1.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

import structlog

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.trade import Trade
from hermes.domain.value_objects.pair import Pair

__all__ = ["BarConsolidator", "interval_to_timedelta"]

_log = structlog.get_logger(__name__)

# 1 ms as a timedelta — used to compute inclusive close_time from the
# exclusive window boundary (Binance Vision klines convention).
_ONE_MS: Final[timedelta] = timedelta(milliseconds=1)


def interval_to_timedelta(interval: KlineInterval) -> timedelta:
    """Return the :class:`~datetime.timedelta` corresponding to ``interval``.

    Args:
        interval: Candle-duration bucket.

    Returns:
        The duration of one candle.

    Raises:
        ValueError: When ``interval`` is not a known :class:`KlineInterval`.
    """
    mapping: dict[KlineInterval, timedelta] = {
        KlineInterval.M1: timedelta(minutes=1),
        KlineInterval.M5: timedelta(minutes=5),
        KlineInterval.H1: timedelta(hours=1),
        KlineInterval.H4: timedelta(hours=4),
        KlineInterval.D1: timedelta(days=1),
    }
    try:
        return mapping[interval]
    except KeyError as exc:
        raise ValueError(
            f"BarConsolidator: unsupported interval {interval!r}. Supported: {list(mapping)}"
        ) from exc


def _window_open_time(trade_time: datetime, duration: timedelta) -> datetime:
    """Compute the canonical open_time of the window containing ``trade_time``.

    The window is aligned to UTC epoch with no offset. For example, for a
    1-hour interval the window boundaries are on the hour; for 4-hour they
    are at 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC.

    Args:
        trade_time: UTC-aware trade timestamp.
        duration: Window duration (from :func:`interval_to_timedelta`).

    Returns:
        UTC-aware ``open_time`` for the window containing ``trade_time``.
    """
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    total_seconds = (trade_time - epoch).total_seconds()
    window_seconds = duration.total_seconds()
    window_start_seconds = (total_seconds // window_seconds) * window_seconds
    return epoch + timedelta(seconds=window_start_seconds)


class BarConsolidator:
    """Consolidates a trade stream into closed OHLCV bars at a fixed interval.

    Usage::

        consolidator = BarConsolidator(pair=btc_usdt, interval=KlineInterval.H4)
        for bar in consolidator.consolidate(trades):
            process(bar)

    Attributes:
        pair: Trading pair for the output bars.
        interval: Target candle-duration bucket.
    """

    def __init__(self, *, pair: Pair, interval: KlineInterval) -> None:
        """Initialise the consolidator.

        Args:
            pair: Trading pair.
            interval: Target candle-duration bucket.

        Raises:
            ValueError: When ``interval`` is not supported by
                :func:`interval_to_timedelta`.
        """
        self.pair = pair
        self.interval = interval
        self._duration: timedelta = interval_to_timedelta(interval)

    def consolidate(self, trades: Iterable[Trade]) -> Iterator[Bar]:
        """Consume ``trades`` and yield closed bars.

        Trades MUST be in ascending ``trade_time`` order. Out-of-order trades
        cause the consolidator to emit incorrect bars (no validation is
        performed — callers must sort first if needed).

        No lookahead invariant: for every emitted bar ``b``, every trade
        that contributed to ``b`` satisfies ``trade.trade_time < b.close_time``.

        Empty windows (no trades in a window) produce no bar.

        Args:
            trades: Iterable of :class:`~hermes.domain.market_data.trade.Trade`
                in ascending ``trade_time`` order.

        Yields:
            Closed :class:`~hermes.domain.market_data.bar.Bar` instances in
            ascending ``open_time`` order.
        """
        current_open_time: datetime | None = None
        window_trades: list[Trade] = []

        for trade in trades:
            trade_window_open = _window_open_time(trade.trade_time, self._duration)

            if current_open_time is None:
                # First trade — start the first window.
                current_open_time = trade_window_open
                window_trades.append(trade)
                continue

            if trade_window_open == current_open_time:
                # Same window — accumulate.
                window_trades.append(trade)
            else:
                # New window — emit the completed bar and start fresh.
                bar = self._build_bar(
                    open_time=current_open_time,
                    trades=window_trades,
                )
                _log.debug(
                    "consolidate: emitting bar",
                    pair=str(self.pair),
                    interval=str(self.interval),
                    open_time=bar.open_time.isoformat(),
                    volume=str(bar.volume),
                )
                yield bar
                current_open_time = trade_window_open
                window_trades = [trade]

        # Emit any remaining trades as the last (potentially partial) bar.
        # Per D-3: we only emit the last bar when the trade stream ends —
        # the caller decides whether to treat it as closed.
        if current_open_time is not None and window_trades:
            bar = self._build_bar(
                open_time=current_open_time,
                trades=window_trades,
            )
            _log.debug(
                "consolidate: emitting final bar",
                pair=str(self.pair),
                interval=str(self.interval),
                open_time=bar.open_time.isoformat(),
                volume=str(bar.volume),
            )
            yield bar

    def _build_bar(self, *, open_time: datetime, trades: list[Trade]) -> Bar:
        """Build a :class:`~hermes.domain.market_data.bar.Bar` from a list of trades.

        The ``close_time`` is set to ``open_time + duration - 1ms`` (inclusive
        Binance Vision convention). The ``Bar`` domain object validates
        ``close_time > open_time`` — this invariant holds because
        ``duration >= 1 minute > 1ms``.

        Args:
            open_time: Window open timestamp (UTC-aware).
            trades: Non-empty list of trades within the window.

        Returns:
            A closed :class:`~hermes.domain.market_data.bar.Bar`.

        Raises:
            ValueError: When ``trades`` is empty (programming error — callers
                MUST NOT call ``_build_bar`` with an empty list).
        """
        if not trades:
            raise ValueError("BarConsolidator._build_bar: trades list must not be empty")

        open_price = trades[0].price
        close_price = trades[-1].price
        high_price = max(t.price for t in trades)
        low_price = min(t.price for t in trades)
        total_volume = sum((t.quantity for t in trades), Decimal("0"))

        close_time = open_time + self._duration - _ONE_MS

        return Bar(
            pair=self.pair,
            interval=self.interval,
            open_time=open_time,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=total_volume,
            close_time=close_time,
        )
