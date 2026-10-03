"""Builders and fakes shared by the serve bar-loop tests.

The scripted market is one pair (BTC/USDT) with twelve rising 1d bars and a
run of 4h bars: eleven flat bars (0-10), a breakout to 2000 that satisfies all
three entry conditions (11), two flat bars (12, 13) and a crash whose low is
far below any Chandelier stop (14). With the small periods of
:func:`fresh_strategy` that produces exactly one ENTRY (bar 11) and one EXIT
(bar 14).

Bars carry Binance's inclusive ``close_time`` (``open + interval - 1 ms``), the
value the live feed delivers.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hermes.application.notification_router import RouterConfig
from hermes.application.signal_notifier import SignalNotifier
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import close_time_of, delivery_key
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.loader import load_config
from hermes.ports.bar_feed import SourceHealth, SourceState

BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE = datetime(2024, 1, 1, tzinfo=UTC)
H4_BASE = BASE + timedelta(days=15)
_H4 = timedelta(hours=4)
_D1 = timedelta(days=1)


def _make_bar(
    pair: Pair, interval: KlineInterval, open_time: datetime, close: str, low: str | None = None
) -> Bar:
    """Build a closed bar with Binance's inclusive close time."""
    price = Decimal(close)
    return Bar(
        pair=pair,
        interval=interval,
        open_time=open_time,
        open=price,
        high=price + Decimal("2"),
        low=Decimal(low) if low is not None else price - Decimal("2"),
        close=price,
        volume=Decimal("100"),
        close_time=close_time_of(open_time, interval),
    )


