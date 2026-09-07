"""Composition root for the ``hermes serve`` command.

Wires config → adapters → strategy → router → event bus → runs the async
loop until SIGTERM/SIGINT is received.

This module delivers the MINIMUM composition needed to start the paper-trading
engine and exit cleanly on signal. It intentionally stays scoped to
adapter wiring and shutdown plumbing:

- :class:`Composition` is a frozen dataclass holding every wired port that
  Phase-1 paper-trading needs at process start.
- :func:`build_composition` is pure wiring — the only I/O is the SQLite
  ``connect`` inside :class:`~hermes.adapters.ledger.sqlite_ledger.SqliteLedger`
  (which runs migrations idempotently) and the ``httpx.Client`` opened by
  :class:`~hermes.adapters.notifier.telegram_adapter.TelegramAdapter` when
  Telegram credentials are present in the env snapshot.
- :func:`run` awaits the shutdown event and returns; the event-bus is
  populated and ready for publishers but no scheduling loop is spun up
  here — that lives in the follow-up wave (see the PR body for the
  Phase-2 boundary).
- :func:`install_signal_handlers` binds SIGTERM + SIGINT to
  :meth:`asyncio.Event.set`. Falls back to :func:`signal.signal` on
  Windows where :meth:`asyncio.AbstractEventLoop.add_signal_handler`
  raises :class:`NotImplementedError`.

Real-money guard
----------------

The composition root refuses to build a mainnet-touching adapter unless
:attr:`~hermes.infrastructure.config.HermesConfig.allow_mainnet` is True
AND :attr:`~hermes.infrastructure.config.HermesConfig.env` is ``"prod"``.
The stricter of those checks already lives on the config model
(``_mainnet_only_in_prod``); this module treats a missing
``allow_mainnet`` flag as "wire paper-trading only" and never attempts
to open a live exchange client from here. See ADR-003 §00 invariant
I-08.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import structlog

from hermes.adapters.clock.wall import WallClock
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.market_data.binance_historical import (
    BinanceHistoricalAdapter,
    BinanceHistoricalConfig,
)
from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)
from hermes.domain.events.event_bus import EventBus
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.infrastructure.config.root import HermesConfig

__all__ = [
    "Composition",
    "build_composition",
    "install_signal_handlers",
    "run",
]

_log = structlog.get_logger(__name__)

# Default SQLite path used when the operator does not pass ``--db``. The
# file is created on first write; a fresh clone with no prior ledger is a
# valid starting state.
DEFAULT_DB_PATH: Final[Path] = Path("hermes.sqlite3")


@dataclass(frozen=True)
class Composition:
    """Container for every wired port + owned resource at process start.

    Attributes:
        config: The fully-validated :class:`HermesConfig` snapshot the
            composition was built from. Kept on the container so
            downstream layers can consult tier / mainnet flags without
            re-reading YAML.
        event_bus: Synchronous in-process pub/sub. Publishers push
            :class:`~hermes.domain.events.base.DomainEvent` instances,
            subscribers register at composition time.
        ledger: SQLite-backed FIFO ledger. Runs migrations idempotently
            in its constructor.
        clock: :class:`~hermes.adapters.clock.wall.WallClock` — the
            production time source. Backtests inject a
            :class:`~hermes.adapters.clock.simulated.SimulatedClock` at
            their own composition root.
        market_data: Historical filesystem-backed
            :class:`~hermes.adapters.market_data.binance_historical.BinanceHistoricalAdapter`.
            The live WebSocket adapter is intentionally NOT wired here —
            its ``stream_bars`` implementation raises
            :class:`NotImplementedError` today.
        strategy: :class:`~hermes.domain.strategy.swing.adapter.SwingStrategyAdapter`
            configured with pre-registration defaults.
        notifier: Optional :class:`~hermes.adapters.notifier.telegram_adapter.TelegramAdapter`.
            ``None`` when the operator has not populated
            ``HERMES_TELEGRAM__BOT_TOKEN`` + ``HERMES_TELEGRAM__CHAT_ID``.
            Absent credentials degrade the channel to a silent no-op —
            they do NOT abort composition.
        sqlite_connection: The raw connection owned by the composition.
            Kept on the container so :func:`run` can close it in its
            ``finally`` block.
    """

    config: HermesConfig
    event_bus: EventBus
    ledger: SqliteLedger
    clock: WallClock
    market_data: BinanceHistoricalAdapter
    strategy: SwingStrategyAdapter
    notifier: TelegramAdapter | None
    sqlite_connection: sqlite3.Connection


def _build_notifier(env: dict[str, str]) -> TelegramAdapter | None:
    """Build the Telegram notifier when credentials are present in ``env``.

    The notifier is optional — a fresh clone with no populated Telegram
    env vars is a valid starting state (the operator gets no push pings
    until they wire the bot). Any credential-shape error is logged and
    swallowed so a missing token cannot crash the whole engine at
    startup.

    Args:
        env: Env-var snapshot. Pass ``os.environ`` in production; tests
            pass a controlled dict so the ambient shell never leaks in.

    Returns:
        A configured :class:`TelegramAdapter` or ``None`` when
        credentials are missing.
    """
    try:
        config = TelegramConfig.from_env(env)
    except TelegramNotifierError as exc:
        _log.info("serve.notifier.telegram_disabled", reason=str(exc))
        return None
    return TelegramAdapter.create(config)


def build_composition(
    config: HermesConfig,
    *,
    db_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> Composition:
    """Wire every adapter the paper-trading loop needs and return the container.

    Pure wiring — the only I/O is the SQLite ``connect`` (which the
    ledger constructor uses to apply pending migrations idempotently)
    and an optional ``httpx.Client`` on the Telegram adapter when
    credentials are present.

    Args:
        config: The validated :class:`HermesConfig` snapshot.
        db_path: Optional path to the SQLite ledger file. Defaults to
            :data:`DEFAULT_DB_PATH` (``./hermes.sqlite3`` in CWD).
        env: Optional env-var snapshot for the Telegram credential
            probe. Defaults to :data:`os.environ`.

    Returns:
        A fully wired :class:`Composition` ready to hand to :func:`run`.
    """
    db_target = db_path if db_path is not None else DEFAULT_DB_PATH
    env_snapshot = dict(os.environ) if env is None else env

    connection = sqlite3.connect(db_target)
    ledger = SqliteLedger(connection)

    clock = WallClock()
    strategy = SwingStrategyAdapter()
    event_bus = EventBus()

    # BinanceHistoricalAdapter reads Vision-ingested klines from disk. The
    # data directory resolves lazily (first ``stream_bars`` call) from
    # ``HERMES_DATA_DIR``; not required at wire time.
    market_data = BinanceHistoricalAdapter(config=BinanceHistoricalConfig())

    notifier = _build_notifier(env_snapshot)

    return Composition(
        config=config,
        event_bus=event_bus,
        ledger=ledger,
        clock=clock,
        market_data=market_data,
        strategy=strategy,
        notifier=notifier,
        sqlite_connection=connection,
    )


async def run(composition: Composition, shutdown: asyncio.Event) -> None:
    """Await the shutdown signal, then release the composition's resources.

    Phase-1 scope: this is the minimum loop the CLI wraps. Publishers
    fan events out through :attr:`Composition.event_bus`; subscribers
    register at composition time. The scheduling loop that pumps market
    data through the strategy lives in the Phase-2 follow-up wave — see
    the PR body for that boundary.

    Cleanup runs in the ``finally`` block so a mid-loop exception cannot
    leak the SQLite connection or the notifier's ``httpx.Client``.

    Args:
        composition: The wired container from :func:`build_composition`.
        shutdown: An :class:`asyncio.Event` that :func:`install_signal_handlers`
            (or the caller) sets when SIGTERM / SIGINT arrives.
    """
    _log.info("serve.run.started", env=composition.config.env)
    try:
        await shutdown.wait()
    finally:
        _log.info("serve.run.stopping")
        if composition.notifier is not None:
            composition.notifier.close()
        composition.sqlite_connection.close()
        _log.info("serve.run.stopped")


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
