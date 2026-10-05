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
- A notifier outage is logged and sent to Sentry; it never stops the loop.
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
from hermes.adapters.fees.binance_history import load_binance_fee_history
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.adapters.fx.frankfurter import FrankfurterAdapter
from hermes.adapters.fx.frankfurter import FrankfurterConfig as FrankfurterWireConfig
from hermes.adapters.fx.rate_limiter import RateLimitConfig
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
from hermes.application.execution.kernel import Declined, Executed, FillKernel, Halted
from hermes.application.execution.shadow import (
    ShadowExecution,
    ShadowExecutor,
    shadow_risk_engine,
)
from hermes.application.ledger.eur_backfill import backfill_pending_eur, pending_rate_keys
from hermes.application.ledger.eur_pricer import EurPricer, LookupBudget
from hermes.application.ledger.trade_recorder import TradeRecorder
from hermes.application.notification_router import RouterConfig
from hermes.application.order_router.sizing import SizingPolicy
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.signal_notifier import SignalNotifier
from hermes.application.strategy_protocol.kill_trigger_evaluator import KillTriggerEvaluator
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.errors import HermesError, LedgerError
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.event_bus import EventBus, EventDispatchError
from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.events.signal_emitted import SignalEmitted
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
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.strategy.signal import OpenPosition, SignalKind, StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints._release import read_release
from hermes.infrastructure import sentry_integration
from hermes.infrastructure.config.models import FrankfurterConfig
from hermes.infrastructure.config.root import HermesConfig
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
    "SHADOW_STRATEGY_NAME",
    "SHADOW_UNIVERSE",
    "BarGapError",
    "Composition",
    "StartupError",
    "build_composition",
    "certification_dir_for",
    "check_startup",
    "experiment_files",
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


def experiment_files(experiment_root: Path) -> tuple[Path, Path]:
    """Return the pre-registration and fee-history paths under ``experiment_root``.

    The root is the repository in development and the release directory on
    the VPS (``/opt/hermes/current``), which carries both files (D-6).
    """
    folder = experiment_root / EXPERIMENT_DIR
    return folder / "pre-registration-v1.md", folder / "fee-history.csv"


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
        executor: Turns signals into simulated fills through the shared
            fill kernel and records them.
        orderbooks: Live depth snapshots; closed by :func:`run`.
        fx_adapter: The FX adapter the trade recorder prices with, when the
            composition built it (closed by :func:`run`); ``None`` when one
            was injected.
        eur_pricer: The EUR rate lookups shared by the trade recorder and the
            start-up pricing of pending values.
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
    executor: ShadowExecutor
    orderbooks: OrderbookSnapshotPort
    fx_adapter: FrankfurterAdapter | None
    eur_pricer: EurPricer


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
        experiment_root: Directory holding :data:`EXPERIMENT_DIR`. Defaults to
            the repository root found from the installed package.
        orderbooks: Depth source override for tests. Defaults to
            :class:`BinanceDepthSnapshotAdapter`.
        fx: FX provider override for tests. Defaults to Frankfurter (USDT
            read as USD).

    Returns:
        A fully wired :class:`Composition` ready to hand to :func:`run`.

    Raises:
        StartupError: The pre-registration or the fee history cannot be read,
            the start capital cannot be recorded, or the ledger's recorded
            trades or fill-quality rows cannot be read or replayed into the
            portfolio. The SQLite connection is closed before raising.
    """
    db_target = db_path if db_path is not None else DEFAULT_DB_PATH
    env_snapshot: Mapping[str, str] = dict(os.environ) if env is None else env
    pre_registration, fee_rows = _load_experiment(experiment_root)

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
        recorded_seed = ledger.capital_seed(strategy_id)
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
        connection.close()
        raise StartupError(f"cannot read the fill-quality rows: {exc.msg}") from exc
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
    """Close the feed and the depth source, then FX, Slack, Telegram, SQLite last."""
    try:
        await c.feed.aclose()
    except Exception as exc:
        _log.error("serve.close.feed_failed", exc_info=exc)
    try:
        await c.orderbooks.aclose()
    except Exception as exc:
        _log.error("serve.close.orderbooks_failed", exc_info=exc)
    with contextlib.ExitStack() as stack:
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
        warm = list(order_for_delivery(warm))
        cursors = self._load_cursors(warm)
        for pair, strategy in c.strategies.items():
            seeded = [
                b for b in warm if b.pair == pair and b.close_time <= cursors[(b.pair, b.interval)]
            ]
            strategy.seed(seeded, open_position=self._restore_position(pair, cursors))
        for bar in warm:
            if bar.close_time <= cursors[(bar.pair, bar.interval)]:
                c.executor.observe(bar)
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
        c.executor.observe(bar)
        signal_ = c.strategies[bar.pair].on_bar(bar, c.tracker.state())
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
            if is_new:
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
        # The console reads the portfolio from the ledger, not from this process (#387).
        c.ledger.record_portfolio_mark(
            PortfolioMark(
                snapshot=c.tracker.current,
                pair=bar.pair,
                interval=bar.interval,
                bar_close_time=bar.close_time,
            )
        )
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

    async def _execute(self, signal_: StrategySignal, bar: Bar, correlation_id: str) -> None:
        """Simulate the signal's order and log the outcome, never its P&L (D-7)."""
        result = await self.c.executor.execute(signal_, bar, correlation_id=correlation_id)
        _log_execution(bar.pair, signal_.kind, result)

    async def _notify(self, event: SignalEmitted) -> None:
        """Fan the event out in a worker thread; a channel outage is logged, not fatal."""
        try:
            await asyncio.to_thread(self.c.signal_notifier.dispatch, event)
        except EventDispatchError as exc:
            _log.error("serve.notify.failed", exc_info=exc)
            for failure in exc.failures:
                if isinstance(failure, Exception):
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