def d1_bar(index: int, pair: Pair = BTC) -> Bar:
    """Return the rising 1d bar ``index`` (0-11), closes 1000 to 1200."""
    close = str(1000 + 200 * index // 11)
    return _make_bar(pair, KlineInterval.D1, BASE + _D1 * index, close)


def h4_bar(index: int, pair: Pair = BTC, *, crash: bool = False) -> Bar:
    """Return scripted 4h bar ``index``: flat, breakout at 11, crash at 14.

    ``crash=True`` forces the crash shape at any index.
    """
    open_time = H4_BASE + _H4 * index
    if index == 14 or crash:
        return _make_bar(pair, KlineInterval.H4, open_time, "500", low="100")
    return _make_bar(pair, KlineInterval.H4, open_time, "1000" if index < 11 else "2000")


def history(h4_upto: int, *, h4_from: int = 0, pair: Pair = BTC) -> list[Bar]:
    """Return the twelve 1d bars plus 4h bars ``h4_from..h4_upto``, in delivery order."""
    bars = [d1_bar(i, pair) for i in range(12)]
    bars.extend(h4_bar(i, pair) for i in range(h4_from, h4_upto + 1))
    return sorted(bars, key=delivery_key)


def live(first: int, last: int) -> list[Bar]:
    """Return scripted 4h bars ``first..last`` (the live remainder)."""
    return [h4_bar(i) for i in range(first, last + 1)]


def fresh_strategy() -> SwingStrategyAdapter:
    """Return an adapter with the small periods the swing integration tests use."""
    return SwingStrategyAdapter(
        donchian_period=5,
        adx_period=5,
        adx_threshold=Decimal("5"),
        ema_period=10,
        chandelier_period=5,
        chandelier_atr_mult=Decimal("3.0"),
        atr_sizing_period=5,
    )


class FakeClock:
    """Clock that always reads ``current``."""

    def __init__(self, current: datetime) -> None:
        """Start at ``current``."""
        self.current = current

    def now(self) -> datetime:
        """Return the fixed instant."""
        return self.current


class FakeFeed:
    """Scripted closed-bar feed.

    ``warm_up`` returns the stored bars of the stream. ``stream_closed_bars``
    yields ``live``, then either raises ``error`` or sets ``drained`` and
    blocks forever (an idle live feed), so a test knows every bar was handled.
    """

    def __init__(
        self,
        warm: Sequence[Bar] = (),
        live_bars: Sequence[Bar] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        """Script the feed."""
        self.warm = list(warm)
        self.live_bars = list(live_bars)
        self.error = error
        self.drained = asyncio.Event()
        self.closed = False

    async def warm_up(self, pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
        """Return the stored bars of one stream, newest ``count`` at most."""
        return [b for b in self.warm if b.pair == pair and b.interval == interval][-count:]

    async def stream_closed_bars(
        self,
        pairs: Sequence[Pair],
        intervals: Sequence[KlineInterval],
        *,
        resume_after: Mapping[tuple[Pair, KlineInterval], datetime] | None = None,
    ) -> AsyncIterator[Bar]:
        """Yield the scripted live bars, then fail or idle."""
        for bar in self.live_bars:
            yield bar
        if self.error is not None:
            raise self.error
        self.drained.set()
        await asyncio.Event().wait()

    def health(self) -> SourceHealth:
        """Return a healthy snapshot."""
        return SourceHealth(
            state=SourceState.HEALTHY,
            overdue_seconds=0.0,
            consecutive_failures=0,
            last_closed=None,
            clock_skew_ms=None,
            used_weight_1m=None,
        )

    async def aclose(self) -> None:
        """Record that the feed was closed."""
        self.closed = True


@dataclass
class RecordingChannel:
    """NotifierPort fake that records events and optionally raises."""

    raises: Exception | None = None
    seen: list[DomainEvent] = field(default_factory=list)

    def notify(self, event: DomainEvent) -> None:
        """Record the event, or raise when configured to."""
        if self.raises is not None:
            raise self.raises
        self.seen.append(event)

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        """Unused by the loop."""

    def notify_report(
        self,
        *,
        event: str,
        correlation_id: str,
        title: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> None:
        """Unused by the loop."""


def signal_kinds(channel: RecordingChannel) -> list[str]:
    """Return ``kind`` of every signal event the channel recorded, in order."""
    return [e.kind.value for e in channel.seen if isinstance(e, SignalEmitted)]


def build(
    db_path: Path,
    feed: FakeFeed,
    now: datetime,
    *,
    telegram: RecordingChannel | None = None,
    slack: RecordingChannel | None = None,
) -> serve_module.Composition:
    """Build a composition over a real ledger file with fakes injected.

    One pair (BTC/USDT) with one small-period strategy and the given channels.
    """
    config = load_config(PROJECT_ROOT / "config", env={})
    base = serve_module.build_composition(
        config, db_path=db_path, env={}, feed=feed, clock=FakeClock(now)
    )
    return dataclasses.replace(
        base,
        strategies={BTC: fresh_strategy()},
        pairs=(BTC,),
        signal_notifier=SignalNotifier(RouterConfig(telegram=telegram, slack=slack)),
    )


async def drive(composition: serve_module.Composition, feed: FakeFeed) -> int:
    """Run the loop until the feed idles or the run ends, then shut it down.

    Returns:
        The exit code of :func:`hermes.entrypoints.serve.run`.

    """
    shutdown = asyncio.Event()
    task = asyncio.create_task(serve_module.run(composition, shutdown))
    waiter = asyncio.create_task(feed.drained.wait())
    await asyncio.wait({task, waiter}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
    shutdown.set()
    waiter.cancel()
    return await asyncio.wait_for(task, timeout=20)


def run_once(composition: serve_module.Composition, feed: FakeFeed) -> int:
    """Run :func:`drive` to completion and return the exit code."""
    return asyncio.run(drive(composition, feed))


def events(db_path: Path, event_type: str) -> list[dict[str, str]]:
    """Return ``occurred_at`` and ``payload_json`` of every event of one type, oldest first."""
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT occurred_at, payload_json FROM events WHERE event_type = ? ORDER BY id",
            (event_type,),
        ).fetchall()
    finally:
        connection.close()
    return [{"occurred_at": r[0], "payload_json": r[1]} for r in rows]
