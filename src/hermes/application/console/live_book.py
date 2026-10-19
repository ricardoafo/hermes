"""Live book health and feed latency, for the operator console only (issue #1292).

The engine rejects an entry on a stale book. Between bars the console shows
the operator, per coin, how much resting size the top of the book holds and
whether the book answer is fresh, and, in the status row, how long the
exchange takes to answer. All of it comes from one public call per coin,
polled every 10 s:

- :class:`BookPoller` decides when a poll is due and fetches the book of every
  watched coin, timing each call with the console clock and turning every
  failure into a failed outcome so nothing is ever raised into the UI;
- :class:`BookBoard` keeps the newest good answer per coin and a ring of the
  recent round-trip times, and says whether a coin's book is fresh.

The fresh/stale flag is the console's own: a book is fresh while its newest
good answer is at most ``STALE_AFTER`` old. It does not read or change the
engine's stale-book rule.

Hard rule: these values exist only in the console process. They are never
written to the ledger, never move a stop or an exit, and never reach the
supervisor, briefs, notifications or any AI. Import-linter contract
``live-prices-are-console-only`` keeps engine, supervisor and notification code
from importing this module.

Reference: openspec/changes/1292-live-book-health/change.md.
"""

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.live_prices import LiveBookPort

__all__ = [
    "BOOK_DEPTH",
    "BOOK_EVERY",
    "LATENCY_POLLS",
    "STALE_AFTER",
    "BookBoard",
    "BookHealth",
    "BookOutcome",
    "BookPoller",
    "LiveBookPort",
    "book_depth",
]

BOOK_EVERY: Final = timedelta(seconds=10)
# Two missed polls: older than this the book is shown as stale.
STALE_AFTER: Final = timedelta(seconds=20)
# Levels per side the depth figure adds up.
BOOK_DEPTH: Final = 10
# Polls kept for the latency sparkline: the last five minutes at 10 s.
LATENCY_POLLS: Final = 30
_DEFAULT_COINS: Final = ("BTC", "ETH", "SOL")
_DEFAULT_QUOTE: Final = "USDT"
_MILLISECOND: Final = timedelta(milliseconds=1)


def _notional(levels: Iterable[OrderbookLevel]) -> Decimal:
    """Return the quote-currency value resting on ``levels``."""
    return sum((level.price * level.quantity for level in levels), Decimal(0))


def book_depth(book: Orderbook) -> Decimal:
    """Return the quote-currency size an order can take from the book's thinner side.

    Args:
        book: A depth snapshot.

    Returns:
        The smaller of the bid side's and the ask side's resting value, in the
        quote currency; zero when a side is empty.
    """
    return min(_notional(book.bids), _notional(book.asks))


@dataclass(frozen=True, slots=True)
class BookOutcome:
    """One fetch of one coin's order book.

    Attributes:
        coin: Base asset.
        at: Console-clock time the fetch finished.
        round_trip: Time the call took on the console clock.
        depth: The book's depth in the quote currency; ``None`` when it failed.
        error: Name of the failure, when it failed.
    """

    coin: str
    at: datetime
    round_trip: timedelta
    depth: Decimal | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BookHealth:
    """What the console knows about one coin's book.

    Attributes:
        coin: Base asset.
        depth: Depth of the newest good book, in the quote currency.
        at: When that book was fetched.
        age: How old it is at the time asked.
        fresh: Whether ``age`` is within ``STALE_AFTER``.
    """

    coin: str
    depth: Decimal
    at: datetime
    age: timedelta
    fresh: bool


class BookPoller:
    """Fetches the order book of every watched coin from a :class:`LiveBookPort`.

    The console's 1 s clock tick asks :meth:`due`, calls :meth:`begin` on the UI
    thread and runs :meth:`fetch` in a worker thread.

    Args:
        port: The book source.
        clock: The console clock; times every call.
        coins: Base assets to fetch.
        quote: Quote asset of every pair.
        every: Time between polls.
        depth: Levels per side to ask for.
    """

    def __init__(
        self,
        port: LiveBookPort,
        clock: Callable[[], datetime],
        *,
        coins: Sequence[str] = _DEFAULT_COINS,
        quote: str = _DEFAULT_QUOTE,
        every: timedelta = BOOK_EVERY,
        depth: int = BOOK_DEPTH,
    ) -> None:
        """Remember the port and the cadence; no poll has started yet."""
        self._port = port
        self._clock = clock
        self._pairs = tuple((coin, Pair(Currency(coin), Currency(quote))) for coin in coins)
        self._every = every
        self._depth = depth
        self._started: datetime | None = None

    def due(self, now: datetime) -> bool:
        """Return whether a poll should start at ``now``."""
        return self._started is None or now - self._started >= self._every

    def begin(self, now: datetime) -> None:
        """Mark a poll as started at ``now``."""
        self._started = now

    def fetch(self) -> tuple[BookOutcome, ...]:
        """Fetch every coin once; never raises.

        Returns:
            One outcome per coin, in order; a failure is an outcome with no depth.
        """
        outcomes = []
        for coin, pair in self._pairs:
            started = self._clock()
            depth: Decimal | None
            try:
                depth = book_depth(self._port.get_orderbook(pair, depth=self._depth))
                error = None
            # Any failure (network, HTTP, schema, a bug in the fake) must stay a
            # failed outcome: the console never lets the book source raise into the UI.
            except Exception as exc:
                depth, error = None, type(exc).__name__
            finished = self._clock()
            outcomes.append(
                BookOutcome(coin, finished, max(finished - started, timedelta(0)), depth, error)
            )
        return tuple(outcomes)


class BookBoard:
    """The newest good book per coin and the recent round-trip times, in memory only.

    Args:
        ring: How many polls of round-trip times to keep.
    """

    def __init__(self, ring: int = LATENCY_POLLS) -> None:
        """Start with no book."""
        self._depth: dict[str, tuple[Decimal, datetime]] = {}
        self._round_trips: deque[Decimal] = deque(maxlen=ring)

    def apply(self, outcomes: Sequence[BookOutcome]) -> None:
        """Record one poll; a failed fetch keeps the previous good book.

        The poll's round-trip time is that of its slowest good call, so a slow
        coin shows on the sparkline; a poll with no good call adds none.

        Args:
            outcomes: Outcomes from :meth:`BookPoller.fetch`.
        """
        good = [outcome for outcome in outcomes if outcome.depth is not None]
        for outcome in good:
            if outcome.depth is not None:
                self._depth[outcome.coin] = (outcome.depth, outcome.at)
        if good:
            slowest = max(outcome.round_trip for outcome in good)
            self._round_trips.append(Decimal(slowest // _MILLISECOND))

    def health(self, coin: str, now: datetime) -> BookHealth | None:
        """Return the coin's book health, or ``None`` before its first good book.

        Args:
            coin: Base asset.
            now: The console clock.
        """
        known = self._depth.get(coin)
        if known is None:
            return None
        depth, at = known
        age = max(now - at, timedelta(0))
        return BookHealth(coin, depth, at, age, fresh=age <= STALE_AFTER)

    def round_trips(self) -> tuple[Decimal, ...]:
        """Return the recent polls' round-trip times in milliseconds, oldest first."""
        return tuple(self._round_trips)
