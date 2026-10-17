"""Composition root and shadow bar loop for the ``hermes serve`` command.

Wires config → adapters → strategies → notifiers → event bus, then runs the
Phase-1 shadow loop until SIGTERM/SIGINT or a failure. The root lives here,
not in the application layer, because choosing a concrete adapter is an
import the application layer may not make (ADR-013, which supersedes
ADR-011 D-02).

What one process does:

- :func:`build_composition` is pure wiring. The only I/O is the serve lock
  next to the ledger (one serve per ledger, #855), the SQLite ``connect``
  (the ledger runs migrations idempotently) and the HTTP clients the adapters
  own.
- :func:`run` opens an :class:`asyncio.TaskGroup` holding the bar loop, logs
  and reports to Sentry whatever escapes it, closes every resource in order
  and returns the process exit code (0 clean, 1 runtime failure).
- Every signal goes through the :class:`~hermes.application.execution.shadow.ShadowExecutor`
  around the fill kernel the backtest uses: a simulated order walks a live
  Binance depth snapshot (or the pre-registration §6.2 book when none is
  fresh), and the fill is recorded as a ``TradeExecuted`` v2 trade with its
  execution-quality row (change ``353-fill-kernel-in-serve``).
- The bar loop warms up one :class:`SwingStrategyAdapter` per pair, seeding
  bars up to a persisted cursor without evaluating them, restoring each
  pair's open position from the ledger, then evaluates every later bar as if
  it were live. A restart therefore neither re-notifies, skips a bar nor
  forgets an open position (change ``309-serve-bar-loop`` D-6).
- Beside the bar loop, a sampler records each pair's spread every few
  minutes, and each completed 1D bar records the 30-day pair correlations
  (#835). These RO-01 / RO-02 inputs are recorded only: no rule reads them,
  and a failed sample or correlation is logged and skipped.
- Delivery rule (#854): each signal is pushed to each routed channel once,
  in practice at least once with dedupe. The push is recorded in the ledger
  (``SignalNotified``) after it is sent, so a crash before the record pushes
  the signal again after the restart, and a crash before the push loses
  nothing. A channel that failed is pushed again on the pair's next bars
  while the signal is within the catch-up window; a stale signal is never
  pushed.
- A failing notifier channel, outage or bug, is logged and sent to Sentry;
  it never stops the loop.
  Every other error exits 1 so systemd restarts the unit. Exit codes 2 and 3
  are reserved for usage and config errors found before the loop starts.
- :func:`install_signal_handlers` binds SIGTERM + SIGINT to
  :meth:`asyncio.Event.set`. Falls back to :func:`signal.signal` on Windows
  where :meth:`asyncio.AbstractEventLoop.add_signal_handler` raises
  :class:`NotImplementedError`.

Real-money guard
----------------

No exchange adapter is wired, so no order can be placed by construction:
fills are simulated by walking a public, keyless depth snapshot. The
real-money gate stays on :attr:`~hermes.infrastructure.config.HermesConfig.allow_mainnet`
AND :attr:`~hermes.infrastructure.config.HermesConfig.env` being ``"prod"``
(``_mainnet_only_in_prod`` on the config model). See ADR-003 §00 invariant
I-08.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import signal
import sqlite3
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Final

import structlog
from returns.result import Failure

from hermes.adapters.clock.wall import WallClock
from hermes.adapters.fees.binance_history import load_binance_fee_history
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.adapters.fx.frankfurter import FrankfurterAdapter
from hermes.adapters.fx.frankfurter import FrankfurterConfig as FrankfurterWireConfig
from hermes.adapters.fx.rate_limiter import RateLimitConfig
from hermes.adapters.ledger.anomaly_reader import latest_anomaly
from hermes.adapters.ledger.connection import open_ledger
from hermes.adapters.ledger.migration_runner import unknown_applied_migrations
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.market_data.binance_depth_snapshot import BinanceDepthSnapshotAdapter
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
from hermes.application.execution.halt_record import halt_record
from hermes.application.execution.kernel import Declined, Executed, FillKernel, Halted
from hermes.application.execution.position_sync import sync_after_signal
from hermes.application.execution.shadow import (
    AlreadyFilled,
    ShadowExecution,
    ShadowExecutor,
    shadow_risk_engine,
)
from hermes.application.ledger.eur_backfill import backfill_pending_eur, pending_rate_keys
from hermes.application.ledger.eur_pricer import EurPricer, LookupBudget
from hermes.application.ledger.trade_recorder import TradeRecorder
from hermes.application.messages.eur_rate import EurRateLookup
from hermes.application.monitoring.clock_skew import clock_is_off, clock_skew_change
from hermes.application.notification_router import EventClass, RouterConfig, route
from hermes.application.order_router.sizing import SizingPolicy
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.risk_inputs.correlation_recorder import CorrelationRecorder
from hermes.application.risk_inputs.spread_sampler import SPREAD_SAMPLE_SECONDS, SpreadSampler
from hermes.application.signal_notifier import SignalNotifier
from hermes.application.strategy_protocol.kill_trigger_evaluator import KillTriggerEvaluator
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.errors import HermesError, InfraError, LedgerError
from hermes.domain.events.anomaly_detected import (
    CLOCK_SKEW_SOURCE,
    AnomalyDetected,
    AnomalySeverity,
)
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.event_bus import EventBus, EventDispatchError
from hermes.domain.events.risk_halt_recorded import RiskHaltRecorded
from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.signal_notified import SignalNotified
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.events.trade_executed import TradeSide as ExecutedSide
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.fees.fee_schedule_selector import FeeScheduleRow, select_fee_schedule
from hermes.domain.fx.eur_pricing import fill_date_madrid
from hermes.domain.lots.quote_leg import CapitalSeed, seed_timestamp
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import (
    Stream,
    interval_duration,
    order_for_delivery,
    within_catchup,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.position import Position
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.strategy.signal import OpenPosition, SignalKind, StrategySignal
from hermes.domain.strategy.stop_mark import StopMark
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints._release import read_release
from hermes.infrastructure import sentry_integration
from hermes.infrastructure.config.models import FrankfurterConfig
from hermes.infrastructure.config.root import HermesConfig
from hermes.infrastructure.file_lock import FileLock
from hermes.infrastructure.logging import bind_correlation_id, new_correlation_id
from hermes.ports.bar_feed import ClosedBarFeedPort
from hermes.ports.certification import CertificationStoreError, CertificationStorePort
from hermes.ports.clock import ClockPort
from hermes.ports.fx import FxRateProviderPort
from hermes.ports.orderbook_snapshot import OrderbookSnapshotPort
from hermes.ports.strategy import StrategyPort

__all__ = [
    "CATCHUP_BARS",
    "EXPERIMENT_DIR",
    "SERVE_LOCK_SUFFIX",
    "SHADOW_STRATEGY_NAME",
    "SHADOW_UNIVERSE",
    "BarGapError",
    "Composition",
    "LedgerInUseError",
    "StartupError",
    "build_composition",
    "certification_dir_for",
    "check_startup",
    "experiment_files",
    "install_signal_handlers",
    "parameter_fingerprint_for",
    "report_failure",
    "run",
    "serve_lock_path",
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

EXPERIMENT_DIR: Final = Path("docs/experiments/swing-mvp1")
"""Where the signed pre-registration and the fee history sit under the experiment root."""

# Boot EUR pricing caps. Why 20 calls / 60 s: a healthy boot needs a handful of
# distinct (quote, date) rates; with the FX source down each call costs up to
# its 10 s timeout, so the caps bound the wasted time to one minute while the
# live stream already runs. Rates left over stay pending for the next boot or
# ``hermes fx backfill``.
BOOT_EUR_MAX_FX_CALLS: Final = 20
BOOT_EUR_BUDGET_SEC: Final = 60.0

_ONE_MS: Final = timedelta(milliseconds=1)
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_NEVER: Final = _EPOCH


class StartupError(HermesError):
    """The configuration cannot run the shadow loop; the process exits 3."""


class LedgerInUseError(StartupError):
    """Another ``hermes serve`` holds the ledger's serve lock; the process exits 4."""


