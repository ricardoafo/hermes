"""Tests for the ``hermes serve`` composition root and bar loop.

Verifies:

- ``build_composition`` wires each field of :class:`Composition` to the
  expected concrete adapter, one strategy instance per pair, and subscribes
  the ledger to ``SignalEmitted`` and ``BarProcessed``;
- ``check_startup`` refuses a roster, universe, interval set or prod
  credential set the shadow loop cannot run safely;
- ``SHADOW_UNIVERSE`` mirrors pre-registration §3;
- the loop skips a repeated bar, raises on a skipped one and seeds warm-up
  history in delivery order (daily bar before the 4h bar at equal close);
- every runtime failure makes ``run`` return 1, never 2 or 3;
- ``install_signal_handlers`` flips the shutdown event on SIGTERM (skipped on
  Windows, where ``os.kill`` on the pytest process is unsafe).

Reference: ``openspec/changes/309-serve-bar-loop/change.md`` D-1, D-5, D-7, D-9.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import signal
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.adapters.clock.wall import WallClock
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.market_data.binance_live_klines import BinanceLiveKlinesAdapter
from hermes.adapters.notifier.slack_adapter import SlackAdapter
from hermes.adapters.notifier.telegram_adapter import TelegramAdapter
from hermes.application.calibration.certification_gates import CertificationRecord
from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.errors import ExchangeError, LedgerError
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.event_bus import EventBus
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import delivery_key
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.loader import load_config
from hermes.infrastructure.config.models import StrategiesConfig
from hermes.infrastructure.config.root import HermesConfig
from tests.support.serve_loop import (
    BASE,
    BTC,
    FakeFeed,
    build,
    d1_bar,
    events,
    h4_bar,
    history,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TELEGRAM_ENV = {
    "HERMES_TELEGRAM__BOT_TOKEN": "123:fake",  # gitleaks:allow
    "HERMES_TELEGRAM__CHAT_ID": "1",
}
_SLACK_ENV = {
    "HERMES_SLACK__BOT_TOKEN": "xoxb-fake",  # gitleaks:allow
    "HERMES_SLACK__DEFAULT_CHANNEL": "C1",
}


@pytest.fixture
def shadow_config() -> HermesConfig:
    """Load the shipped ``config/`` directory as a dev-tier snapshot.

    Uses an empty env dict so no ambient shell variable can flip the
    tier or bleed credentials into the composition.
    """
    return load_config(_PROJECT_ROOT / "config", env={})


class TestBuildComposition:
    """`build_composition` wires each port to the expected adapter class."""

    def test_build_composition_wires_expected_ports_for_shadow_config(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Every field of Composition is an instance of the expected concrete adapter."""
        composition = serve_module.build_composition(
            shadow_config, db_path=tmp_path / "test.sqlite3", env={}
        )
        try:
            assert isinstance(composition.ledger, SqliteLedger)
            assert isinstance(composition.clock, WallClock)
            assert isinstance(composition.feed, BinanceLiveKlinesAdapter)
            assert isinstance(composition.event_bus, EventBus)
            assert (composition.telegram, composition.slack) == (None, None)
        finally:
            asyncio.run(composition.feed.aclose())
            composition.sqlite_connection.close()

    def test_one_distinct_swing_strategy_per_pair_sharing_one_strategy_id(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Three pairs get three separate adapters, all under swing-donchian-4h (D-7)."""
        composition = serve_module.build_composition(
            shadow_config, db_path=tmp_path / "test.sqlite3", env={}, feed=FakeFeed()
        )
        try:
            strategies = list(composition.strategies.values())
            assert (
                sorted(str(p) for p in composition.strategies),
                len({id(s) for s in strategies}),
                all(isinstance(s, SwingStrategyAdapter) for s in strategies),
                composition.strategy_id.value,
            ) == (["BTC/USDT", "ETH/USDT", "SOL/USDT"], 3, True, "swing-donchian-4h")
        finally:
            composition.sqlite_connection.close()

    def test_ledger_is_subscribed_to_signals_and_cursor_events(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Both loop events have exactly one subscriber: the ledger."""
        composition = serve_module.build_composition(
            shadow_config, db_path=tmp_path / "test.sqlite3", env={}, feed=FakeFeed()
        )
        try:
            counts = (
                composition.event_bus.subscriber_count(SignalEmitted),
                composition.event_bus.subscriber_count(BarProcessed),
            )
        finally:
            composition.sqlite_connection.close()
        assert counts == (1, 1)

    def test_credentials_in_env_wire_both_channels(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Telegram and Slack credentials in the env snapshot produce both adapters."""
        composition = serve_module.build_composition(
            shadow_config,
            db_path=tmp_path / "test.sqlite3",
            env={**_TELEGRAM_ENV, **_SLACK_ENV},
            feed=FakeFeed(),
        )
        try:
            assert isinstance(composition.telegram, TelegramAdapter)
            assert isinstance(composition.slack, SlackAdapter)
        finally:
            assert composition.telegram is not None
            assert composition.slack is not None
            composition.telegram.close()
            composition.slack.close()
            composition.sqlite_connection.close()


class TestMainnetNeedsLiveApproval:
    """`allow_mainnet` starts only for a LIVE_APPROVED strategy (#355, ADR-014)."""

    @staticmethod
    def _mainnet(shadow_config: HermesConfig) -> HermesConfig:
        return shadow_config.model_copy(update={"env": "prod", "allow_mainnet": True})

    @staticmethod
    def _store_with(
        tmp_path: Path, state: CertificationState, fingerprint: str
    ) -> JsonCertificationStore:
        store = JsonCertificationStore(tmp_path)
        store.save(
            CertificationRecord(
                serve_module.SHADOW_STRATEGY_NAME, state, parameter_fingerprint=fingerprint
            )
        )
        return store

    # SEV-1: no real-capital start without a certification store (ADR-014 decision 2)
    def test_refused_without_a_store(self, shadow_config: HermesConfig) -> None:
        """Mainnet with no store configured is refused."""
        with pytest.raises(serve_module.StartupError, match="no certification store"):
            serve_module.check_startup(self._mainnet(shadow_config), _TELEGRAM_ENV)

    # SEV-1: no real-capital start for a strategy that has no record
    def test_refused_without_a_record(self, shadow_config: HermesConfig, tmp_path: Path) -> None:
        """An empty store means the strategy was never certified."""
        with pytest.raises(serve_module.StartupError, match="no certification record"):
            serve_module.check_startup(
                self._mainnet(shadow_config),
                _TELEGRAM_ENV,
                certification_store=JsonCertificationStore(tmp_path),
            )

    # SEV-1: OOS_CERTIFIED without the operator's sign-off is not enough for real capital
    def test_refused_when_only_oos_certified(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """OOS_CERTIFIED lacks the operator sign-off, so mainnet is refused."""
        config = self._mainnet(shadow_config)
        fingerprint = serve_module.parameter_fingerprint_for(config)
        store = self._store_with(tmp_path, CertificationState.OOS_CERTIFIED, fingerprint)
        with pytest.raises(serve_module.StartupError, match="not LIVE_APPROVED"):
            serve_module.check_startup(config, _TELEGRAM_ENV, certification_store=store)

    # SEV-1: D-8 guardrail — changed parameters lose live approval
    def test_refused_when_parameters_changed(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """A LIVE_APPROVED record for other parameters is refused."""
        store = self._store_with(tmp_path, CertificationState.LIVE_APPROVED, "0" * 64)
        with pytest.raises(serve_module.StartupError, match="parameters changed"):
            serve_module.check_startup(
                self._mainnet(shadow_config), _TELEGRAM_ENV, certification_store=store
            )

    def test_accepted_when_live_approved(self, shadow_config: HermesConfig, tmp_path: Path) -> None:
        """LIVE_APPROVED with the running parameters starts."""
        config = self._mainnet(shadow_config)
        fingerprint = serve_module.parameter_fingerprint_for(config)
        store = self._store_with(tmp_path, CertificationState.LIVE_APPROVED, fingerprint)
        serve_module.check_startup(config, _TELEGRAM_ENV, certification_store=store)

    def test_shadow_does_not_read_the_store(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Without allow_mainnet a corrupt store is irrelevant: shadow never needs approval."""
        (tmp_path / f"{serve_module.SHADOW_STRATEGY_NAME}.json").write_text("{", encoding="utf-8")
        serve_module.check_startup(
            shadow_config, {}, certification_store=JsonCertificationStore(tmp_path)
        )


def _record_trade(db_path: Path, side: TradeSide, correlation_id: str) -> None:
    """Write one 0.01 BTC fill at 50000 (fee 0.5 USDT) of the shadow strategy to the ledger."""
    pair = Pair(base=Currency.BTC, quote=Currency.USDT)
    connection = sqlite3.connect(db_path)
    try:
        SqliteLedger(connection).append_event(
            TradeExecuted(
                occurred_at=datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
                correlation_id=correlation_id,
                pair=pair,
                side=side,
                qty=Quantity(amount=Decimal("0.01"), asset=Currency.BTC),
                price=Price(amount=Decimal("50000"), pair=pair),
                fee=Money(amount=Decimal("0.5"), currency=Currency.USDT),
                strategy_id=StrategyId("swing-donchian-4h"),
            )
        )
    finally:
        connection.close()


class TestPortfolioRebuiltOnStartup:
    """build_composition restores the portfolio from the ledger's recorded trades."""

    def test_a_recorded_buy_shows_as_an_open_position_and_reduced_cash(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """One BUY of 0.01 BTC at 50000 leaves one position and value seed minus the fee."""
        db_path = tmp_path / "test.sqlite3"
        _record_trade(db_path, TradeSide.BUY, "01J9Z8T3M5BQR2FVKN4XWY6PH1")
        fresh = tmp_path / "fresh.sqlite3"
        baseline = serve_module.build_composition(
            shadow_config, db_path=fresh, env={}, feed=FakeFeed()
        )
        composition = serve_module.build_composition(
            shadow_config, db_path=db_path, env={}, feed=FakeFeed()
        )
        try:
            assert (
                composition.tracker.open_position_count(),
                baseline.tracker.state().current_value - composition.tracker.state().current_value,
            ) == (1, Decimal("0.5"))
        finally:
            composition.sqlite_connection.close()
            baseline.sqlite_connection.close()

    def test_an_unreplayable_ledger_raises_startup_error_naming_the_trade(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """A SELL with no prior BUY stops startup and names the trade's correlation id."""
        db_path = tmp_path / "test.sqlite3"
        _record_trade(db_path, TradeSide.SELL, "01J9Z8T3M5BQR2FVKN4XWY6PH7")
        with pytest.raises(serve_module.StartupError, match="01J9Z8T3M5BQR2FVKN4XWY6PH7"):
            serve_module.build_composition(shadow_config, db_path=db_path, env={}, feed=FakeFeed())

    def test_a_ledger_read_failure_raises_startup_error(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """A schema v1 TradeExecuted row stops startup with StartupError, not LedgerError."""
        db_path = tmp_path / "test.sqlite3"
        _record_trade(db_path, TradeSide.BUY, "01J9Z8T3M5BQR2FVKN4XWY6PH1")
        connection = sqlite3.connect(db_path)
        connection.execute("UPDATE events SET event_schema_version = 1")
        connection.commit()
        connection.close()
        with pytest.raises(serve_module.StartupError, match="schema version 1"):
            serve_module.build_composition(shadow_config, db_path=db_path, env={}, feed=FakeFeed())


class TestCheckStartup:
    """`check_startup` refuses configurations the shadow loop cannot run safely."""

    def test_shipped_dev_config_without_channels_is_accepted(
        self, shadow_config: HermesConfig
    ) -> None:
        """The repo's own config in dev with no credentials passes (D-9)."""
        serve_module.check_startup(shadow_config, {})

    def test_wrong_roster_is_refused(self, shadow_config: HermesConfig) -> None:
        """A roster whose enabled entry is not swing-donchian-4h is a config error."""
        entry = shadow_config.strategies.strategies[0].model_copy(update={"name": "rebalancer"})
        config = shadow_config.model_copy(
            update={"strategies": StrategiesConfig(strategies=[entry])}
        )

        with pytest.raises(serve_module.StartupError, match="enabled strategies"):
            serve_module.check_startup(config, {})

    def test_a_second_enabled_strategy_is_refused(self, shadow_config: HermesConfig) -> None:
        """Enabling a second strategy besides the swing entry is a config error."""
        first = shadow_config.strategies.strategies[0]
        second = first.model_copy(update={"name": "donchian"})
        config = shadow_config.model_copy(
            update={"strategies": StrategiesConfig(strategies=[first, second])}
        )

        with pytest.raises(serve_module.StartupError, match="enabled strategies"):
            serve_module.check_startup(config, {})

    def test_prod_without_any_channel_is_refused(self, shadow_config: HermesConfig) -> None:
        """Prod with neither Telegram nor Slack credentials would alert no one."""
        config = shadow_config.model_copy(update={"env": "prod"})

        with pytest.raises(serve_module.StartupError, match="neither Telegram nor Slack"):
            serve_module.check_startup(config, {})

    @pytest.mark.parametrize("env", [_TELEGRAM_ENV, _SLACK_ENV])
    def test_prod_with_one_channel_is_accepted(
        self, shadow_config: HermesConfig, env: dict[str, str]
    ) -> None:
        """Prod needs only one channel configured."""
        serve_module.check_startup(shadow_config.model_copy(update={"env": "prod"}), env)

    def test_other_pairs_are_refused(self, shadow_config: HermesConfig) -> None:
        """Followed pairs must be the pre-registered three."""
        market = shadow_config.market_data.model_copy(update={"pairs": ["BTC/USDT"]})
        config = shadow_config.model_copy(update={"market_data": market})

        with pytest.raises(serve_module.StartupError, match=r"market_data\.pairs"):
            serve_module.check_startup(config, {})

    def test_missing_daily_interval_is_refused(self, shadow_config: HermesConfig) -> None:
        """The swing strategy never enters without 1d bars, so 4h alone is refused."""
        market = shadow_config.market_data.model_copy(update={"intervals": [KlineInterval.H4]})
        config = shadow_config.model_copy(update={"market_data": market})

        with pytest.raises(serve_module.StartupError, match="intervals"):
            serve_module.check_startup(config, {})


def test_shadow_universe_equals_the_pre_registered_universe() -> None:
    """SHADOW_UNIVERSE is pre-registration §3 once the slash is removed (D-7)."""
    symbols = tuple(pair.replace("/", "") for pair in serve_module.SHADOW_UNIVERSE)

    assert symbols == PreRegistration().universe()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "SIGTERM delivery via os.kill on the running pytest process is unsafe on "
        "Windows and the loop-based add_signal_handler codepath is not available."
    ),
)
class TestInstallSignalHandlers:
    """`install_signal_handlers` flips the shutdown event when SIGTERM arrives."""

    def test_install_signal_handlers_sets_shutdown_on_sigterm(self) -> None:
        """A SIGTERM to self flips the shutdown Event within a short wait."""

        async def _driver() -> bool:
            loop = asyncio.get_running_loop()
            shutdown = asyncio.Event()
            serve_module.install_signal_handlers(loop, shutdown)
            os.kill(os.getpid(), signal.SIGTERM)
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=1.0)
            except TimeoutError:
                return False
            return shutdown.is_set()

        assert asyncio.run(_driver()) is True


class _BrokenFeed(FakeFeed):
    """A feed whose warm-up raises ``exc``."""

    def __init__(self, exc: Exception) -> None:
        """Remember the failure to raise."""
        super().__init__()
        self._exc = exc

    async def warm_up(self, pair: object, interval: object, count: int) -> list[Bar]:
        """Raise the configured failure."""
        raise self._exc


class TestRun:
    """`run` returns an exit code and always releases its resources."""

    def test_run_exits_zero_when_shutdown_is_set_before_start(self, tmp_path: Path) -> None:
        """A pre-set shutdown returns 0 within 1s and closes the SQLite connection."""
        feed = FakeFeed()
        composition = build(tmp_path / "test.sqlite3", feed, BASE)

        async def _driver() -> int:
            shutdown = asyncio.Event()
            shutdown.set()
            return await asyncio.wait_for(serve_module.run(composition, shutdown), timeout=1.0)

        code = asyncio.run(_driver())

        with pytest.raises(sqlite3.ProgrammingError):
            composition.sqlite_connection.execute("SELECT 1")
        assert (code, feed.closed) == (0, True)

    @pytest.mark.parametrize(
        "failure",
        [
            ExchangeError("exchange down"),
            LedgerError(msg="disk full"),
            RuntimeError("bug"),
            serve_module.BarGapError("gap"),
        ],
        ids=lambda exc: type(exc).__name__,
    )
    def test_every_runtime_failure_returns_one_never_two_or_three(
        self, failure: Exception, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whatever escapes the loop maps to exit 1 and is sent to Sentry once."""
        captured: list[Exception] = []
        monkeypatch.setattr(serve_module.sentry_integration, "capture", captured.append)
        feed = _BrokenFeed(failure)
        composition = build(tmp_path / "test.sqlite3", feed, BASE)

        code = asyncio.run(serve_module.run(composition, asyncio.Event()))

        assert (code, captured, feed.closed) == (1, [failure], True)


class _RecordingStrategy:
    """StrategyPort fake that records what it is seeded with."""

    def __init__(self) -> None:
        """Start with nothing seeded."""
        self.seeded: list[Bar] = []
        self.position: OpenPosition | None = None

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Never signal."""
        return None

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Record the seed."""
        self.seeded = list(bars)
        self.position = open_position


class TestBarLoop:
    """The loop's per-bar rules: contiguity and warm-up order."""

    def _loop(self, tmp_path: Path) -> tuple[serve_module.Composition, serve_module._BarLoop]:
        """Return a composition and a fresh loop over it."""
        composition = build(tmp_path / "test.sqlite3", FakeFeed(), BASE)
        return composition, serve_module._BarLoop(composition)

    def test_in_order_bars_each_write_a_cursor_row(self, tmp_path: Path) -> None:
        """Contiguous bars pass the check and each moves the cursor."""
        composition, loop = self._loop(tmp_path)
        asyncio.run(loop.handle(h4_bar(0)))
        asyncio.run(loop.handle(h4_bar(1)))
        composition.sqlite_connection.close()

        assert len(events(tmp_path / "test.sqlite3", "BarProcessed")) == 2

    def test_a_repeated_bar_is_skipped(self, tmp_path: Path) -> None:
        """Handing the same bar twice writes one cursor row."""
        composition, loop = self._loop(tmp_path)
        asyncio.run(loop.handle(h4_bar(0)))
        asyncio.run(loop.handle(h4_bar(0)))
        composition.sqlite_connection.close()

        assert len(events(tmp_path / "test.sqlite3", "BarProcessed")) == 1

    def test_a_skipped_bar_raises_bar_gap_error(self, tmp_path: Path) -> None:
        """A bar that does not directly follow the last one raises BarGapError."""
        composition, loop = self._loop(tmp_path)
        asyncio.run(loop.handle(h4_bar(0)))

        with pytest.raises(serve_module.BarGapError, match="expected the bar opening at"):
            asyncio.run(loop.handle(h4_bar(2)))
        composition.sqlite_connection.close()

    def test_each_stream_has_its_own_contiguity(self, tmp_path: Path) -> None:
        """A 1d bar does not count as the 4h stream's previous bar."""
        composition, loop = self._loop(tmp_path)
        asyncio.run(loop.handle(d1_bar(0)))
        asyncio.run(loop.handle(h4_bar(0)))
        composition.sqlite_connection.close()

        assert len(events(tmp_path / "test.sqlite3", "BarProcessed")) == 2

    def test_warm_up_seeds_in_delivery_order_daily_bar_first_at_equal_close(
        self, tmp_path: Path
    ) -> None:
        """Seed order equals delivery_key order; the 1d bar precedes the 4h bar closing with it."""
        day_close = BASE + timedelta(days=1) - timedelta(milliseconds=1)
        day_end_h4 = dataclasses.replace(
            h4_bar(0), open_time=BASE + timedelta(hours=20), close_time=day_close
        )
        daily = dataclasses.replace(d1_bar(0), open_time=BASE, close_time=day_close)
        earlier = dataclasses.replace(
            h4_bar(0),
            open_time=BASE + timedelta(hours=16),
            close_time=BASE + timedelta(hours=20) - timedelta(milliseconds=1),
        )
        feed = FakeFeed(warm=[day_end_h4, earlier, daily])
        recorder = _RecordingStrategy()
        composition = dataclasses.replace(
            build(tmp_path / "test.sqlite3", feed, BASE), strategies={BTC: recorder}
        )

        asyncio.run(serve_module._BarLoop(composition).warm_up_and_catch_up(asyncio.Event()))
        composition.sqlite_connection.close()

        assert [(b.interval, b.close_time) for b in recorder.seeded] == [
            (KlineInterval.H4, earlier.close_time),
            (KlineInterval.D1, daily.close_time),
            (KlineInterval.H4, day_end_h4.close_time),
        ]
        assert recorder.seeded == sorted(recorder.seeded, key=delivery_key)

    def test_first_boot_seeds_everything_flat(self, tmp_path: Path) -> None:
        """First boot seeds the whole warm-up set with no open position."""
        feed = FakeFeed(warm=history(10))
        composition = dataclasses.replace(
            build(tmp_path / "test.sqlite3", feed, datetime(2030, 1, 1, tzinfo=UTC)),
            strategies={BTC: (recorder := _RecordingStrategy())},
        )

        asyncio.run(serve_module._BarLoop(composition).warm_up_and_catch_up(asyncio.Event()))
        composition.sqlite_connection.close()

        assert (len(recorder.seeded), recorder.position) == (23, None)
