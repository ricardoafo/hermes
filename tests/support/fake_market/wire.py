"""Render synthetic market data as the vendors' JSON.

The shapes follow what production receives and what the recorded fixtures in
``tests/fixtures/binance_public/`` and ``tests/fixtures/frankfurter/`` hold:

- Binance ``GET /api/v3/klines``: a list of 12-element rows, prices and volumes
  as strings with eight decimals, times as integer milliseconds, and an
  inclusive ``close_time`` of ``open + interval - 1 ms``.
- Binance ``GET /api/v3/time``: ``{"serverTime": <ms>}``.
- Binance ``GET /api/v3/depth``: ``{"lastUpdateId", "bids", "asks"}`` with
  ``[price, quantity]`` string pairs, best level first.
- Frankfurter ``GET /<date>``: ``{"amount", "base", "date", "rates"}``.

:mod:`tests.support.fake_market.schema` holds the checks both the fixtures and
these renderings must pass.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from hermes.domain.market_data.bar import Bar

_EIGHT = Decimal("0.00000001")
_TRADES_PER_BAR = 1000


def ms(at: datetime) -> int:
    """Return ``at`` as integer epoch milliseconds."""
    return int(at.astimezone(UTC).timestamp() * 1000)


def decimal_text(value: Decimal) -> str:
    """Return ``value`` the way Binance prints prices: eight fixed decimals."""
    return f"{value.quantize(_EIGHT):f}"


def kline_row(bar: Bar) -> list[object]:
    """Return ``bar`` as one Binance klines row."""
    quote_volume = bar.volume * bar.close
    return [
        ms(bar.open_time),
        decimal_text(bar.open),
        decimal_text(bar.high),
        decimal_text(bar.low),
        decimal_text(bar.close),
        decimal_text(bar.volume),
        ms(bar.close_time),
        decimal_text(quote_volume),
        _TRADES_PER_BAR,
        decimal_text(bar.volume / 2),
        decimal_text(quote_volume / 2),
        "0",
    ]


def server_time(now: datetime) -> dict[str, object]:
    """Return the body of ``GET /api/v3/time`` at ``now``."""
    return {"serverTime": ms(now)}


def depth(
    mid: Decimal, *, levels: int, update_id: int, step_bps: Decimal = Decimal(1)
) -> dict[str, object]:
    """Return a depth snapshot around ``mid``: ``levels`` per side, best first.

    Each level is ``step_bps`` further from the mid than the previous one and
    holds a little more size, as a liquid book does.
    """
    step = mid * step_bps / Decimal(10_000)
    bids = [
        [decimal_text(mid - step * (i + 1)), decimal_text(Decimal("0.5") * (i + 1))]
        for i in range(levels)
    ]
    asks = [
        [decimal_text(mid + step * (i + 1)), decimal_text(Decimal("0.5") * (i + 1))]
        for i in range(levels)
    ]
    return {"lastUpdateId": update_id, "bids": bids, "asks": asks}


def fx_rate(base: str, quote: str, on: date, rate: Decimal) -> dict[str, object]:
    """Return the body of Frankfurter's ``GET /<date>?from=<base>&to=<quote>``."""
    return {"amount": 1.0, "base": base, "date": on.isoformat(), "rates": {quote: float(rate)}}
