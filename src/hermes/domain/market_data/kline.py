"""Kline (OHLCV candle) domain value object + interval enum.

A :class:`Kline` is one candle of open/high/low/close/volume data for a
:class:`~hermes.domain.value_objects.pair.Pair` over a fixed
:class:`KlineInterval` window. All timestamps are timezone-aware UTC and
all monetary/quantity fields are :class:`~decimal.Decimal`-precise via the
existing :class:`~hermes.domain.value_objects.price.Price` and
:class:`~hermes.domain.value_objects.quantity.Quantity` value objects.

Domain purity: this module imports only domain value objects and stdlib.
No pydantic, no httpx, no asyncio (enforced by ``import-linter`` contract
``domain-no-async-or-network``).

Reference: code-conventions.md §1 (Value Object) and §9 (UTC internally).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity

__all__ = ["Kline", "KlineInterval"]


class KlineInterval(StrEnum):
    """Discrete candle-duration values supported by hermes.

    Values match the Binance interval strings verbatim so a
    :class:`KlineInterval` can be passed to the Binance klines endpoint
    without translation. Extend deliberately — every new interval crosses
    a domain boundary and a storage schema check.

    Reference: code-conventions.md §1 (Value Object).
    """

    M1 = "1m"
    M5 = "5m"
    H1 = "1h"
    D1 = "1d"


@dataclass(frozen=True, slots=True)
class Kline:
    """Immutable OHLCV candle for one ``(pair, interval, open_time)`` tuple.

    Every field is deterministic given the tuple ``(pair, interval,
    open_time)`` — two Klines with the same identity carry the same
    OHLCV values. This is the invariant the deduplication policy in the
    kline store relies on (``INSERT OR IGNORE`` on the primary key).

    Attributes:
        pair: Trading pair the candle belongs to (e.g. ``BTC/USDT``). The
            embedded :class:`Price` fields carry their own pair too; they
            MUST match this pair (validated in :meth:`__post_init__`).
        interval: The candle-duration bucket (:class:`KlineInterval`).
        open_time: First tick of the window, timezone-aware UTC. The
            constructor rejects naive datetimes (per code-conventions §9).
        open: Price at ``open_time``.
        high: Maximum trade price observed within the window.
        low: Minimum trade price observed within the window.
        close: Price at ``close_time``.
        volume: Total base-asset quantity traded within the window.
        close_time: Last tick of the window, timezone-aware UTC. Must be
            strictly after ``open_time``.

    Reference: code-conventions.md §1.
    """

    pair: Pair
    interval: KlineInterval
    open_time: datetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Quantity
    close_time: datetime

    def __post_init__(self) -> None:
        """Enforce tz-aware UTC timestamps, price pair coherence, and OHLC bounds.

        Raises:
            ValueError: on naive datetime, non-UTC datetime, mismatched
                :class:`Price` pair, ``close_time <= open_time``, or
                inconsistent high/low bounds.
        """
        if self.open_time.tzinfo is None or self.open_time.tzinfo.utcoffset(None) != UTC.utcoffset(
            None
        ):
            raise ValueError(f"Kline.open_time must be UTC-aware, got {self.open_time!r}")
        if self.close_time.tzinfo is None or self.close_time.tzinfo.utcoffset(
            None
        ) != UTC.utcoffset(None):
            raise ValueError(f"Kline.close_time must be UTC-aware, got {self.close_time!r}")
        if self.close_time <= self.open_time:
            raise ValueError(
                f"Kline.close_time ({self.close_time!r}) must be strictly after "
                f"Kline.open_time ({self.open_time!r})"
            )
        for field_name, price in (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
        ):
            if price.pair != self.pair:
                raise ValueError(
                    f"Kline.{field_name}.pair ({price.pair!r}) does not match "
                    f"Kline.pair ({self.pair!r})"
                )
        if self.volume.asset != self.pair.base:
            raise ValueError(
                f"Kline.volume.asset ({self.volume.asset!r}) must equal the pair "
                f"base ({self.pair.base!r})"
            )
        if self.high.amount < self.low.amount:
            raise ValueError(
                f"Kline.high ({self.high.amount}) must be >= Kline.low ({self.low.amount})"
            )
        if self.high.amount < self.open.amount or self.high.amount < self.close.amount:
            raise ValueError(
                "Kline.high must be >= open and >= close, "
                f"got high={self.high.amount}, open={self.open.amount}, "
                f"close={self.close.amount}"
            )
        if self.low.amount > self.open.amount or self.low.amount > self.close.amount:
            raise ValueError(
                "Kline.low must be <= open and <= close, "
                f"got low={self.low.amount}, open={self.open.amount}, "
                f"close={self.close.amount}"
            )