SERVE_LOCK_SUFFIX: Final = ".serve.lock"
"""Suffix of the lock file next to the ledger that one running serve holds."""


def serve_lock_path(db_path: Path) -> Path:
    """Return the serve lock file for the ledger at ``db_path``.

    Only ``hermes serve`` takes this lock. Readers (console, bot, tax pack,
    ``hermes status``) never touch it, so they keep working while serve runs.
    """
    return db_path.with_name(db_path.name + SERVE_LOCK_SUFFIX)


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


def experiment_files(experiment_root: Path) -> tuple[Path, Path]:
    """Return the pre-registration and fee-history paths under ``experiment_root``.

    The root is the repository in development and the release directory on
    the VPS (``/opt/hermes/current``), which carries both files (D-6).
    """
    folder = experiment_root / EXPERIMENT_DIR
    return folder / "pre-registration-v1.md", folder / "fee-history.csv"


def pre_registration_for(config_dir: Path) -> PreRegistration:
    """Return the signed pre-registration that sits beside ``config_dir``.

    A release keeps ``config/`` and the experiment folder side by side, and
    an installed package has no repository to search from (issue #923).
    Without that file beside the config, the repository's copy is read.

    Raises:
        FileNotFoundError: Neither copy exists.
        ValueError: The file is not the v1 pre-registration.
    """
    root = config_dir.resolve().parent
    if experiment_files(root)[0].is_file():
        return PreRegistration(project_root=root)
    return PreRegistration()


