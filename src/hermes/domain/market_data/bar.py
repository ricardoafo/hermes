"""Bar value object — closed-bar OHLCV streaming event.

A :class:`Bar` is one completed candle emitted by a live market-data stream.
It carries open/high/low/close/volume as plain :class:`~decimal.Decimal` values
rather than the pair-aware :class:`~hermes.domain.value_objects.price.Price` /
:class:`~hermes.domain.value_objects.quantity.Quantity` wrappers used by
:class:`~hermes.domain.market_data.kline.Kline` — live adapters receive raw
decimals from the wire and do not always have full pair context at event time.

``Bar`` and ``Kline`` carry the same semantic interval
(:class:`~hermes.domain.market_data.kline.KlineInterval`) so application code
can bucket both shapes with the same interval logic.

Domain purity: this module imports only domain value objects and stdlib. No
pydantic, no httpx (enforced by import-linter contracts).

Reference: code-conventions.md §1 (Value Object) and §9 (UTC internally).
spec-4b-market-data-port change.md §Design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["Bar"]


@dataclass(frozen=True, slots=True)
class Bar:
    """Immutable closed-bar OHLCV event for one ``(pair, interval, open_time)`` tuple.

    Attributes:
        pair: Trading pair the bar belongs to (e.g. ``BTC/USDT``).
        interval: The candle-duration bucket, shared with
            :class:`~hermes.domain.market_data.kline.KlineInterval`.
        open_time: First tick of the bar window, timezone-aware UTC. The
            constructor rejects naive datetimes (per code-conventions §9).
        open: Open price in quote currency. Must be > 0.
        high: Highest trade price within the window. Must be >= open, close,
            and low.
        low: Lowest trade price within the window. Must be > 0 and <= open,
            close, and high.
        close: Close price in quote currency. Must be > 0.
        volume: Total base-asset quantity traded within the window. Must be >= 0.
        close_time: Last tick of the bar window (exclusive bound),
            timezone-aware UTC. Must be strictly after ``open_time``.

    Reference: code-conventions.md §1.
    spec-4b-market-data-port change.md D-3.
    """

    pair: Pair
    interval: KlineInterval
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: datetime

    def __post_init__(self) -> None:
        """Enforce UTC-aware timestamps and OHLCV price/volume bounds.

        Raises:
            ValueError: On naive or non-UTC ``open_time`` / ``close_time``,
                ``close_time <= open_time``, non-positive prices, negative
                volume, or inconsistent high/low/open/close relationships.
        """
        _utc_offset = UTC.utcoffset(None)
        for field_name, dt in (("open_time", self.open_time), ("close_time", self.close_time)):
            if dt.tzinfo is None or dt.tzinfo.utcoffset(None) != _utc_offset:
                raise ValueError(f"Bar.{field_name} must be UTC-aware, got {dt!r}")
        if self.close_time <= self.open_time:
            raise ValueError(
                f"Bar.close_time ({self.close_time!r}) must be strictly after "
                f"Bar.open_time ({self.open_time!r})"
            )
        for field_name, value in (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
        ):
            if value <= Decimal("0"):
                raise ValueError(f"Bar.{field_name} must be > 0, got {value}")
        if self.volume < Decimal("0"):
            raise ValueError(f"Bar.volume must be >= 0, got {self.volume}")
        if self.high < self.low:
            raise ValueError(f"Bar.high ({self.high}) must be >= Bar.low ({self.low})")
        if self.high < self.open or self.high < self.close:
            raise ValueError(
                "Bar.high must be >= open and >= close, "
                f"got high={self.high}, open={self.open}, close={self.close}"
            )
        if self.low > self.open or self.low > self.close:
            raise ValueError(
                "Bar.low must be <= open and <= close, "
                f"got low={self.low}, open={self.open}, close={self.close}"
            )
