"""Live bar context between bars, for the operator console only (issue #1289).

The engine acts on closed 4h bars. Between them the Market panel shows, under
each coin, the 4h bar being built right now and the last 24 hours (design spec
"Live between bars", issue #722 items 1 and 2). Both come from the same call:
the hourly bars of the coin, polled every minute.

- :class:`BarsPoller` decides when a poll is due and fetches the hourly bars of
  every watched coin, turning every failure into a failed outcome so nothing is
  ever raised into the UI;
- :func:`bar_context` builds a :class:`BarContext` from hourly bars: the 4h bar
  is the hourly bars since the last 4h boundary, the 24 h window starts at the
  hourly bar that opened 24 h before the current hour;
- :class:`ContextBoard` keeps the last good context per coin and drops it once
  it is older than ``STALE_AFTER``.

A context holds no live price. The caller passes the live price when it asks
for the forming bar or the 24 h change, so the line always agrees with the
price column beside it.

Hard rule: these values exist only in the console process. They are never
written to the ledger, never move a stop or an exit, and never reach the
supervisor, briefs, notifications or any AI. Import-linter contract
``live-prices-are-console-only`` keeps engine, supervisor and notification code
from importing this module.

Reference: openspec/changes/1289-live-bar-context/change.md.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from hermes.domain.market_data.bar_schedule import previous_boundary
from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.live_prices import LiveBarsPort

__all__ = [
    "BAR",
    "CONTEXT_EVERY",
    "DAY",
    "STALE_AFTER",
    "BarContext",
    "BarsOutcome",
    "BarsPoller",
    "ContextBoard",
    "DayContext",
    "FormingBar",
    "LiveBarsPort",
    "bar_context",
]

BAR: Final = timedelta(hours=4)
DAY: Final = timedelta(hours=24)
CONTEXT_EVERY: Final = timedelta(seconds=60)
# Five missed polls: older than this the context is hidden rather than shown as current.
STALE_AFTER: Final = timedelta(minutes=5)
_HOUR: Final = timedelta(hours=1)
_HUNDRED: Final = Decimal(100)
_DEFAULT_COINS: Final = ("BTC", "ETH", "SOL")
_DEFAULT_QUOTE: Final = "USDT"


@dataclass(frozen=True, slots=True)
class FormingBar:
    """The 4h bar being built right now, at one live price.

    Attributes:
        open: First price of the bar.
        high: Highest price so far, counting the live price.
        low: Lowest price so far, counting the live price.
        last: The live price.
        closes_at: When the bar closes.
    """

    open: Decimal
    high: Decimal
    low: Decimal
    last: Decimal
    closes_at: datetime

    def left(self, now: datetime) -> timedelta:
        """Return the time left in the bar at ``now`` (never negative)."""
        return max(self.closes_at - now, timedelta(0))


@dataclass(frozen=True, slots=True)
class DayContext:
    """The last 24 hours of one coin, at one live price.

    Attributes:
        change_pct: Change from the 24 h opening price to the live price, percent.
        high: Highest price in the window, counting the live price.
        low: Lowest price in the window, counting the live price.
        volume: Base-asset volume traded in the window.
    """

    change_pct: Decimal
    high: Decimal
    low: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class BarContext:
    """What the hourly bars of one coin say, before any live price.

    Attributes:
        coin: Base asset.
        at: Console-clock time of the fetch.
        bar_opens_at: When the 4h bar the numbers belong to opened.
        bar_open: First price of that 4h bar; ``None`` when its first hourly bar is missing.
        bar_high: Highest price in it so far.
        bar_low: Lowest price in it so far.
        day_open: First price of the 24 h window; ``None`` when its first hourly bar is missing.
        day_high: Highest price in the window.
        day_low: Lowest price in the window.
        day_volume: Base-asset volume in the window.
    """

    coin: str
    at: datetime
    bar_opens_at: datetime
    bar_open: Decimal | None
    bar_high: Decimal
    bar_low: Decimal
    day_open: Decimal | None
    day_high: Decimal
    day_low: Decimal
    day_volume: Decimal

    def forming(self, last: Decimal, now: datetime) -> FormingBar | None:
        """Return the forming 4h bar at the live price ``last``.

        Args:
            last: The live price.
            now: The console clock.

        Returns:
            ``None`` when the bar's first hourly bar is missing, or when ``now``
            is past the close of the bar these numbers belong to (the next poll
            brings the new bar).
        """
        closes_at = self.bar_opens_at + BAR
        if self.bar_open is None or now >= closes_at:
            return None
        return FormingBar(
            open=self.bar_open,
            high=max(self.bar_high, last),
            low=min(self.bar_low, last),
            last=last,
            closes_at=closes_at,
        )

    def day(self, last: Decimal) -> DayContext | None:
        """Return the 24 h context at the live price ``last``.

        Args:
            last: The live price.

        Returns:
            ``None`` when the window's first hourly bar is missing.
        """
        if self.day_open is None:
            return None
        return DayContext(
            change_pct=(last / self.day_open - 1) * _HUNDRED,
            high=max(self.day_high, last),
            low=min(self.day_low, last),
            volume=self.day_volume,
        )


def bar_context(coin: str, klines: Sequence[Kline], now: datetime) -> BarContext | None:
    """Build a coin's bar context from its hourly bars.

    Args:
        coin: Base asset.
        klines: Hourly bars of the coin, any order, covering at least the
            24 h window and the current hour.
        now: The console clock (UTC-aware).

    Returns:
        The context, or ``None`` when no hourly bar falls in the 24 h window.
    """
    bar_start = previous_boundary(now, KlineInterval.H4)
    day_start = previous_boundary(now, KlineInterval.H1) - DAY
    in_day = sorted(
        (k for k in klines if day_start <= k.open_time <= now), key=lambda k: k.open_time
    )
    if not in_day:
        return None
    in_bar = [k for k in in_day if k.open_time >= bar_start]
    return BarContext(
        coin=coin,
        at=now,
        bar_opens_at=bar_start,
        bar_open=_opening(in_bar, bar_start),
        bar_high=max((k.high.amount for k in in_bar), default=in_day[-1].high.amount),
        bar_low=min((k.low.amount for k in in_bar), default=in_day[-1].low.amount),
        day_open=_opening(in_day, day_start),
        day_high=max(k.high.amount for k in in_day),
        day_low=min(k.low.amount for k in in_day),
        day_volume=sum((k.volume.amount for k in in_day), Decimal(0)),
    )


def _opening(klines: Sequence[Kline], start: datetime) -> Decimal | None:
    """Return the opening price of the window, or ``None`` when its first hour is missing."""
    if klines and klines[0].open_time == start:
        return klines[0].open.amount
    return None


@dataclass(frozen=True, slots=True)
class BarsOutcome:
    """One fetch of one coin's hourly bars.

    Attributes:
        coin: Base asset.
        at: Console-clock time of the fetch.
        context: The coin's bar context; ``None`` when the fetch failed or
            returned no usable bar.
        error: Name of the failure, when it failed.
    """

    coin: str
    at: datetime
    context: BarContext | None
    error: str | None = None


class BarsPoller:
    """Fetches the hourly bars of every watched coin from a :class:`LiveBarsPort`.

    The console's 1 s clock tick asks :meth:`due`, calls :meth:`begin` on the UI
    thread and runs :meth:`fetch` in a worker thread.

    Args:
        port: The bars source.
        clock: The console clock; stamps every fetch and sets the 24 h window.
        coins: Base assets to fetch.
        quote: Quote asset of every pair.
        every: Time between polls.
    """

    def __init__(
        self,
        port: LiveBarsPort,
        clock: Callable[[], datetime],
        *,
        coins: Sequence[str] = _DEFAULT_COINS,
        quote: str = _DEFAULT_QUOTE,
        every: timedelta = CONTEXT_EVERY,
    ) -> None:
        """Remember the port and the cadence; no poll has started yet."""
        self._port = port
        self._clock = clock
        self._pairs = tuple((coin, Pair(Currency(coin), Currency(quote))) for coin in coins)
        self._every = every
        self._started: datetime | None = None

    def due(self, now: datetime) -> bool:
        """Return whether a poll should start at ``now``."""
        return self._started is None or now - self._started >= self._every

    def begin(self, now: datetime) -> None:
        """Mark a poll as started at ``now``."""
        self._started = now

    def fetch(self) -> tuple[BarsOutcome, ...]:
        """Fetch every coin once; never raises.

        Returns:
            One outcome per coin, in order; a failure is an outcome with no context.
        """
        outcomes = []
        for coin, pair in self._pairs:
            now = self._clock()
            since = previous_boundary(now, KlineInterval.H1) - DAY
            try:
                klines = list(
                    self._port.fetch_klines(
                        pair=pair,
                        interval=KlineInterval.H1,
                        since=since,
                        until=previous_boundary(now, KlineInterval.H1) + _HOUR,
                    )
                )
                outcomes.append(BarsOutcome(coin, now, bar_context(coin, klines, now)))
            # Any failure (network, HTTP, schema, a bug in the fake) must stay a
            # failed outcome: the console never lets the bars source raise into the UI.
            except Exception as exc:
                outcomes.append(BarsOutcome(coin, now, None, type(exc).__name__))
        return tuple(outcomes)


class ContextBoard:
    """The last good bar context per coin, kept in memory only."""

    def __init__(self) -> None:
        """Start with no context."""
        self._contexts: dict[str, BarContext] = {}

    def apply(self, outcomes: Iterable[BarsOutcome]) -> None:
        """Record fetched contexts; a failed fetch keeps the previous good one.

        Args:
            outcomes: Outcomes from :meth:`BarsPoller.fetch`.
        """
        for outcome in outcomes:
            if outcome.context is not None:
                self._contexts[outcome.coin] = outcome.context

    def get(self, coin: str, now: datetime) -> BarContext | None:
        """Return the coin's context, or ``None`` without one or when it is stale.

        Args:
            coin: Base asset.
            now: The console clock.
        """
        context = self._contexts.get(coin)
        if context is None or now - context.at > STALE_AFTER:
            return None
        return context
