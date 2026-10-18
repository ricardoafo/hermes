"""Live prices between bars, for the operator console only (issue #713).

The engine acts on closed bars. Between them, the console asks a public ticker
for the price of each watched coin every 10 s and shows the operator what the
open positions and the portfolio are worth at that price (design spec "Live
between bars"). This module holds the pure parts:

- :class:`TickerPoller` decides when a poll is due (the cadence follows the
  console clock) and fetches one tick per coin, turning every failure into a
  failed tick so nothing is ever raised into the UI;
- :class:`LiveBoard` keeps the ticks in memory: the last good price per coin,
  a ring buffer of recent prices, the time of a failure, and whether the live
  layer is waiting, live or paused (no good tick for more than 60 s);
- :func:`live_position` and :func:`live_total` value a position and the
  portfolio at live prices. A live figure uses the live price for every price
  term; a live total exists only when every open position has a live price, so
  no figure is ever half live and half booked.

Hard rule: live values exist only in the console process. They are never
written to the ledger, never move a stop or an exit, and never reach the
supervisor, briefs, notifications or any AI. Import-linter contract
``live-prices-are-console-only`` keeps engine, supervisor and notification code
from importing this module; the shared renderers in ``render.py`` never import
it either.

Reference: openspec/changes/713-live-ticker/change.md (D-2 to D-6).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

from hermes.application.console.format import stop_room_share
from hermes.application.console.views import PortfolioSummary, PositionDetail, PositionRow
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.live_prices import LiveTickerPort

__all__ = [
    "LIVE_COINS",
    "LIVE_QUOTE",
    "RING_TICKS",
    "STALE_AFTER",
    "TICK_EVERY",
    "LiveBoard",
    "LivePosition",
    "LiveQuote",
    "LiveState",
    "LiveStatus",
    "LiveTickerPort",
    "LiveTotal",
    "TickOutcome",
    "TickerPoller",
    "live_position",
    "live_total",
    "poll_succeeded",
    "spread_bps",
]

TICK_EVERY: Final = timedelta(seconds=10)
STALE_AFTER: Final = timedelta(seconds=60)
# 90 ticks of 10 s: the last 15 minutes, the Market panel's sparkline window.
RING_TICKS: Final = 90
LIVE_COINS: Final = ("BTC", "ETH", "SOL")
LIVE_QUOTE: Final = "USDT"
_ZERO: Final = Decimal(0)
_HUNDRED: Final = Decimal(100)
_BPS: Final = Decimal(10_000)


@dataclass(frozen=True, slots=True)
class TickOutcome:
    """One fetch of one coin's price.

    Attributes:
        coin: Base asset (``"BTC"``).
        at: Console-clock time of the fetch.
        price: Last traded price in the quote currency; ``None`` when it failed.
        error: Name of the failure, when it failed.
        spread_bps: Best ask over best bid in basis points of the mid price;
            ``None`` when the fetch failed or the source gave no book.
    """

    coin: str
    at: datetime
    price: Decimal | None
    error: str | None = None
    spread_bps: Decimal | None = None


def spread_bps(best_bid: Decimal, best_ask: Decimal) -> Decimal:
    """Return the gap between the best ask and the best bid in basis points of the mid.

    Args:
        best_bid: Highest bid; must be positive.
        best_ask: Lowest ask; must be positive.

    Returns:
        ``(ask - bid) / mid * 10000``; negative or zero only for a crossed or
        locked book, which the ticker's own checks reject.
    """
    return (best_ask - best_bid) / ((best_ask + best_bid) / 2) * _BPS


def poll_succeeded(outcomes: Sequence[TickOutcome]) -> bool:
    """Return whether one ticker poll priced every coin it asked for.

    The status row's feed pulse draws one glyph per poll (issue #713);
    a poll with any failed coin counts as failed, so a half-working feed
    never looks healthy.

    Args:
        outcomes: One poll's ticks, one per coin.

    Returns:
        ``True`` when there is at least one tick and every tick has a price.
    """
    return bool(outcomes) and all(outcome.price is not None for outcome in outcomes)


@dataclass(frozen=True, slots=True)
class LiveQuote:
    """What the console knows about one coin's live price.

    Attributes:
        coin: Base asset.
        price: Last good price in the quote currency.
        at: When that price was fetched.
        previous: The good price before it, if any.
        history: The kept good prices, oldest first (at most ``RING_TICKS``).
        failed_at: When the newest fetch failed; ``None`` when it succeeded.
        spread_bps: The spread of that newest good tick in basis points;
            ``None`` when it had none.
    """

    coin: str
    price: Decimal
    at: datetime
    previous: Decimal | None
    history: tuple[Decimal, ...]
    failed_at: datetime | None = None
    spread_bps: Decimal | None = None

    @property
    def change_pct(self) -> Decimal:
        """Return the change from the oldest kept price to the newest, percent."""
        first = self.history[0] if self.history else self.price
        return (self.price / first - 1) * _HUNDRED


class LiveState(StrEnum):
    """Whether the live layer has a fresh price."""

    WAITING = "waiting"
    LIVE = "live"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class LiveStatus:
    """The live layer's state at one moment.

    Attributes:
        state: WAITING before the first good tick, LIVE while the newest good
            tick is at most ``STALE_AFTER`` old, PAUSED after that.
        last_tick_at: When the newest good tick was fetched, if any.
    """

    state: LiveState
    last_tick_at: datetime | None = None


class LiveBoard:
    """The ticks the console has received, kept in memory only.

    Args:
        ring: How many good prices to keep per coin.
    """

    def __init__(self, ring: int = RING_TICKS) -> None:
        """Start with no tick."""
        self._ring = ring
        self._history: dict[str, deque[Decimal]] = {}
        self._at: dict[str, datetime] = {}
        self._failed: dict[str, datetime] = {}
        self._spread: dict[str, Decimal] = {}
        self._last_good: datetime | None = None

    def apply(self, outcomes: Iterable[TickOutcome]) -> dict[str, int]:
        """Record fetched ticks.

        Args:
            outcomes: Ticks from :meth:`TickerPoller.fetch`.

        Returns:
            The coins whose price moved, ``1`` up and ``-1`` down (a coin's
            first price and an unchanged price are not listed).
        """
        moved: dict[str, int] = {}
        for outcome in outcomes:
            if outcome.price is None:
                if outcome.coin in self._history:
                    self._failed[outcome.coin] = outcome.at
                continue
            history = self._history.setdefault(outcome.coin, deque(maxlen=self._ring))
            if history and outcome.price != history[-1]:
                moved[outcome.coin] = 1 if outcome.price > history[-1] else -1
            history.append(outcome.price)
            self._at[outcome.coin] = outcome.at
            self._failed.pop(outcome.coin, None)
            if outcome.spread_bps is None:
                self._spread.pop(outcome.coin, None)
            else:
                self._spread[outcome.coin] = outcome.spread_bps
            if self._last_good is None or outcome.at > self._last_good:
                self._last_good = outcome.at
        return moved

    def quote(self, coin: str) -> LiveQuote | None:
        """Return the coin's live quote, or ``None`` before its first good tick."""
        history = self._history.get(coin)
        if not history:
            return None
        return LiveQuote(
            coin=coin,
            price=history[-1],
            at=self._at[coin],
            previous=history[-2] if len(history) > 1 else None,
            history=tuple(history),
            failed_at=self._failed.get(coin),
            spread_bps=self._spread.get(coin),
        )

    def prices(self) -> dict[str, Decimal]:
        """Return the last good price of every coin that has one."""
        return {coin: history[-1] for coin, history in self._history.items() if history}

    def status(self, now: datetime) -> LiveStatus:
        """Return whether the live layer is waiting, live or paused at ``now``."""
        last = self._last_good
        if last is None:
            return LiveStatus(LiveState.WAITING)
        state = LiveState.PAUSED if now - last > STALE_AFTER else LiveState.LIVE
        return LiveStatus(state, last)


