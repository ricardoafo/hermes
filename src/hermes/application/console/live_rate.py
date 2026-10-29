"""Live Kraken USDT/EUR quote, for the operator console only (issue #1330).

Booked money keeps using the ECB reference rate. Beside it the System tab
shows what Kraken quotes right now, so the operator sees how far the day's
reference sits from the market:

- :class:`RatePoller` decides when a poll is due and fetches the USDT/EUR
  ticker, turning every failure into a failed outcome so nothing is ever
  raised into the UI;
- :class:`RateBoard` keeps the newest good quote and says how old it is.

The quote is the middle of the best bid and ask. A failed poll keeps the
previous quote, which then ages into stale.

Hard rule: this value exists only in the console process. It is never
written to the ledger, never prices a booked figure, and never reaches the
engine, supervisor, briefs, notifications or any AI. Import-linter contract
``live-prices-are-console-only`` keeps engine, supervisor and notification code
from importing this module.

Reference: openspec/changes/1330-ticking-ages-live-rate/change.md.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.live_prices import LiveTickerPort

__all__ = [
    "RATE_EVERY",
    "RATE_STALE_AFTER",
    "LiveRate",
    "LiveTickerPort",
    "RateBoard",
    "RateOutcome",
    "RatePoller",
]

RATE_EVERY: Final = timedelta(seconds=30)
# Two missed polls: older than this the quote is shown with its age.
RATE_STALE_AFTER: Final = timedelta(seconds=90)
_PAIR: Final = Pair(Currency.USDT, Currency.EUR)
_TWO: Final = Decimal(2)


@dataclass(frozen=True, slots=True)
class RateOutcome:
    """One fetch of the USDT/EUR quote.

    Attributes:
        at: Console-clock time the fetch finished.
        eur_per_usdt: Middle of the best bid and ask; ``None`` when it failed.
        error: Name of the failure, when it failed.
    """

    at: datetime
    eur_per_usdt: Decimal | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class LiveRate:
    """What the console knows about the live USDT/EUR quote.

    Attributes:
        eur_per_usdt: Euro per one USDT.
        at: When the quote was fetched.
        age: How old it is at the time asked.
        fresh: Whether ``age`` is within ``RATE_STALE_AFTER``.
    """

    eur_per_usdt: Decimal
    at: datetime
    age: timedelta
    fresh: bool


class RatePoller:
    """Fetches the USDT/EUR ticker from a :class:`LiveTickerPort`, every ``every``.

    The console's 1 s clock tick asks :meth:`due`, calls :meth:`begin` on the UI
    thread and runs :meth:`fetch` in a worker thread.

    Args:
        port: The ticker.
        clock: The console clock; stamps the answer.
        every: Time between polls.
    """

    def __init__(
        self,
        port: LiveTickerPort,
        clock: Callable[[], datetime],
        *,
        every: timedelta = RATE_EVERY,
    ) -> None:
        """Remember the port and the cadence; no poll has started yet."""
        self._port = port
        self._clock = clock
        self._every = every
        self._started: datetime | None = None

    def due(self, now: datetime) -> bool:
        """Return whether a poll should start at ``now``."""
        return self._started is None or now - self._started >= self._every

    def begin(self, now: datetime) -> None:
        """Mark a poll as started at ``now``."""
        self._started = now

    def fetch(self) -> tuple[RateOutcome, ...]:
        """Fetch the quote once; never raises.

        Returns:
            One outcome; a failure is an outcome with no rate.
        """
        rate: Decimal | None
        try:
            ticker = self._port.get_ticker(_PAIR)
            rate, error = (ticker.best_bid + ticker.best_ask) / _TWO, None
        # Any failure (network, HTTP, schema, a bug in the fake) must stay a
        # failed outcome: the console never lets the ticker raise into the UI.
        except Exception as exc:
            rate, error = None, type(exc).__name__
        return (RateOutcome(self._clock(), rate, error),)


class RateBoard:
    """The newest good USDT/EUR quote, in memory only."""

    def __init__(self) -> None:
        """Start with no quote."""
        self._known: tuple[Decimal, datetime] | None = None

    def apply(self, outcomes: Sequence[RateOutcome]) -> None:
        """Record one poll; a failed fetch keeps the previous good quote.

        Args:
            outcomes: Outcomes from :meth:`RatePoller.fetch`.
        """
        for outcome in outcomes:
            if outcome.eur_per_usdt is not None:
                self._known = (outcome.eur_per_usdt, outcome.at)

    def quote(self, now: datetime) -> LiveRate | None:
        """Return the live quote, or ``None`` before the first good one.

        Args:
            now: The console clock.
        """
        if self._known is None:
            return None
        eur_per_usdt, at = self._known
        age = max(now - at, timedelta(0))
        return LiveRate(eur_per_usdt, at, age, fresh=age <= RATE_STALE_AFTER)