def _holding_opened_at(
    trades: Sequence[TradeExecuted], pair: Pair, at_or_before: datetime
) -> datetime | None:
    """Return the close time of the 4h bar the held coin was bought on.

    The buy is the first since ``pair`` was last flat. Its fill lands just after
    the bar closes, but the strategy keys its time stop and its highest high on
    the bar's ``close_time``, so the fill time is moved back to it.
    """
    opened_at: datetime | None = None
    held = Decimal(0)
    for trade in trades:
        if trade.pair != pair or trade.occurred_at > at_or_before:
            continue
        if trade.side is ExecutedSide.BUY:
            if held == 0:
                opened_at = trade.occurred_at
            held += trade.qty.amount
            continue
        held -= trade.qty.amount
        if held <= 0:
            held = Decimal(0)
            opened_at = None
    if opened_at is None:
        return None
    four_hours = interval_duration(KlineInterval.H4)
    boundary = _EPOCH + ((opened_at + _ONE_MS - _EPOCH) // four_hours) * four_hours
    return boundary - _ONE_MS


class BarGapError(HermesError):
    """A stream skipped a bar, so the strategy buffers would be wrong; exits 1."""


def _open_ledger(connection: sqlite3.Connection, path: Path) -> SqliteLedger:
    """Open the ledger, refusing a file that is not one or a schema newer than this code.

    Raises:
        StartupError: The file is corrupt or not a SQLite database, or a newer
            release has migrated it. The caller closes the connection.
    """
    try:
        ledger = SqliteLedger(connection)
        newer = unknown_applied_migrations(connection)
    except sqlite3.OperationalError:
        # A full disk or a held lock is not a corrupt file; it keeps its own error.
        raise
    except sqlite3.DatabaseError as exc:
        raise StartupError(f"the ledger {path} cannot be opened as a SQLite ledger: {exc}") from exc
    if newer:
        # The deploy and rollback scripts refuse this; a manual symlink flip
        # or a direct start does not, and old code would write to a schema it
        # does not know (#856).
        raise StartupError(
            f"the ledger {path} was migrated by a newer release ({', '.join(newer)}); "
            "start the release that migrated it"
        )
    return ledger


@dataclass(frozen=True)
class Composition:
    """Container for every wired port + owned resource at process start.

    Attributes:
        config: The fully-validated :class:`HermesConfig` snapshot the
            composition was built from.
        event_bus: Synchronous in-process pub/sub. ``SignalEmitted``,
            ``BarProcessed`` and ``AnomalyDetected`` are subscribed to the
            ledger at build time; anomalies are recorded only (#136).
        ledger: SQLite-backed ledger. Holds signals and the bar cursor.
        clock: Production time source.
        feed: Closed-bar source (warm-up history plus the live stream).
        strategies: One strategy instance per pair, routed by ``bar.pair``.
        strategy_id: The bucket id every signal carries.
        tracker: Portfolio state handed to the strategies on every bar.
        signal_notifier: Fans a signal out to the routed channels.
        alert_channels: The channels a halt or kill alert is routed over (#749).
        pairs: Pairs the feed follows.
        intervals: Intervals the feed follows.
        telegram: Telegram adapter, or ``None`` without credentials. Kept
            concrete because only the adapters have ``close()``.
        slack: Slack adapter, or ``None`` without credentials.
        sqlite_connection: The raw connection owned by the composition,
            closed last by :func:`run`.
        executor: Turns signals into simulated fills through the shared
            fill kernel and records them.
        orderbooks: Live depth snapshots; closed by :func:`run`.
        fx_adapter: The FX adapter the trade recorder prices with, when the
            composition built it (closed by :func:`run`); ``None`` when one
            was injected.
        eur_pricer: The EUR rate lookups shared by the trade recorder and the
            start-up pricing of pending values.
        spread_sample_seconds: Step of the spread sampler that records the
            RO-02 input (#835); no rule reads the samples.
        serve_lock: The ledger's serve lock, held from build until
            :func:`run` releases it after closing SQLite; ``None`` for a
            composition built by hand in a test.
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
    alert_channels: RouterConfig
    pairs: tuple[Pair, ...]
    intervals: tuple[KlineInterval, ...]
    telegram: TelegramAdapter | None
    slack: SlackAdapter | None
    sqlite_connection: sqlite3.Connection
    executor: ShadowExecutor
    orderbooks: OrderbookSnapshotPort
    fx_adapter: FrankfurterAdapter | None
    eur_pricer: EurPricer
    spread_sample_seconds: float = SPREAD_SAMPLE_SECONDS
    serve_lock: FileLock | None = None


def _eur_rate_lookup(pricer: EurPricer) -> EurRateLookup:
    """Return the notifier's rate lookup: the pricer's memory, never its provider.

    An event with a pair is looked up by the pair's quote currency and the
    Madrid date of ``occurred_at``; any other event has no rate. The lookup
    never touches the network, so a push cannot wait on the FX source.

    Args:
        pricer: The shared pricer whose cache the fill path and boot warm.

    Returns:
        A callable mapping an event to units of its quote per euro, or ``None``.
    """

    def lookup(event: DomainEvent) -> Decimal | None:
        pair = getattr(event, "pair", None)
        if pair is None:
            return None
        return pricer.cached_rate(pair.quote, fill_date_madrid(event.occurred_at))

    return lookup


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
    experiment_root: Path | None = None,
) -> None:
    """Refuse a configuration the shadow loop cannot run safely.

    Args:
        config: The loaded configuration.
        env: Env-var snapshot used to probe channel credentials.
        certification_store: Where the certification record lives. Required
            when ``allow_mainnet`` is true; without it, mainnet is refused.
        experiment_root: Directory holding :data:`EXPERIMENT_DIR`. When given,
            the signed pre-registration and the fee history must exist under
            it: the fill kernel's kill rules and fees read them.

    Raises:
        StartupError: The enabled roster is not exactly the one swing entry,
            the followed pairs or intervals differ from pre-registration §3,
            ``env`` is ``prod`` and neither Telegram nor Slack has
            credentials, or ``allow_mainnet`` is true while the strategy is
            not LIVE_APPROVED with unchanged parameters (pre-registration §7,
            ADR-014), or a required experiment file is missing.
    """
    if experiment_root is not None:
        missing = [str(f) for f in experiment_files(experiment_root) if not f.is_file()]
        if missing:
            raise StartupError(f"experiment files missing: {', '.join(missing)}")
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
    Its EUR value stays pending until the start-up pricing in :func:`run` (or
    ``hermes fx backfill``) prices the seed date (changes ``405-usdt-fifo`` D-1,
    ``630-seed-eur-at-boot``).

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


def _rebuild_tracker(
    ledger: SqliteLedger, seed: PortfolioSnapshot, strategy_id: StrategyId
) -> PortfolioTracker:
    """Replay the ledger's trades over ``seed`` and restore the recorded peak equity.

    Fills cannot see mark-to-market highs, so the peak is raised to the one the
    latest portfolio mark stored; without it the RO-08 drawdown reads low after
    a restart (integrity rules 4.1, pre-registration 10).

    Raises:
        StartupError: The trades or the mark cannot be read, or a trade cannot be replayed.
    """
    try:
        rebuilt = PortfolioTracker.rebuilt(seed, ledger.trade_events(strategy_id))
        last_mark = ledger.latest_portfolio_mark(strategy_id)
    except LedgerError as exc:
        raise StartupError(f"cannot rebuild the portfolio from the ledger: {exc.msg}") from exc
    if isinstance(rebuilt, Failure):
        raise StartupError(f"cannot rebuild the portfolio from the ledger: {rebuilt.failure()}")
    tracker = rebuilt.unwrap()
    if last_mark is not None and last_mark.snapshot.quote == tracker.snapshot.quote:
        tracker.restore_peak(last_mark.snapshot.peak_equity)
    return tracker


def build_composition(
    config: HermesConfig,
    *,
    db_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    feed: ClosedBarFeedPort | None = None,
    clock: ClockPort | None = None,
    experiment_root: Path | None = None,
    orderbooks: OrderbookSnapshotPort | None = None,
    fx: FxRateProviderPort | None = None,
) -> Composition:
    """Wire every adapter the shadow loop needs and return the container.

    Pure wiring: the only I/O is the serve lock next to the ledger (held until
    :func:`run` returns, so a second serve on the same ledger is refused), the
    SQLite ``connect`` (migrations run idempotently in the ledger constructor)
    and the HTTP clients the adapters own.

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
        experiment_root: Directory holding :data:`EXPERIMENT_DIR`. Defaults to
            the repository root found from the installed package.
        orderbooks: Depth source override for tests. Defaults to
            :class:`BinanceDepthSnapshotAdapter`.
        fx: FX provider override for tests. Defaults to Frankfurter (USDT
            read as USD).

    Returns:
        A fully wired :class:`Composition` ready to hand to :func:`run`.

    Raises:
        LedgerInUseError: Another ``hermes serve`` holds this ledger's serve
            lock. Nothing was opened.
        StartupError: The pre-registration or the fee history cannot be read,
            the start capital cannot be recorded, or the ledger's recorded
            trades or fill-quality rows cannot be read or replayed into the
            portfolio. The SQLite connection is closed before raising.
    """
    db_target = db_path if db_path is not None else DEFAULT_DB_PATH
    lock = FileLock(serve_lock_path(db_target))
    try:
        acquired = lock.try_acquire()
    except OSError as exc:
        raise StartupError(f"cannot create the serve lock {lock.path}: {exc}") from exc
    if not acquired:
        holder = lock.holder_pid()
        raise LedgerInUseError(
            f"another hermes serve is running on {db_target}"
            + (f" (pid {holder})" if holder else "")
            + f"; it holds {lock.path}. Stop it first, or point --db at another ledger."
        )
    try:
        composition = _wire_composition(
            config,
            db_path=db_target,
            env=env,
            feed=feed,
            clock=clock,
            experiment_root=experiment_root,
            orderbooks=orderbooks,
            fx=fx,
        )
    except BaseException:
        lock.release()
        raise
    return dataclasses.replace(composition, serve_lock=lock)


def _wire_composition(
    config: HermesConfig,
    *,
    db_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    feed: ClosedBarFeedPort | None = None,
    clock: ClockPort | None = None,
    experiment_root: Path | None = None,
    orderbooks: OrderbookSnapshotPort | None = None,
    fx: FxRateProviderPort | None = None,
) -> Composition:
    """Wire the composition once the serve lock is held; see :func:`build_composition`."""
    db_target = db_path if db_path is not None else DEFAULT_DB_PATH
    env_snapshot: Mapping[str, str] = dict(os.environ) if env is None else env
    pre_registration, fee_rows = _load_experiment(experiment_root)

    connection = open_ledger(db_target)
    # Any failure after the ledger opened (a migration refused, an adapter that
    # cannot be built) must close it, or the connection leaks (#855).
    try:
        return _wire_on_ledger(
            config,
            connection,
            env_snapshot=env_snapshot,
            pre_registration=pre_registration,
            fee_rows=fee_rows,
            feed=feed,
            clock=clock,
            orderbooks=orderbooks,
            fx=fx,
            ledger_path=db_target,
        )
    except BaseException:
        connection.close()
        raise


def _wire_on_ledger(
    config: HermesConfig,
    connection: sqlite3.Connection,
    *,
    env_snapshot: Mapping[str, str],
    pre_registration: PreRegistration,
    fee_rows: list[FeeScheduleRow],
    feed: ClosedBarFeedPort | None,
    clock: ClockPort | None,
    orderbooks: OrderbookSnapshotPort | None,
    fx: FxRateProviderPort | None,
    ledger_path: Path,
) -> Composition:
    """Wire everything that needs the open ledger; the caller closes it on failure."""
    ledger = _open_ledger(connection, ledger_path)

    wall: ClockPort = clock if clock is not None else WallClock()
    event_bus = EventBus()
    event_bus.subscribe(SignalEmitted, ledger.append_event)
    event_bus.subscribe(BarProcessed, ledger.append_event)
    # Record-only (#136): no subscriber pauses or gates entries on an anomaly.
    event_bus.subscribe(AnomalyDetected, ledger.append_event)

    roster = next(s for s in config.strategies.strategies if s.enabled)
    strategy_id = StrategyId(roster.name)
    capital = Money(amount=roster.capital_budget, currency=roster.quote)
    seed = PortfolioSnapshot.seed(strategy_id, capital)
    try:
        seed_capital_once(ledger, strategy_id, capital, now=wall.now())
        recorded_seed = ledger.capital_seed(strategy_id)
    except LedgerError as exc:
        raise StartupError(f"cannot record the start capital in the ledger: {exc.msg}") from exc
    tracker = _rebuild_tracker(ledger, seed, strategy_id)
    fill_model = OrderbookWalkFillModel()
    books = (
        orderbooks
        if orderbooks is not None
        else BinanceDepthSnapshotAdapter.create(config.market_data, wall)
    )
    fx_adapter: FrankfurterAdapter | None = None
    rates = fx
    if rates is None:
        fx_adapter = _frankfurter(config.frankfurter)
        rates = fx_adapter
    eur_pricer = EurPricer(rates)
    try:
        executor = ShadowExecutor(
            kernel=_build_kernel(
                fill_model, tracker, strategy_id, pre_registration, roster.sizing_policy()
            ),
            fill_model=fill_model,
            books=books,
            recorder=TradeRecorder(ledger, eur_pricer),
            ledger=ledger,
            clock=wall,
            fees_at=_fees_at(fee_rows),
            strategy_id=strategy_id,
            not_before=None if recorded_seed is None else recorded_seed.seeded_at,
        )
    except LedgerError as exc:
        raise StartupError(f"cannot read the fill-quality rows: {exc.msg}") from exc
    pairs = config.market_data.parsed_pairs()
    strategies: dict[Pair, StrategyPort] = {pair: SwingStrategyAdapter() for pair in pairs}

    telegram_config = _telegram_config(env_snapshot)
    slack_config = _slack_config(env_snapshot)
    eur_rate = _eur_rate_lookup(eur_pricer)
    telegram = (
        TelegramAdapter.create(telegram_config, eur_rate=eur_rate)
        if telegram_config is not None
        else None
    )
    slack = (
        SlackAdapter.create(slack_config, eur_rate=eur_rate) if slack_config is not None else None
    )

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
        alert_channels=RouterConfig(telegram=telegram, slack=slack),
        pairs=pairs,
        intervals=tuple(config.market_data.intervals),
        telegram=telegram,
        slack=slack,
        sqlite_connection=connection,
        executor=executor,
        orderbooks=books,
        fx_adapter=fx_adapter,
        eur_pricer=eur_pricer,
    )


def _load_experiment(
    experiment_root: Path | None,
) -> tuple[PreRegistration, list[FeeScheduleRow]]:
    """Read the signed pre-registration and the fee history (D-6).

    Raises:
        StartupError: Either file is missing or unreadable.
    """
    try:
        pre_registration = PreRegistration(project_root=experiment_root)
        fee_path = pre_registration.pre_reg_path().parent / "fee-history.csv"
        return pre_registration, load_binance_fee_history(fee_path)
    except (OSError, ValueError, KeyError) as exc:
        raise StartupError(f"cannot read the experiment files: {exc}") from exc


def _fees_at(rows: list[FeeScheduleRow]) -> Callable[[datetime, Pair], FeeSchedule]:
    """Return the pre-registration §6.1 fee lookup over ``rows``."""

    def fees_at(at: datetime, pair: Pair) -> FeeSchedule:
        return select_fee_schedule(rows, at, pair)

    return fees_at


def _build_kernel(
    fill_model: OrderbookWalkFillModel,
    tracker: PortfolioTracker,
    strategy_id: StrategyId,
    pre_registration: PreRegistration,
    sizing: SizingPolicy,
) -> FillKernel:
    """Build the fill kernel the backtest uses, with serve's risk rules (D-3).

    The one place serve configures the kernel. ``sizing`` is the enabled
    roster entry's ``StrategyConfig.sizing_policy()``, the same limits the
    backtest sizes with.
    """
    return FillKernel(
        fill_model=fill_model,
        risk_engine=shadow_risk_engine(),
        kill_evaluator=KillTriggerEvaluator(pre_reg=pre_registration),
        tracker=tracker,
        strategy_id=strategy_id,
        sizing=sizing,
    )


def _frankfurter(config: FrankfurterConfig) -> FrankfurterAdapter:
    """Build the Frankfurter adapter that prices USDT as USD, as ``hermes fx`` does."""
    return FrankfurterAdapter.create(
        FrankfurterWireConfig(
            base_url=config.base_url,
            timeout_sec=config.timeout_sec,
            rate_limit=RateLimitConfig(max_weight=config.requests_per_minute, window_seconds=60),
            wire_codes=FrankfurterWireConfig.USDT_AS_USD,
        )
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
        release=read_release().name,
    )
    code = 0
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(_bar_loop(composition, shutdown))
            group.create_task(_sample_spreads(composition, shutdown))
    except* Exception as failures:
        code = 1
        for exc in failures.exceptions:
            report_failure(exc)
    finally:
        _log.info("serve.run.stopping")
        await _release(composition)
        _log.info("serve.run.stopped", exit_code=code)
    return code


def price_pending_eur_once(c: Composition, pricer: EurPricer | None = None) -> None:
    """Price the pending capital seed and trades once, on the calling thread.

    Runs the same backfill service as ``hermes fx backfill`` (same rate source
    and rules). :func:`_price_pending_eur_capped` is the boot entry point: it
    warms the rate cache off the event loop first, so this call only reads the
    cache. Any failure is logged and swallowed: a value that stays pending is
    priced by the next boot or ``hermes fx backfill``.

    Args:
        c: The wired container.
        pricer: The pricer to use; the container's own when omitted.
    """
    try:
        report = backfill_pending_eur(
            c.ledger,
            pricer if pricer is not None else c.eur_pricer,
            today=fill_date_madrid(c.clock.now()),
        )
    except Exception as exc:
        _log.warning("serve.eur_pricing_failed", error=type(exc).__name__, detail=str(exc))
        return
    _log.info(
        "serve.eur_priced",
        seeds_filled=len(report.seeds_filled),
        seeds_pending=len(report.seeds_pending),
        trades_filled=len(report.filled),
        trades_pending=len(report.pending),
    )


async def _price_pending_eur_capped(c: Composition, shutdown: asyncio.Event) -> None:
    """Price pending EUR values after catch-up without blocking the event loop.

    The blocking FX lookups run in a worker thread under a call and time
    budget; the SQLite reads and writes stay on the loop thread (the connection
    is bound to it). Shutdown or cancellation aborts the lookups at the next
    one; at most the call in flight finishes in the background. Rates not
    looked up stay pending, and the next boot or ``hermes fx backfill`` prices
    them.
    """
    today = fill_date_madrid(c.clock.now())
    budget = LookupBudget(max_calls=BOOT_EUR_MAX_FX_CALLS, max_seconds=BOOT_EUR_BUDGET_SEC)
    try:
        keys = pending_rate_keys(c.ledger)
    except Exception as exc:
        _log.warning("serve.eur_pricing_failed", error=type(exc).__name__, detail=str(exc))
        return
    if not keys:
        price_pending_eur_once(c)
        return
    warm = asyncio.ensure_future(
        asyncio.to_thread(c.eur_pricer.warm, keys, today=today, budget=budget)
    )
    stop = asyncio.ensure_future(shutdown.wait())
    try:
        await asyncio.wait({warm, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        budget.stop.set()
        stop.cancel()
    if not warm.done():
        return
    try:
        skipped = warm.result()
    except Exception as exc:
        _log.warning("serve.eur_pricing_failed", error=type(exc).__name__, detail=str(exc))
        return
    if skipped:
        _log.warning("serve.eur_pricing_capped", rates_skipped=skipped, rates_total=len(keys))
    price_pending_eur_once(c, c.eur_pricer.cached_only())


def report_failure(exc: BaseException) -> None:
    """Log ``exc`` with its traceback and send it to Sentry.

    Args:
        exc: The failure that ends the process with exit code 1.
    """
    _log.error("serve.run.failed", exc_info=exc)
    if isinstance(exc, Exception):
        sentry_integration.capture(exc)


async def _release(c: Composition) -> None:
    """Close the feeds, then FX, Slack, Telegram, SQLite and, last, the serve lock."""
    try:
        await c.feed.aclose()
    except Exception as exc:
        _log.error("serve.close.feed_failed", exc_info=exc)
    try:
        await c.orderbooks.aclose()
    except Exception as exc:
        _log.error("serve.close.orderbooks_failed", exc_info=exc)
    with contextlib.ExitStack() as stack:
        # Callbacks run last-in first-out: the lock goes only after SQLite has closed.
        if c.serve_lock is not None:
            stack.callback(_close_quietly, c.serve_lock.release)
        stack.callback(_close_quietly, c.sqlite_connection.close)
        if c.fx_adapter is not None:
            stack.callback(_close_quietly, c.fx_adapter.close)
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
    # After catch-up so its fills keep the live book; as a task so the live
    # stream is not held back; cancelled with the loop so shutdown is prompt.
    pricing = asyncio.create_task(_price_pending_eur_capped(c, shutdown))
    try:
        loop.log_next_due()
        stream = c.feed.stream_closed_bars(c.pairs, c.intervals).__aiter__()
        while (bar := await _next_or_stop(stream, shutdown)) is not None:
            await loop.handle(bar)
    finally:
        pricing.cancel()
        await asyncio.gather(pricing, return_exceptions=True)


async def _sample_spreads(c: Composition, shutdown: asyncio.Event) -> None:
    """Record each pair's spread on a fixed step beside the bar loop (#835).

    Record only: no rule reads the samples. The sampler logs and skips every
    failed sample, so this task never ends the TaskGroup; the TaskGroup
    cancels it when the bar loop fails.
    """
    await SpreadSampler(
        books=c.orderbooks,
        store=c.ledger,
        clock=c.clock,
        pairs=c.pairs,
        interval_seconds=c.spread_sample_seconds,
    ).run(shutdown)


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
    catching_up: bool = False
    clock_off: bool | None = None
    """Whether the last recorded clock-skew state is off; read from the ledger once."""

    async def warm_up_and_catch_up(self, shutdown: asyncio.Event) -> None:
        """Seed history up to each cursor, then evaluate the bars after it."""
        c = self.c
        warm: list[Bar] = []
        for pair in c.pairs:
            for interval in c.intervals:
                count = c.config.market_data.warmup_bars(interval)
                warm.extend(await c.feed.warm_up(pair, interval, count))
        warm = list(order_for_delivery(warm))
        cursors = self._load_cursors(warm)
        for pair, strategy in c.strategies.items():
            seeded = [
                b for b in warm if b.pair == pair and b.close_time <= cursors[(b.pair, b.interval)]
            ]
            strategy.seed(seeded, open_position=self._restore_position(pair, cursors))
        for bar in warm:
            if bar.close_time <= cursors[(bar.pair, bar.interval)]:
                # The tracker is rebuilt from trades at fill prices; without the
                # marks, exposure, equity and drawdown read cost until each
                # pair's next bar (#863).
                c.tracker.mark(bar)
                c.executor.observe(bar)
        _log.info("serve.warmup.done", bars=len(warm))
        self.catching_up = True
        try:
            for bar in warm:
                if shutdown.is_set():
                    return
                if bar.close_time > cursors[(bar.pair, bar.interval)]:
                    await self.handle(bar)
        finally:
            self.catching_up = False

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
        return self._held_position(pair, at_or_before=cursor)

    def _held_position(self, pair: Pair, *, at_or_before: datetime) -> OpenPosition | None:
        """Return the position the portfolio holds in ``pair``, from its entry signal.

        An entry signal that was never filled leaves the portfolio without the
        pair, so it gives nothing (#707). Exits declined after the entry are
        skipped: the coin is still held, and so is that entry (#706).
        """
        position = self.c.tracker.snapshot.positions.get(pair)
        if position is None:
            return None
        entry = self.c.ledger.latest_signal_event(
            self.c.strategy_id, pair, at_or_before=at_or_before, kind=SignalKind.ENTRY
        )
        if entry is None or entry.entry_price is None:
            return self._held_without_entry_signal(position, at_or_before=at_or_before)
        stop = self.c.ledger.latest_stop_mark(self.c.strategy_id, pair, entry.occurred_at)
        return OpenPosition(
            entry_time=entry.occurred_at,
            entry_price=entry.entry_price,
            last_stop=None if stop is None else stop.record.stop_price,
        )

    def _held_without_entry_signal(
        self, position: Position, *, at_or_before: datetime
    ) -> OpenPosition | None:
        """Watch a held coin whose entry signal is missing, from its own fills (#902).

        An old or manual trade, or a ledger that lost its signal rows, would
        otherwise seed the strategy flat: no exit rule would watch the coin,
        and the next breakout would buy it a second time. The entry time is
        the first buy since the pair was last flat, so the time stop counts
        from when the coin was bought; the entry price is the average cost.
        """
        opened_at = _holding_opened_at(
            self.c.ledger.trade_events(self.c.strategy_id), position.pair, at_or_before
        )
        if opened_at is None:
            _log.warning("serve.position.unwatched", pair=str(position.pair))
            return None
        _log.warning(
            "serve.position.no_entry_signal",
            pair=str(position.pair),
            entry_time=opened_at.isoformat(),
        )
        stop = self.c.ledger.latest_stop_mark(self.c.strategy_id, position.pair, opened_at)
        return OpenPosition(
            entry_time=opened_at,
            entry_price=position.cost_basis / position.quantity,
            last_stop=None if stop is None else stop.record.stop_price,
        )

    async def handle(self, bar: Bar) -> None:
        """Evaluate one closed bar, record any signal, notify, then move the cursor.

        A gap in the warm-up history is a bar the exchange still does not
        publish after a restart, so it is reported and passed over instead of
        stopping serve (#852).

        Raises:
            BarGapError: A live bar skips one; the restart picks up a late bar.
        """
        c = self.c
        stream = (bar.pair, bar.interval)
        try:
            if not self._is_next(stream, bar):
                return
        except BarGapError as gap:
            # Exiting here again would loop until systemd gives up, with any
            # open position left unwatched; the exchange's history keeps the
            # same hole, so the backtest reads it the same way.
            if not self.catching_up:
                raise
            await self._report_missing_bars(gap)
        await self._watch_clock()
        correlation_id = new_correlation_id()
        bind_correlation_id(correlation_id)

        c.tracker.mark(bar)
        c.executor.observe(bar)
        signal_ = c.strategies[bar.pair].on_bar(bar, c.tracker.state())
        emitted: tuple[SignalEmitted, bool] | None = None
        if signal_ is not None:
            event = SignalEmitted.from_signal(
                signal_,
                strategy_id=c.strategy_id,
                pair=bar.pair,
                correlation_id=correlation_id,
            )
            is_new = not c.ledger.has_signal_event(event)
            if is_new:
                c.event_bus.publish(event)
            # The signal row comes first, then the fill (D-5): a crash between
            # them re-evaluates the bar, and the executor sees no trade yet.
            await self._execute(signal_, bar, correlation_id)
            emitted = (event if is_new else self._stored_signal(event), is_new)
        # Failed pushes of earlier bars go out first: this bar's signal has no
        # failed push yet, so it is never pushed twice in one bar (#854).
        await self._push_owed(bar.pair)
        if emitted is not None:
            await self._push_first(*emitted, bar=bar)
        # Data only (#421): the archive check and the parity replay read these
        # values back; nothing in the strategy does. Stored before BarProcessed
        # so a processed bar always has its values on record.
        stored = c.ledger.record_live_bar(bar)
        self._record_correlations(bar)
        # The console reads the portfolio from the ledger, not from this process (#387).
        c.ledger.record_portfolio_mark(
            PortfolioMark(
                snapshot=c.tracker.current,
                pair=bar.pair,
                interval=bar.interval,
                bar_close_time=bar.close_time,
            )
        )
        self._record_stop(bar)
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
            **_entry_check_fields(c.strategies[bar.pair]),
        )

    def _record_correlations(self, bar: Bar) -> None:
        """Store the 30-day pair correlations when ``bar`` completes a day (#835).

        Record only: no rule reads them. A failure is logged and skipped, so it
        never stops the loop; the journal gets counts, never the values.
        """
        if bar.interval is not KlineInterval.D1:
            return
        recorder = CorrelationRecorder(bars=self.c.ledger, store=self.c.ledger, pairs=self.c.pairs)
        try:
            written = recorder.record_for(bar)
        except Exception as exc:
            _log.warning(
                "serve.correlations.failed",
                close_time=bar.close_time.isoformat(),
                error=type(exc).__name__,
                detail=str(exc),
            )
            return
        if written:
            _log.info(
                "serve.correlations.recorded",
                close_time=bar.close_time.isoformat(),
                count=written,
            )

    def _record_stop(self, bar: Bar) -> None:
        """Store the stop the strategy evaluated on this bar, when it holds a position (#388).

        Ledger only, never the journal: the stop is a price, and the reviews
        read the journal (ADR-012 D-02).
        """
        strategy = self.c.strategies[bar.pair]
        if not isinstance(strategy, SwingStrategyAdapter):
            return
        record = strategy.last_stop_record
        if record is None:
            return
        self.c.ledger.record_stop_mark(
            StopMark(
                strategy_id=self.c.strategy_id,
                pair=bar.pair,
                bar_close_time=bar.close_time,
                record=record,
            )
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

    async def _report_missing_bars(self, gap: BarGapError) -> None:
        """Tell the operator a bar is passed over: error log, Sentry and a Telegram alert."""
        _log.error("serve.bar.gap_passed_over", reason=str(gap))
        sentry_integration.capture(gap)
        telegram = self.c.telegram
        if telegram is None:
            return
        alert = AnomalyDetected(
            occurred_at=self.c.clock.now(),
            correlation_id=new_correlation_id(),
            source="serve.bar_gap",
            severity=AnomalySeverity.CRITICAL,
            reason=f"bar never published, passed over: {gap}",
        )
        try:
            await asyncio.to_thread(telegram.notify, alert)
        except InfraError as exc:
            _log.error("serve.bar.gap_alert_failed", exc_info=exc)

    async def _watch_clock(self) -> None:
        """Record and alert the host clock leaving, or coming back to, exchange time (#859).

        The limit is the settle delay, as in ``hermes health``. Leaving is a
        loud ``CRITICAL`` alert, coming back a silent ``INFO`` one. Only a
        change is recorded, so a clock that stays off alerts once; the state survives
        a restart through the ledger, which is also where ``hermes status``
        reads it. RO-04 is untouched: entries still halt on a stale book.
        """
        c = self.c
        if self.clock_off is None:
            self.clock_off = clock_is_off(latest_anomaly(c.sqlite_connection, CLOCK_SKEW_SOURCE))
        alert = clock_skew_change(
            c.feed.health().clock_skew_ms,
            limit=timedelta(seconds=c.config.market_data.settle_delay_seconds),
            was_off=self.clock_off,
            now=c.clock.now(),
            correlation_id=new_correlation_id(),
        )
        if alert is None:
            return
        self.clock_off = alert.severity is AnomalySeverity.CRITICAL
        c.ledger.append_event(alert)
        _log.warning("serve.clock.skew_changed", severity=alert.severity.value, reason=alert.reason)
        for adapter in route(EventClass.ANOMALY_DETECTED, c.alert_channels):
            try:
                await asyncio.to_thread(adapter.notify, alert)
            except Exception as exc:  # one channel's bug must not stop the bar loop (#854)
                _log.error("serve.clock.alert_failed", exc_info=exc)
                sentry_integration.capture(exc)

    async def _execute(self, signal_: StrategySignal, bar: Bar, correlation_id: str) -> None:
        """Simulate the signal's order, log the outcome (never its P&L, D-7), sync the strategy."""
        result = await self.c.executor.execute(signal_, bar, correlation_id=correlation_id)
        _log_execution(bar.pair, signal_.kind, result)
        record = halt_record(
            result.outcome,
            strategy_id=self.c.strategy_id,
            pair=bar.pair,
            at=signal_.signal_time,
            correlation_id=correlation_id,
        )
        if record is not None:
            await self._record_halt(record)
        # A signal that did not fill leaves the strategy in the position the
        # portfolio holds: flat after an entry (#707), still long after an exit (#706).
        sync_after_signal(
            self.c.strategies[bar.pair],
            signal_,
            filled=isinstance(result.outcome, Executed | AlreadyFilled),
            held=partial(self._held_position, bar.pair, at_or_before=signal_.signal_time),
        )

    async def _record_halt(self, record: RiskHaltRecorded) -> None:
        """Record a halted or killed entry once, then alert it on every routed channel (#749).

        Record, then push: the ledger row is the record of truth. A bar
        evaluated again after a restart finds its row and neither records
        nor alerts it twice. A failing channel is logged and sent to Sentry.
        """
        c = self.c
        if c.ledger.has_risk_halt(record):
            return
        c.ledger.append_event(record)
        _log.warning(
            "serve.halt.recorded",
            pair=str(record.pair),
            kind=record.kind.value,
            rule=record.rule,
            reason=record.reason,
        )
        for adapter in route(EventClass.RISK_HALT, c.alert_channels):
            try:
                await asyncio.to_thread(adapter.notify, record)
            except Exception as exc:  # one channel's bug must not stop the bar loop (#854)
                _log.error("serve.halt.alert_failed", exc_info=exc)
                sentry_integration.capture(exc)

    def _stored_signal(self, event: SignalEmitted) -> SignalEmitted:
        """Return the stored row of a re-evaluated signal: its push record keys on it."""
        stored = self.c.ledger.latest_signal_event(
            self.c.strategy_id, event.pair, at_or_before=event.occurred_at, kind=event.kind
        )
        return event if stored is None else stored

    async def _push_first(self, event: SignalEmitted, is_new: bool, *, bar: Bar) -> None:
        """Push this bar's signal unless it is stale or a push of it is on record."""
        c = self.c
        if not within_catchup(c.clock.now(), event.occurred_at, bar.interval, bars=CATCHUP_BARS):
            if is_new:
                _log.warning(
                    "serve.signal.stale_not_notified",
                    pair=str(event.pair),
                    kind=event.kind.value,
                    occurred_at=event.occurred_at.isoformat(),
                )
            return
        if c.ledger.latest_signal_notified(event) is None:
            await self._push(event)

    async def _push_owed(self, pair: Pair) -> None:
        """Push again, to the failed channels only, each recent signal whose last push failed.

        Recent is the catch-up window of the shortest stream, where signals come from.
        """
        c = self.c
        now = c.clock.now()
        window = CATCHUP_BARS * min(interval_duration(i) for i in c.intervals)
        owed = c.ledger.signals_owed_a_push(c.strategy_id, pair, since=now - window, until=now)
        for event, failed in owed:
            _log.info(
                "serve.notify.retry",
                pair=str(event.pair),
                kind=event.kind.value,
                occurred_at=event.occurred_at.isoformat(),
                channels=list(failed),
            )
            await self._push(event, only=failed)

    async def _push(self, event: SignalEmitted, only: tuple[str, ...] | None = None) -> None:
        """Push in a worker thread, then record the push; a failing channel is logged, not fatal."""
        delivery = await asyncio.to_thread(self.c.signal_notifier.deliver, event, only)
        # Push, then record (#854): a crash between the two pushes the signal
        # again after the restart. Twice beats never for a trade.
        self.c.ledger.append_event(
            SignalNotified.after(event, delivered=delivery.delivered, failed=delivery.failed)
        )
        if not delivery.failures:
            return
        failures = tuple(exc for _, exc in delivery.failures)
        _log.error(
            "serve.notify.failed",
            channels=list(delivery.failed),
            exc_info=EventDispatchError(
                event_type_name=type(event).__qualname__, failures=failures
            ),
        )
        for failure in failures:
            sentry_integration.capture(failure)


def _entry_check_fields(strategy: StrategyPort) -> dict[str, str]:
    """Return which entry conditions held on the bar just evaluated, as yes/no.

    Booleans only, never a price or an indicator value, so the journal line
    stays outcome-blind for the reviews that read it (ADR-012 D-06, #656).
    Empty when the bar was not an entry check (a daily bar, or a position open).
    """
    if not isinstance(strategy, SwingStrategyAdapter):
        return {}
    check = strategy.last_entry_check
    if check is None:
        return {}
    return {
        "entry_breakout": _yes_no(check.breakout),
        "entry_momentum": _yes_no(check.momentum),
        "entry_regime": _yes_no(check.regime),
    }


def _yes_no(held: bool) -> str:
    return "yes" if held else "no"


def _log_execution(pair: Pair, kind: SignalKind, result: ShadowExecution) -> None:
    """Log one execution: prices, sizes and rules only, never equity, P&L or drift."""
    source = None if result.book_source is None else result.book_source.value
    if result.snapshot_error is not None:
        _log.warning("serve.book.snapshot_failed", pair=str(pair), error=result.snapshot_error)
    outcome = result.outcome
    if isinstance(outcome, Executed):
        fill = outcome.fill
        _log.info(
            "serve.fill.executed",
            pair=str(pair),
            side=fill.order.side.value,
            qty=str(fill.executed_qty),
            price=str(fill.avg_price),
            fee=str(fill.fee.amount),
            book_source=source,
        )
    elif isinstance(outcome, Declined):
        _log.info(
            "serve.fill.declined",
            pair=str(pair),
            kind=kind.value,
            rule=outcome.rule,
            book_source=source,
        )
    elif isinstance(outcome, Halted):
        _log.warning("serve.fill.halted", pair=str(pair), kind=kind.value)
    else:
        _log.info(
            "serve.fill.already_filled",
            pair=str(pair),
            side=outcome.trade.side.value,
            occurred_at=outcome.trade.occurred_at.isoformat(),
        )


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
