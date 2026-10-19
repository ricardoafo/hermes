"""The console's slow public feeds: bars, order books and the EUR quote (#1289, #1292, #1330).

The ticker polls prices every 10 s. Three more public calls feed the Market panel,
the status row and the System tab: the hourly bars of each coin every 60 s (forming
4h bar and 24 h context), the order book of each coin every 10 s (depth, the
fresh/stale flag and the round-trip time) and the USDT/EUR quote every 30 s.
:class:`ContextFeeds` owns the pollers and their boards, starts a worker when a
poll is due and hands the answer to the UI thread.

Display only: nothing here is written anywhere, and no engine, supervisor or
notification code imports this module (import-linter contract
``live-prices-are-console-only``).
"""

from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import Any, Protocol

from hermes.application.console.live_book import (
    BookBoard,
    BookHealth,
    BookPoller,
    LiveBookPort,
)
from hermes.application.console.live_context import (
    BarContext,
    BarsPoller,
    ContextBoard,
    LiveBarsPort,
)
from hermes.application.console.live_rate import (
    LiveRate,
    LiveTickerPort,
    RateBoard,
    RatePoller,
)

__all__ = ["ContextFeeds"]


class _Host(Protocol):
    """The two methods of a Textual app the feeds call."""

    @property
    def run_worker(self) -> Callable[..., object]: ...

    def call_from_thread(self, callback: Callable[..., None], *args: Any) -> object: ...


class _Poller[Outcome](Protocol):
    def due(self, now: datetime) -> bool: ...

    def begin(self, now: datetime) -> None: ...

    def fetch(self) -> Sequence[Outcome]: ...


class _Feed[Outcome]:
    """One poller, its worker and the record of what it fetched."""

    def __init__(
        self,
        name: str,
        poller: _Poller[Outcome] | None,
        record: Callable[[Sequence[Outcome]], None],
    ) -> None:
        """Remember the poller (``None`` when the source cannot give this feed)."""
        self._name = name
        self._poller = poller
        self._record = record
        self._busy = False

    def start(self, app: _Host, now: datetime, repaint: Callable[[], None]) -> None:
        """Start a worker thread when a poll is due and none is running."""
        poller = self._poller
        if poller is None or self._busy or not poller.due(now):
            return
        poller.begin(now)
        self._busy = True
        app.run_worker(
            partial(self._run, app, poller, repaint),
            name=self._name,
            group=self._name,
            thread=True,
            exit_on_error=False,
        )

    def _run(self, app: _Host, poller: _Poller[Outcome], repaint: Callable[[], None]) -> None:
        outcomes = poller.fetch()
        # The app may be closing while the fetch ran; its answer is then dropped.
        with suppress(RuntimeError):
            app.call_from_thread(self._apply, outcomes, repaint)

    def _apply(self, outcomes: Sequence[Outcome], repaint: Callable[[], None]) -> None:
        self._busy = False
        self._record(outcomes)
        repaint()


class ContextFeeds:
    """The bars, book and EUR-quote feeds of one console.

    Args:
        source: The ticker the console was given; a feed starts only when it
            also satisfies that feed's port.
        clock: The console clock.
    """

    def __init__(self, source: object | None, clock: Callable[[], datetime]) -> None:
        """Build the feeds the source can give; the others stay off."""
        self._contexts = ContextBoard()
        self._books = BookBoard()
        self._rates = RateBoard()
        self._bars = _Feed(
            "bars",
            BarsPoller(source, clock) if isinstance(source, LiveBarsPort) else None,
            self._contexts.apply,
        )
        self._book = _Feed(
            "books",
            BookPoller(source, clock) if isinstance(source, LiveBookPort) else None,
            self._books.apply,
        )
        self._rate = _Feed(
            "rate",
            RatePoller(source, clock) if isinstance(source, LiveTickerPort) else None,
            self._rates.apply,
        )

    def start(self, app: _Host, now: datetime, repaint: Callable[[], None]) -> None:
        """Start the polls that are due; ``repaint`` runs on the UI thread after each answer."""
        self._bars.start(app, now, repaint)
        self._book.start(app, now, repaint)
        self._rate.start(app, now, repaint)

    def contexts(self, coins: Iterable[str], now: datetime) -> dict[str, BarContext]:
        """Return the fresh bar context of every coin that has one."""
        found = {coin: self._contexts.get(coin, now) for coin in coins}
        return {coin: context for coin, context in found.items() if context is not None}

    def healths(self, coins: Iterable[str], now: datetime) -> dict[str, BookHealth]:
        """Return the book health of every coin that has a good book."""
        found = {coin: self._books.health(coin, now) for coin in coins}
        return {coin: health for coin, health in found.items() if health is not None}

    def round_trips(self) -> tuple[Decimal, ...]:
        """Return the recent book-call round trips in milliseconds, oldest first."""
        return self._books.round_trips()

    def rate(self, now: datetime) -> LiveRate | None:
        """Return the live USDT/EUR quote, or ``None`` before the first good one."""
        return self._rates.quote(now)