class TickerPoller:
    """Fetches one tick per coin from a :class:`LiveTickerPort`, every ``every``.

    The console's 1 s clock tick asks :meth:`due`, calls :meth:`begin` on the UI
    thread and runs :meth:`fetch` in a worker thread.

    Args:
        port: The ticker.
        clock: The console clock; stamps every tick.
        coins: Base assets to fetch.
        quote: Quote asset of every pair.
        every: Time between polls.
    """

    def __init__(
        self,
        port: LiveTickerPort,
        clock: Callable[[], datetime],
        *,
        coins: Sequence[str] = LIVE_COINS,
        quote: str = LIVE_QUOTE,
        every: timedelta = TICK_EVERY,
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

    def fetch(self) -> tuple[TickOutcome, ...]:
        """Fetch every coin once; never raises.

        Returns:
            One outcome per coin, in order; a failure is an outcome with no price.
        """
        outcomes = []
        for coin, pair in self._pairs:
            try:
                ticker = self._port.get_ticker(pair)
                price: Decimal | None = ticker.last_price
                spread = spread_bps(ticker.best_bid, ticker.best_ask)
                error = None
            # Any failure (network, HTTP, schema, a bug in the fake) must stay a
            # failed tick: the console never lets the ticker raise into the UI.
            except Exception as exc:
                price, spread, error = None, None, type(exc).__name__
            outcomes.append(
                TickOutcome(
                    coin=coin, at=self._clock(), price=price, error=error, spread_bps=spread
                )
            )
        return tuple(outcomes)


@dataclass(frozen=True, slots=True)
class LivePosition:
    """One open position valued at a live price.

    Attributes:
        coin: Base asset.
        price: Live price in the quote currency.
        price_eur: Live price in EUR at the row's rate; ``None`` while EUR is pending.
        change_pct: Change since entry at the live price, percent.
        gross: Unrealised gross P&L at the live price, EUR; ``None`` while EUR is pending.
        net: Gross less fees paid and fees to close at the live price, EUR;
            ``None`` without the detail (the fees) or while EUR is pending.
        high: Highest price since entry, counting the live price.
        stop_room: Share of the room from the stop up to ``high`` left at the live price.
        stop_distance_pct: Distance from the live price to the stop, percent.
        stop_distance_atr: The same distance in ATR; ``None`` when the ATR is unknown.
    """

    coin: str
    price: Decimal
    price_eur: Decimal | None
    change_pct: Decimal
    gross: Decimal | None
    net: Decimal | None
    high: Decimal
    stop_room: Decimal
    stop_distance_pct: Decimal
    stop_distance_atr: Decimal | None


def live_position(row: PositionRow, detail: PositionDetail | None, price: Decimal) -> LivePosition:
    """Return a long position valued at ``price``.

    Every price term uses ``price``; the booked facts supply only what the price
    does not change (size, entry, fees paid, the ATR). Fees to close scale with
    the price, as in ``position_figures.build_detail``. Tax is not part of it:
    tax figures stay booked-only.

    Args:
        row: The booked list row.
        detail: The booked detail (its fees), when the model has it.
        price: Live price in the quote currency.

    Returns:
        The live figures.
    """
    cushion = price - row.stop_price
    high = max(row.high_since_entry, price)
    booked_cushion = row.current_price - row.stop_price
    atr = booked_cushion / row.stop_distance_atr if row.stop_distance_atr else None
    price_eur: Decimal | None = None
    gross: Decimal | None = None
    net: Decimal | None = None
    if row.eur_pending:
        change = (price / row.entry_price - 1) * _HUNDRED
    else:
        price_eur = price * row.eur_per_usdt
        change = (price_eur / row.entry_eur - 1) * _HUNDRED
        if detail is None:
            gross = row.size * (price_eur - row.entry_eur)
        else:
            gross = detail.gross + row.size * (price - row.current_price) * row.eur_per_usdt
            to_close = detail.fees_to_close * price / row.current_price
            net = gross - detail.fees_paid - to_close
    return LivePosition(
        coin=row.coin,
        price=price,
        price_eur=price_eur,
        change_pct=change,
        gross=gross,
        net=net,
        high=high,
        stop_room=stop_room_share(row.stop_price, price, high),
        stop_distance_pct=cushion * _HUNDRED / price,
        stop_distance_atr=cushion / atr if atr else None,
    )


@dataclass(frozen=True, slots=True)
class LiveTotal:
    """The portfolio valued at live prices.

    Attributes:
        total: Cash plus every open position at its live price, EUR.
        change: The range's change with the open positions at live prices, EUR.
        change_pct: That change as a percentage of the range's starting value.
    """

    total: Decimal
    change: Decimal
    change_pct: Decimal


def live_total(
    summary: PortfolioSummary, rows: Sequence[PositionRow], prices: Mapping[str, Decimal]
) -> LiveTotal | None:
    """Return the portfolio at live prices, or ``None`` when it cannot be wholly live.

    Args:
        summary: The booked portfolio view.
        rows: The booked open positions.
        prices: Live price per coin, in the quote currency.

    Returns:
        The live total and range change; ``None`` with no open position, while
        EUR is pending, or when any open position has no live price.
    """
    if not rows or summary.eur_pending:
        return None
    if any(row.eur_pending or row.coin not in prices for row in rows):
        return None
    delta = sum(
        (row.size * (prices[row.coin] - row.current_price) * row.eur_per_usdt for row in rows),
        _ZERO,
    )
    change = summary.change + delta
    start = summary.total_value - summary.change
    pct = change * _HUNDRED / start if start > 0 else _ZERO
    return LiveTotal(total=summary.total_value + delta, change=change, change_pct=pct)
