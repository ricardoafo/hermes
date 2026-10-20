"""Ticker value object — a spot best-bid/best-ask/last-price snapshot.

The :class:`Ticker` is the smallest cross-venue price object hermes uses:
best bid, best ask, and last-traded price for one pair at one point in
time. The backtest harness and cross-venue price sanity checks both read
this shape; the port stays deliberately minimal so venues that publish
more fields (24h volume, 24h high/low, ...) can drop them at the
adapter boundary without leaking wire-specific structure into domain
code.

Domain purity: this module imports only value_objects and stdlib. No
pydantic, no httpx (enforced by import-linter contracts).

Reference: code-conventions.md §1 (Value Object) and §9 (UTC internally).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from hermes.domain.value_objects.pair import Pair

__all__ = ["Ticker"]


@dataclass(frozen=True, slots=True)
class Ticker:
    """Immutable spot BBO + last-trade snapshot for one pair.

    Attributes:
        pair: The trading pair.
        best_bid: The current best (highest) bid price in quote currency.
            Must be > 0.
        best_ask: The current best (lowest) ask price in quote currency.
            Must be > 0 and strictly > ``best_bid`` (an uncrossed book).
        last_price: The most-recent trade price. Must be > 0. May sit
            outside the current bid/ask range briefly if the book moved
            after the trade — no cross-check is enforced.
        snapshot_time: UTC-aware :class:`~datetime.datetime` when the
            snapshot was captured (adapter-side clock).
    """

    pair: Pair
    best_bid: Decimal
    best_ask: Decimal
    last_price: Decimal
    snapshot_time: datetime

    def __post_init__(self) -> None:
        """Enforce positive prices, uncrossed book, and UTC-aware timestamp.

        Raises:
            ValueError: On naive / non-UTC timestamp, non-positive
                ``best_bid`` / ``best_ask`` / ``last_price``, or a crossed
                book (``best_bid >= best_ask``).
        """
        if self.snapshot_time.tzinfo is None or self.snapshot_time.tzinfo.utcoffset(
            None
        ) != UTC.utcoffset(None):
            raise ValueError(f"Ticker.snapshot_time must be UTC-aware, got {self.snapshot_time!r}")
        for name, value in (
            ("best_bid", self.best_bid),
            ("best_ask", self.best_ask),
            ("last_price", self.last_price),
        ):
            if value <= Decimal("0"):
                raise ValueError(f"Ticker.{name} must be > 0, got {value}")
        if self.best_bid >= self.best_ask:
            raise ValueError(
                "Ticker is crossed: best_bid "
                f"{self.best_bid} >= best_ask {self.best_ask} (pair={self.pair})"
            )
