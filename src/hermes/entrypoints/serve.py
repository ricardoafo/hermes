"""Composition root and shadow bar loop for the ``hermes serve`` command.

Wires config → adapters → strategies → notifiers → event bus, then runs the
Phase-1 shadow loop until SIGTERM/SIGINT or a failure. The root lives here,
not in the application layer, because choosing a concrete adapter is an
import the application layer may not make (ADR-013, which supersedes
ADR-011 D-02).

What one process does:

- :func:`build_composition` is pure wiring. The only I/O is the SQLite
  ``connect`` (the ledger runs migrations idempotently) and the HTTP clients
  the adapters own.
- :func:`run` opens an :class:`asyncio.TaskGroup` holding the bar loop, logs
  and reports to Sentry whatever escapes it, closes every resource in order
  and returns the process exit code (0 clean, 1 runtime failure).
- The bar loop warms up one :class:`SwingStrategyAdapter` per pair, seeding
  bars up to a persisted cursor without evaluating them, restoring each
  pair's open position from the ledger, then evaluates every later bar as if
  it were live. A restart therefore neither re-notifies, skips a bar nor
  forgets an open position (change ``309-serve-bar-loop`` D-6).
- A notifier outage is logged and sent to Sentry; it never stops the loop.
  Every other error exits 1 so systemd restarts the unit. Exit codes 2 and 3
  are reserved for usage and config errors found before the loop starts.
- :func:`install_signal_handlers` binds SIGTERM + SIGINT to
  :meth:`asyncio.Event.set`. Falls back to :func:`signal.signal` on Windows
  where :meth:`asyncio.AbstractEventLoop.add_signal_handler` raises
  :class:`NotImplementedError`.

Real-money guard
----------------

No exchange adapter is wired, so no order can be placed by construction. The
real-money gate stays on :attr:`~hermes.infrastructure.config.HermesConfig.allow_mainnet`
AND :attr:`~hermes.infrastructure.config.HermesConfig.env` being ``"prod"``
(``_mainnet_only_in_prod`` on the config model). See ADR-003 §00 invariant
I-08.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sqlite3
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import structlog
from returns.result import Failure

from hermes.adapters.clock.wall import WallClock
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.market_data.binance_live_klines import BinanceLiveKlinesAdapter
from hermes.adapters.notifier.slack_adapter import SlackAdapter, SlackConfig, SlackNotifierError
from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)
from hermes.application.calibration.certification_gates import (
    require_live_approval,
    v1_parameter_fingerprint,
)
from hermes.application.notification_router import RouterConfig
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.signal_notifier import SignalNotifier
from hermes.domain.errors import HermesError, LedgerError
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.event_bus import EventBus, EventDispatchError
from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.lots.quote_leg import CapitalSeed, seed_timestamp
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import (
    Stream,
    delivery_key,
    interval_duration,
    within_catchup,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.strategy.signal import OpenPosition, SignalKind
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure import sentry_integration
from hermes.infrastructure.config.root import HermesConfig
from hermes.infrastructure.logging import bind_correlation_id, new_correlation_id
from hermes.ports.bar_feed import ClosedBarFeedPort
from hermes.ports.certification import CertificationStoreError, CertificationStorePort
from hermes.ports.clock import ClockPort
from hermes.ports.strategy import StrategyPort

__all__ = [
    "CATCHUP_BARS",
    "SHADOW_STRATEGY_NAME",
    "SHADOW_UNIVERSE",
    "BarGapError",
    "Composition",
    "StartupError",
    "build_composition",
    "certification_dir_for",
    "check_startup",
    "install_signal_handlers",
    "parameter_fingerprint_for",
    "report_failure",
    "run",
]

_log = structlog.get_logger(__name__)

# Default SQLite path used when the operator does not pass ``--db``. The
# file is created on first write; a fresh clone with no prior ledger is a
# valid starting state.
DEFAULT_DB_PATH: Final[Path] = Path("hermes.sqlite3")

CATCHUP_BARS: Final = 2
"""A signal at most this many bars old is still notified (H4: up to 8 h)."""

SHADOW_UNIVERSE: Final = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
"""The three pairs of pre-registration §3."""

SHADOW_STRATEGY_NAME: Final = "swing-donchian-4h"
"""The one strategy the shadow roster may enable (pre-registration §5)."""

_ONE_MS: Final = timedelta(milliseconds=1)
_NEVER: Final = datetime(1970, 1, 1, tzinfo=UTC)


class StartupError(HermesError):
    """The configuration cannot run the shadow loop; the process exits 3."""


def certification_dir_for(db_path: Path) -> Path:
    """Return the certification record directory that sits next to the ledger."""
    return db_path.parent / "certification"


def parameter_fingerprint_for(config: HermesConfig) -> str:
    """Fingerprint what the enabled strategy trades with (D-8 guardrail).

    Raises:
        StartupError: No strategy is enabled.
    """
    roster = next((s for s in config.strategies.strategies if s.enabled), None)
    if roster is None:
        raise StartupError("no enabled strategy to fingerprint")
    return v1_parameter_fingerprint(
        capital_budget=roster.capital_budget, universe=config.market_data.pairs
    )


class BarGapError(HermesError):
    """A stream skipped a bar, so the strategy buffers would be wrong; exits 1."""


@dataclass(frozen=True)
class Composition:
    """Container for every wired port + owned resource at process start.

    Attributes:
        config: The fully-validated :class:`HermesConfig` snapshot the
            composition was built from.
        event_bus: Synchronous in-process pub/sub. ``SignalEmitted`` and
            ``BarProcessed`` are subscribed to the ledger at build time.
        ledger: SQLite-backed ledger. Holds signals and the bar cursor.
        clock: Production time source.
        feed: Closed-bar source (warm-up history plus the live stream).
        strategies: One strategy instance per pair, routed by ``bar.pair``.
        strategy_id: The bucket id every signal carries.
        tracker: Portfolio state handed to the strategies on every bar.
        signal_notifier: Fans a signal out to the routed channels.
        pairs: Pairs the feed follows.
        intervals: Intervals the feed follows.
        telegram: Telegram adapter, or ``None`` without credentials. Kept
            concrete because only the adapters have ``close()``.
        slack: Slack adapter, or ``None`` without credentials.
        sqlite_connection: The raw connection owned by the composition,
            closed last by :func:`run`.
    """

    config: HermesConfig
    event_bus: EventBus
    ledger: SqliteLedger
    clock: ClockPort
    feed: ClosedBarFeedPort
    strategies: Mapping[Pair, StrategyPort]
    strategy_id: StrategyId
    tracker: PortfolioTracker
    signal_notifier: SignalNotifier
    pairs: tuple[Pair, ...]
    intervals: tuple[KlineInterval, ...]
    telegram: TelegramAdapter | None
    slack: SlackAdapter | None
    sqlite_connection: sqlite3.Connection


def _telegram_config(env: Mapping[str, str]) -> TelegramConfig | None:
    """Return the Telegram credentials in ``env``, or ``None`` when absent."""
    try:
        return TelegramConfig.from_env(dict(env))
    except TelegramNotifierError as exc:
        _log.info("serve.notifier.telegram_disabled", reason=str(exc))
        return None


def _slack_config(env: Mapping[str, str]) -> SlackConfig | None:
    """Return the Slack credentials in ``env``, or ``None`` when absent."""
    try:
        return SlackConfig.from_env(dict(env))
    except SlackNotifierError as exc:
        _log.info("serve.notifier.slack_disabled", reason=str(exc))
        return None


def check_startup(
    config: HermesConfig,
    env: Mapping[str, str],
    *,
    certification_store: CertificationStorePort | None = None,
) -> None:
    """Refuse a configuration the shadow loop cannot run safely.

    Args:
        config: The loaded configuration.
        env: Env-var snapshot used to probe channel credentials.
        certification_store: Where the certification record lives. Required
            when ``allow_mainnet`` is true; without it, mainnet is refused.

    Raises:
        StartupError: The enabled roster is not exactly the one swing entry,
            the followed pairs or intervals differ from pre-registration §3,
            ``env`` is ``prod`` and neither Telegram nor Slack has
            credentials, or ``allow_mainnet`` is true while the strategy is
            not LIVE_APPROVED with unchanged parameters (pre-registration §7,
            ADR-014).
    """
    enabled = [s.name for s in config.strategies.strategies if s.enabled]
    if enabled != [SHADOW_STRATEGY_NAME]:
        raise StartupError(
            f"enabled strategies must be exactly [{SHADOW_STRATEGY_NAME!r}], got {enabled!r}"
        )
    if set(config.market_data.pairs) != set(SHADOW_UNIVERSE):
        raise StartupError(
            f"market_data.pairs must be {list(SHADOW_UNIVERSE)!r}, got {config.market_data.pairs!r}"
        )
    if set(config.market_data.intervals) != {KlineInterval.H4, KlineInterval.D1}:
        raise StartupError("market_data.intervals must be exactly 4h and 1d")
    if config.env == "prod" and _telegram_config(env) is None and _slack_config(env) is None:
        raise StartupError(
            "env is prod but neither Telegram nor Slack credentials are set; "
            "a signal would reach no one"
        )
    if config.allow_mainnet:
        _check_live_approval(config, certification_store)


def _check_live_approval(
    config: HermesConfig, certification_store: CertificationStorePort | None
) -> None:
    """Raise unless real capital is allowed for the enabled strategy."""
    if certification_store is None:
        raise StartupError("allow_mainnet is set but no certification store is configured")
    try:
        record = certification_store.load(SHADOW_STRATEGY_NAME)
    except CertificationStoreError as exc:
        raise StartupError(f"allow_mainnet is set but {exc}") from exc
    refusal = require_live_approval(record, parameter_fingerprint_for(config))
    if refusal:
        raise StartupError(f"allow_mainnet is set but {refusal}")


def seed_capital_once(
    ledger: SqliteLedger, strategy_id: StrategyId, capital: Money, *, now: datetime
) -> bool:
    """Record the strategy's start capital in the ledger unless it is already there.

    The seed is dated by the ledger's earliest bar, signal or trade, or ``now``
    for a ledger without any, so a restart never moves the start of the run.
    Its EUR value stays pending until ``hermes fx backfill`` prices the seed
    date (change ``405-usdt-fifo`` D-1).

    Args:
        ledger: The ledger.
        strategy_id: Strategy the capital belongs to.
        capital: The strategy's capital budget in its quote currency.
        now: Current time, from the composition's clock.

    Returns:
        ``True`` when this call wrote the seed.

    Raises:
        LedgerError: When the ledger cannot be read or written.
    """
    if ledger.capital_seed(strategy_id) is not None:
        return False
    seeded_at = seed_timestamp(ledger.earliest_activity(), now)
    ledger.record_capital_seed(
        CapitalSeed(strategy_id=strategy_id, amount=capital, seeded_at=seeded_at),
        ShadowCapitalSeeded(
            occurred_at=seeded_at,
            correlation_id=new_correlation_id(),
            strategy_id=strategy_id,
            amount=capital,
        ),
    )
    _log.info(
        "serve.capital_seeded",
        strategy_id=strategy_id.value,
        amount=str(capital.amount),
        currency=capital.currency.value,
        seeded_at=seeded_at.isoformat(),
    )
    return True


def build_composition(
    config: HermesConfig,
    *,
    db_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    feed: ClosedBarFeedPort | None = None,
    clock: ClockPort | None = None,
) -> Composition:
    """Wire every adapter the shadow loop needs and return the container.

    Pure wiring: the only I/O is the SQLite ``connect`` (migrations run
    idempotently in the ledger constructor) and the HTTP clients the adapters
    own.

    Args:
        config: The validated :class:`HermesConfig` snapshot. Callers run
            :func:`check_startup` first.
        db_path: Optional path to the SQLite ledger file. Defaults to
            :data:`DEFAULT_DB_PATH` (``./hermes.sqlite3`` in CWD).
        env: Optional env-var snapshot for the credential probes. Defaults
            to :data:`os.environ`.
        feed: Closed-bar source override for tests. Defaults to
            :class:`BinanceLiveKlinesAdapter`.
        clock: Clock override for tests. Defaults to :class:`WallClock`.

    Returns:
        A fully wired :class:`Composition` ready to hand to :func:`run`.

    Raises:
        StartupError: The start capital cannot be recorded, or the ledger's
            recorded trades cannot be replayed into the portfolio. The SQLite
            connection is closed before raising.
    """
    db_target = db_path if db_path is not None else DEFAULT_DB_PATH
    env_snapshot: Mapping[str, str] = dict(os.environ) if env is None else env

    connection = sqlite3.connect(db_target)
    ledger = SqliteLedger(connection)

    wall: ClockPort = clock if clock is not None else WallClock()
    event_bus = EventBus()
    event_bus.subscribe(SignalEmitted, ledger.append_event)
    event_bus.subscribe(BarProcessed, ledger.append_event)

    roster = next(s for s in config.strategies.strategies if s.enabled)
    strategy_id = StrategyId(roster.name)
    capital = Money(amount=roster.capital_budget, currency=roster.quote)
    seed = PortfolioSnapshot.seed(strategy_id, capital)
    try:
        seed_capital_once(ledger, strategy_id, capital, now=wall.now())
    except LedgerError as exc:
        connection.close()
        raise StartupError(f"cannot record the start capital in the ledger: {exc.msg}") from exc
    try:
        rebuilt = PortfolioTracker.rebuilt(seed, ledger.trade_events(strategy_id))
    except LedgerError as exc:
        connection.close()
        raise StartupError(f"cannot rebuild the portfolio from the ledger: {exc.msg}") from exc
    if isinstance(rebuilt, Failure):
        connection.close()
        raise StartupError(f"cannot rebuild the portfolio from the ledger: {rebuilt.failure()}")
    tracker = rebuilt.unwrap()
    pairs = config.market_data.parsed_pairs()
    strategies: dict[Pair, StrategyPort] = {pair: SwingStrategyAdapter() for pair in pairs}

    telegram_config = _telegram_config(env_snapshot)
    slack_config = _slack_config(env_snapshot)
    telegram = TelegramAdapter.create(telegram_config) if telegram_config is not None else None
    slack = SlackAdapter.create(slack_config) if slack_config is not None else None

    return Composition(
        config=config,
        event_bus=event_bus,
        ledger=ledger,
        clock=wall,
        feed=feed
        if feed is not None
        else BinanceLiveKlinesAdapter.create(config.market_data, wall),
        strategies=strategies,
        strategy_id=strategy_id,
        tracker=tracker,
        signal_notifier=SignalNotifier(RouterConfig(telegram=telegram, slack=slack)),
        pairs=pairs,
        intervals=tuple(config.market_data.intervals),
        telegram=telegram,
        slack=slack,
        sqlite_connection=connection,
    )


async def run(composition: Composition, shutdown: asyncio.Event) -> int:
    """Run the bar loop until shutdown or failure, then release every resource.

    The loop runs inside an :class:`asyncio.TaskGroup`, so a sibling task
    added later cancels it and vice versa. Anything that escapes is logged
    with its traceback, sent to Sentry (a returned exit code no longer
    reaches Sentry's excepthook) and turned into exit code 1.

    Args:
        composition: The wired container from :func:`build_composition`.
        shutdown: An :class:`asyncio.Event` that :func:`install_signal_handlers`
            (or the caller) sets when SIGTERM / SIGINT arrives.

    Returns:
        ``0`` after a clean shutdown, ``1`` after a runtime failure. Never 2
        or 3: those would stop the systemd unit for good.
    """
    _log.info(
        "serve.run.started",
        env=composition.config.env,
        pairs=len(composition.strategies),
    )
    code = 0
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(_bar_loop(composition, shutdown))
    except* Exception as failures:
        code = 1
        for exc in failures.exceptions:
            report_failure(exc)
    finally:
        _log.info("serve.run.stopping")
        await _release(composition)
        _log.info("serve.run.stopped", exit_code=code)
    return code


def report_failure(exc: BaseException) -> None:
    """Log ``exc`` with its traceback and send it to Sentry.

    Args:
        exc: The failure that ends the process with exit code 1.
    """
    _log.error("serve.run.failed", exc_info=exc)
    if isinstance(exc, Exception):
        sentry_integration.capture(exc)


async def _release(c: Composition) -> None:
    """Close the feed, then Slack, Telegram and SQLite last; never raise."""
    try:
        await c.feed.aclose()
    except Exception as exc:
        _log.error("serve.close.feed_failed", exc_info=exc)
    with contextlib.ExitStack() as stack:
        stack.callback(_close_quietly, c.sqlite_connection.close)
        if c.telegram is not None:
            stack.callback(_close_quietly, c.telegram.close)
        if c.slack is not None:
            stack.callback(_close_quietly, c.slack.close)


def _close_quietly(close: Callable[[], object]) -> None:
    """Call ``close`` and log, instead of raising, when it fails."""
    try:
        close()
    except Exception as exc:
        _log.error("serve.close.failed", exc_info=exc)


async def _bar_loop(c: Composition, shutdown: asyncio.Event) -> None:
    """Warm up, catch up, then handle live bars until shutdown."""
    if shutdown.is_set():
        return
    loop = _BarLoop(c)
    await loop.warm_up_and_catch_up(shutdown)
    if shutdown.is_set():
        return
    loop.log_next_due()
    stream = c.feed.stream_closed_bars(c.pairs, c.intervals).__aiter__()
    while (bar := await _next_or_stop(stream, shutdown)) is not None:
        await loop.handle(bar)


async def _anext(stream: AsyncIterator[Bar]) -> Bar:
    """Await the next bar; a thin coroutine so it can be a task."""
    return await anext(stream)


async def _next_or_stop(stream: AsyncIterator[Bar], shutdown: asyncio.Event) -> Bar | None:
    """Wait for the next bar or for shutdown, whichever comes first.

    Shutdown is raced at this idle await only: a bar being handled is never
    cancelled, so a handler cannot be cut between the ledger write and the
    notification.

    Args:
        stream: The live bar stream.
        shutdown: The shutdown event.

    Returns:
        The next bar, or ``None`` when shutdown arrived first.

    Raises:
        RuntimeError: The feed ended, which a live feed must not do.
    """
    bar_task = asyncio.ensure_future(_anext(stream))
    stop_task = asyncio.ensure_future(shutdown.wait())
    try:
        await asyncio.wait({bar_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (bar_task, stop_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(bar_task, stop_task, return_exceptions=True)
    if bar_task.cancelled():
        return None
    try:
        return bar_task.result()
    except StopAsyncIteration as exc:
        raise RuntimeError("bar feed ended unexpectedly") from exc


@dataclass
class _BarLoop:
    """Per-process loop state: the last handled bar of every stream."""

    c: Composition
    previous: dict[Stream, datetime] = field(default_factory=dict)

    async def warm_up_and_catch_up(self, shutdown: asyncio.Event) -> None:
        """Seed history up to each cursor, then evaluate the bars after it."""
        c = self.c
        warm: list[Bar] = []
        for pair in c.pairs:
            for interval in c.intervals:
                count = c.config.market_data.warmup_bars(interval)
                warm.extend(await c.feed.warm_up(pair, interval, count))
        warm.sort(key=delivery_key)
        cursors = self._load_cursors(warm)
        for pair, strategy in c.strategies.items():
            seeded = [
                b for b in warm if b.pair == pair and b.close_time <= cursors[(b.pair, b.interval)]
            ]
            strategy.seed(seeded, open_position=self._restore_position(pair, cursors))
        _log.info("serve.warmup.done", bars=len(warm))
        for bar in warm:
            if shutdown.is_set():
                return
            if bar.close_time > cursors[(bar.pair, bar.interval)]:
                await self.handle(bar)

    def _load_cursors(self, warm: list[Bar]) -> dict[Stream, datetime]:
        """Return each stream's cursor; a first boot starts at its last warm-up bar."""
        c = self.c
        cursors: dict[Stream, datetime] = {}
        for pair in c.pairs:
            for interval in c.intervals:
                stream = (pair, interval)
                stored = c.ledger.last_processed_close(pair, interval)
                if stored is not None:
                    cursors[stream] = stored
                    self.previous[stream] = stored + _ONE_MS - interval_duration(interval)
                    continue
                history = [b for b in warm if (b.pair, b.interval) == stream]
                if not history:
                    cursors[stream] = _NEVER
                    continue
                last = history[-1]
                cursors[stream] = last.close_time
                self.previous[stream] = last.open_time
                c.event_bus.publish(
                    BarProcessed.from_bar(last, correlation_id=new_correlation_id())
                )
                _log.info("serve.cursor.initialised", pair=str(pair), interval=interval.value)
        return cursors

    def _restore_position(
        self, pair: Pair, cursors: Mapping[Stream, datetime]
    ) -> OpenPosition | None:
        """Rebuild the open position from the ledger, bounded by the 4h cursor."""
        cursor = cursors.get((pair, KlineInterval.H4))
        if cursor is None:
            return None
        latest = self.c.ledger.latest_signal_event(self.c.strategy_id, pair, at_or_before=cursor)
        if latest is None or latest.kind is not SignalKind.ENTRY or latest.entry_price is None:
            return None
        return OpenPosition(entry_time=latest.occurred_at, entry_price=latest.entry_price)

    async def handle(self, bar: Bar) -> None:
        """Evaluate one closed bar, record any signal, notify, then move the cursor."""
        c = self.c
        stream = (bar.pair, bar.interval)
        if not self._is_next(stream, bar):
            return
        correlation_id = new_correlation_id()
        bind_correlation_id(correlation_id)

        c.tracker.mark(bar)
        signal_ = c.strategies[bar.pair].on_bar(bar, c.tracker.state())
        if signal_ is not None:
            event = SignalEmitted.from_signal(
                signal_,
                strategy_id=c.strategy_id,
                pair=bar.pair,
                correlation_id=correlation_id,
            )
            if not c.ledger.has_signal_event(event):
                c.event_bus.publish(event)
                if within_catchup(
                    c.clock.now(), event.occurred_at, bar.interval, bars=CATCHUP_BARS
                ):
                    await self._notify(event)
                else:
                    _log.warning(
                        "serve.signal.stale_not_notified",
                        pair=str(event.pair),
                        kind=event.kind.value,
                        occurred_at=event.occurred_at.isoformat(),
                    )
        # Data only (#421): the archive check and the parity replay read these
        # values back; nothing in the strategy does. Stored before BarProcessed
        # so a processed bar always has its values on record.
        stored = c.ledger.record_live_bar(bar)
        if stored is not None:
            _log.warning(
                "serve.live_bar.differs_from_stored",
                pair=str(bar.pair),
                interval=bar.interval.value,
                open_time=bar.open_time.isoformat(),
                stored_close=str(stored.close),
                received_close=str(bar.close),
            )
        c.event_bus.publish(BarProcessed.from_bar(bar, correlation_id=correlation_id))
        self.previous[stream] = bar.open_time
        # The ledger row alone leaves the journal silent between signals, so
        # an operator could not tell a quiet market from a stalled loop (#371).
        _log.info(
            "serve.bar.processed",
            pair=str(bar.pair),
            interval=bar.interval.value,
            open_time=bar.open_time.isoformat(),
            close_time=bar.close_time.isoformat(),
            signal="none" if signal_ is None else signal_.kind.value,
            next_due=self.next_due(stream).isoformat(),
        )

    def next_due(self, stream: Stream) -> datetime:
        """Return when the bar after the last handled one should arrive.

        That bar closes one interval after the last handled bar closed, and the
        feed delivers it ``settle_delay_seconds`` later (the closed-bar rule).

        Args:
            stream: A stream with at least one handled or seeded bar.

        Returns:
            The expected delivery time of the stream's next bar.
        """
        duration = interval_duration(stream[1])
        settle = timedelta(seconds=self.c.config.market_data.settle_delay_seconds)
        return self.previous[stream] + 2 * duration + settle

    def log_next_due(self) -> None:
        """Log, once the loop goes live, when each stream's next bar is due."""
        _log.info(
            "serve.bar.next_due",
            streams={
                f"{pair} {interval.value}": self.next_due((pair, interval)).isoformat()
                for pair, interval in sorted(self.previous, key=lambda s: (str(s[0]), s[1].value))
            },
        )

    def _is_next(self, stream: Stream, bar: Bar) -> bool:
        """Return ``False`` for a repeated bar; raise on a skipped one.

        Raises:
            BarGapError: ``bar`` does not directly follow the stream's last bar.
        """
        last = self.previous.get(stream)
        if last is None:
            return True
        if bar.open_time <= last:
            _log.debug("serve.bar.duplicate_skipped", pair=str(bar.pair))
            return False
        expected = last + interval_duration(bar.interval)
        if bar.open_time != expected:
            raise BarGapError(
                f"{bar.pair} {bar.interval.value}: expected the bar opening at "
                f"{expected.isoformat()}, got {bar.open_time.isoformat()}"
            )
        return True

    async def _notify(self, event: SignalEmitted) -> None:
        """Fan the event out in a worker thread; a channel outage is logged, not fatal."""
        try:
            await asyncio.to_thread(self.c.signal_notifier.dispatch, event)
        except EventDispatchError as exc:
            _log.error("serve.notify.failed", exc_info=exc)
            for failure in exc.failures:
                if isinstance(failure, Exception):
                    sentry_integration.capture(failure)


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown: asyncio.Event,
) -> None:
    """Bind SIGTERM + SIGINT to ``shutdown.set`` for a graceful stop.

    Uses :meth:`asyncio.AbstractEventLoop.add_signal_handler` on POSIX.
    Falls back to :func:`signal.signal` on Windows where the loop-based
    API raises :class:`NotImplementedError`. In both cases the handler
    marshals to the loop via :meth:`asyncio.AbstractEventLoop.call_soon_threadsafe`
    so the event flip is guaranteed to reach the async waiter.

    Args:
        loop: The running event loop (``asyncio.get_running_loop()``).
        shutdown: The :class:`asyncio.Event` to flip when a signal
            arrives.
    """

    def _flip() -> None:
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _flip)
        except NotImplementedError, RuntimeError:
            # Windows path: add_signal_handler is not available.
            # signal.signal accepts an int-handler signature; wrap it
            # so the loop wakeup is threadsafe.
            def _win_handler(
                _signum: int,
                _frame: object,
                _loop: asyncio.AbstractEventLoop = loop,
            ) -> None:
                _loop.call_soon_threadsafe(shutdown.set)

            signal.signal(sig, _win_handler)
