"""Tests for the ``hermes serve`` composition root and bar loop.

Verifies:

- ``build_composition`` wires each field of :class:`Composition` to the
  expected concrete adapter, one strategy instance per pair, and subscribes
  the ledger to ``SignalEmitted`` and ``BarProcessed``, and ``AnomalyDetected``
  to the ledger only (record-only, #136);
- ``check_startup`` refuses a roster, universe, interval set or prod
  credential set the shadow loop cannot run safely, and a pre-registered
  strategy whose exit pricing is not its own (v2-strategy-config D-3);
- the shadow executor prices with the enabled roster entry's execution
  model, v1's stop-order or v2's bar close (v2-strategy-config D-1);
- ``SHADOW_UNIVERSE`` mirrors pre-registration §3;
- the loop skips a repeated bar, raises on a skipped one and seeds warm-up
  history in delivery order (daily bar before the 4h bar at equal close);
- every runtime failure makes ``run`` return 1, never 2 or 3;
- ``install_signal_handlers`` flips the shutdown event on SIGTERM (skipped on
  Windows, where ``os.kill`` on the pytest process is unsafe).

Reference: ``openspec/archive/309-serve-bar-loop/change.md`` D-1, D-5, D-7, D-9.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import signal
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from structlog.testing import capture_logs

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.adapters.clock.remote import TEST_CLOCK_URL_ENV, RemoteClock
from hermes.adapters.clock.wall import WallClock
from hermes.adapters.endpoint import EndpointConfigError
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.market_data.binance_depth_snapshot import BinanceDepthSnapshotAdapter
from hermes.adapters.market_data.binance_live_klines import BinanceLiveKlinesAdapter
from hermes.adapters.notifier.slack_adapter import SlackAdapter
from hermes.adapters.notifier.telegram_adapter import TelegramAdapter
from hermes.application.backtest.replay import ExecutionModel as ReplayExecutionModel
from hermes.application.execution.prereg_book import ExecutionModel, ExitPricing
from hermes.domain.calibration.certification import CertificationRecord, CertificationState
from hermes.domain.errors import ExchangeError, InfraError, LedgerError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.event_bus import EventBus
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import delivery_key
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, SignalSide, StrategySignal
from hermes.domain.strategy.swing.adapter import EntryCheck, SwingStrategyAdapter
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
from hermes.infrastructure.project_root import load_pre_registration
from tests.support.accepted import assert_accepted
from tests.support.anomaly_rows import recorded_anomalies
from tests.support.paths import REPO_ROOT
from tests.support.serve_loop import (
    BASE,
    BTC,
    FakeFeed,
    RecordingChannel,
    UnavailableBooks,
    build,
    d1_bar,
    events,
    h4_bar,
    history,
)

_TELEGRAM_ENV = {
    "HERMES_TELEGRAM__BOT_TOKEN": "123:fake",  # gitleaks:allow
    "HERMES_TELEGRAM__CHAT_ID": "1",
}
_SLACK_ENV = {
    "HERMES_SLACK__BOT_TOKEN": "xoxb-fake",  # gitleaks:allow
    "HERMES_SLACK__DEFAULT_CHANNEL": "C1",
}


_V2_ENABLED_ENV = {
    "HERMES_STRATEGIES__STRATEGIES__0__ENABLED": "false",
    "HERMES_STRATEGIES__STRATEGIES__1__ENABLED": "true",
}
"""Swap the shipped roster to v2: v1 (entry 0) off, swing-mvp1-v2 (entry 1) on."""


def _with_pricing(config: HermesConfig, index: int, pricing: ExitPricing) -> HermesConfig:
    """Return ``config`` with roster entry ``index`` priced ``pricing``."""
    entries = list(config.strategies.strategies)
    entries[index] = entries[index].model_copy(update={"exit_pricing": pricing})
    return config.model_copy(update={"strategies": StrategiesConfig(strategies=entries)})


@pytest.fixture
def v2_config() -> HermesConfig:
    """Load the shipped ``config/`` with swing-mvp1-v2 enabled in place of v1."""
    return load_config(REPO_ROOT / "config", env=_V2_ENABLED_ENV)


@pytest.fixture
def shadow_config() -> HermesConfig:
    """Load the shipped ``config/`` directory as a dev-tier snapshot.

    Uses an empty env dict so no ambient shell variable can flip the
    tier or bleed credentials into the composition.
    """
    return load_config(REPO_ROOT / "config", env={})


class TestBuildComposition:
    """`build_composition` wires each port to the expected adapter class."""

    def test_build_composition_wires_expected_ports_for_shadow_config(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Every field of Composition is an instance of the expected concrete adapter."""
        composition = serve_module.build_composition(
            shadow_config,
            db_path=tmp_path / "test.sqlite3",
            env={},
            new_ledger=True,
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

    def test_a_test_clock_in_the_env_becomes_serves_clock(
        self, shadow_config: HermesConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HERMES_TEST_CLOCK_URL puts serve on the test clock (#972).

        The clock's transport is mocked here; the real socket seam is covered by
        tests/integration/lower_env/test_installed_release.py.
        """
        scripted = datetime(2024, 10, 28, tzinfo=UTC)
        test_clock = RemoteClock(
            "http://127.0.0.1:9/_fake/clock",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"now": scripted.isoformat()})
            ),
        )
        monkeypatch.setattr(
            serve_module,
            "remote_clock_from_env",
            lambda env: test_clock if env.get(TEST_CLOCK_URL_ENV) else None,
        )

        composition = serve_module.build_composition(
            shadow_config,
            db_path=tmp_path / "test.sqlite3",
            env={TEST_CLOCK_URL_ENV: "http://127.0.0.1:9/_fake/clock"},
            new_ledger=True,
        )
        try:
            assert (composition.clock is test_clock, composition.clock.now()) == (True, scripted)
        finally:
            asyncio.run(composition.feed.aclose())
            composition.sqlite_connection.close()

    def test_one_distinct_swing_strategy_per_pair_sharing_one_strategy_id(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Three pairs get three separate adapters, all under swing-donchian-4h (D-7)."""
        composition = serve_module.build_composition(
            shadow_config,
            db_path=tmp_path / "test.sqlite3",
            env={},
            feed=FakeFeed(),
            new_ledger=True,
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
            shadow_config,
            db_path=tmp_path / "test.sqlite3",
            env={},
            feed=FakeFeed(),
            new_ledger=True,
        )
        try:
            counts = (
                composition.event_bus.subscriber_count(SignalEmitted),
                composition.event_bus.subscriber_count(BarProcessed),
            )
        finally:
            composition.sqlite_connection.close()
        assert counts == (1, 1)

    def test_an_anomaly_has_exactly_one_subscriber_the_ledger(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """#136: one AnomalyDetected subscriber, and publishing one writes one ledger row."""
        db = tmp_path / "test.sqlite3"
        composition = serve_module.build_composition(
            shadow_config,
            db_path=db,
            env={},
            feed=FakeFeed(),
            new_ledger=True,
        )
        try:
            count = composition.event_bus.subscriber_count(AnomalyDetected)
            composition.event_bus.publish(
                AnomalyDetected(
                    occurred_at=datetime(2026, 10, 6, tzinfo=UTC),
                    correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
                    source="test.injected",
                    severity=AnomalySeverity.WARNING,
                    reason="injected",
                )
            )
        finally:
            composition.sqlite_connection.close()
        assert (count, [r["source"] for r in recorded_anomalies(db)]) == (1, ["test.injected"])

    def test_credentials_in_env_wire_both_channels(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Telegram and Slack credentials in the env snapshot produce both adapters."""
        composition = serve_module.build_composition(
            shadow_config,
            db_path=tmp_path / "test.sqlite3",
            env={**_TELEGRAM_ENV, **_SLACK_ENV},
            feed=FakeFeed(),
            new_ledger=True,
        )
        try:
            assert (type(composition.telegram), type(composition.slack)) == (
                TelegramAdapter,
                SlackAdapter,
            )
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
        assert_accepted(
            serve_module.check_startup, config, _TELEGRAM_ENV, certification_store=store
        )

    # SEV-1: a v1 live approval must never unlock real capital for v2 (ADR-014)
    def test_a_v1_approval_does_not_start_v2_on_mainnet(
        self, v2_config: HermesConfig, tmp_path: Path
    ) -> None:
        """With v2 enabled the approval is read for swing-mvp1-v2, which has no record."""
        config = self._mainnet(v2_config)
        fingerprint = serve_module.parameter_fingerprint_for(config)
        store = self._store_with(tmp_path, CertificationState.LIVE_APPROVED, fingerprint)

        with pytest.raises(
            serve_module.StartupError, match="'swing-mvp1-v2' but no certification record"
        ):
            serve_module.check_startup(config, _TELEGRAM_ENV, certification_store=store)

    def test_shadow_does_not_read_the_store(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Without allow_mainnet a corrupt store is irrelevant: shadow never needs approval."""
        (tmp_path / f"{serve_module.SHADOW_STRATEGY_NAME}.json").write_text("{", encoding="utf-8")
        assert_accepted(
            serve_module.check_startup,
            shadow_config,
            {},
            certification_store=JsonCertificationStore(tmp_path),
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
            shadow_config,
            db_path=fresh,
            env={},
            feed=FakeFeed(),
            new_ledger=True,
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

    def test_a_file_that_is_not_a_ledger_raises_startup_error_naming_it(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """A corrupt ledger stops startup with a message naming the file, not a raw sqlite error."""
        db_path = tmp_path / "test.sqlite3"
        db_path.write_bytes(b"not a database " * 300)
        with pytest.raises(serve_module.StartupError, match="cannot be opened as a SQLite ledger"):
            serve_module.build_composition(shadow_config, db_path=db_path, env={}, feed=FakeFeed())

    def test_a_ledger_a_newer_release_migrated_raises_startup_error_naming_the_migration(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Old code refuses a schema it does not know instead of writing to it (issue #856)."""
        db_path = tmp_path / "test.sqlite3"
        conn = sqlite3.connect(db_path)
        try:
            SqliteLedger(conn)
            conn.execute("INSERT INTO schema_migrations (filename) VALUES ('9999_newer.sql')")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(serve_module.StartupError, match=r"9999_newer\.sql"):
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
        assert_accepted(serve_module.check_startup, shadow_config, {})

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
        assert_accepted(
            serve_module.check_startup, shadow_config.model_copy(update={"env": "prod"}), env
        )

    def test_v2_enabled_alone_with_its_close_based_pricing_is_accepted(
        self, v2_config: HermesConfig
    ) -> None:
        """The shipped v2 entry, enabled in place of v1, may run in shadow."""
        assert_accepted(serve_module.check_startup, v2_config, {})

    def test_v2_priced_as_a_stop_order_is_refused(self, v2_config: HermesConfig) -> None:
        """v2 priced like v1 would trade and measure drift off its signed §6.2 price."""
        config = _with_pricing(v2_config, 1, ExitPricing.STOP_ORDER)

        with pytest.raises(
            serve_module.StartupError, match=r"swing-mvp1-v2 must price its exits bar_close"
        ):
            serve_module.check_startup(config, {})

    def test_v1_priced_at_the_bar_close_is_refused(self, shadow_config: HermesConfig) -> None:
        """v1 priced at the bar close would trade off its signed stop-market price."""
        config = _with_pricing(shadow_config, 0, ExitPricing.BAR_CLOSE)

        with pytest.raises(
            serve_module.StartupError, match=r"swing-donchian-4h must price its exits stop_order"
        ):
            serve_module.check_startup(config, {})

    def test_v1_and_v2_enabled_together_are_refused(self, shadow_config: HermesConfig) -> None:
        """Serve runs one strategy at a time: both pre-registered entries enabled is refused."""
        v1, v2, *_ = shadow_config.strategies.strategies
        both = [v1, v2.model_copy(update={"enabled": True})]
        config = shadow_config.model_copy(update={"strategies": StrategiesConfig(strategies=both)})

        with pytest.raises(serve_module.StartupError, match="enabled strategies"):
            serve_module.check_startup(config, {})

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

    assert symbols == load_pre_registration().universe()


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

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Record the restored position."""
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

        assert _cursor(tmp_path / "test.sqlite3") == [
            ("4h", "2024-01-16T03:59:59.999000+00:00"),
            ("4h", "2024-01-16T07:59:59.999000+00:00"),
        ]

    def test_a_repeated_bar_is_skipped(self, tmp_path: Path) -> None:
        """Handing the same bar twice writes one cursor row."""
        composition, loop = self._loop(tmp_path)
        asyncio.run(loop.handle(h4_bar(0)))
        asyncio.run(loop.handle(h4_bar(0)))
        composition.sqlite_connection.close()

        assert _cursor(tmp_path / "test.sqlite3") == [("4h", "2024-01-16T03:59:59.999000+00:00")]

    def test_a_skipped_bar_raises_bar_gap_error(self, tmp_path: Path) -> None:
        """A bar that does not directly follow the last one raises BarGapError."""
        composition, loop = self._loop(tmp_path)
        asyncio.run(loop.handle(h4_bar(0)))

        with pytest.raises(serve_module.BarGapError, match="expected the bar opening at"):
            asyncio.run(loop.handle(h4_bar(2)))
        composition.sqlite_connection.close()

    def _catching_up_with_telegram(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, telegram: RecordingChannel
    ) -> tuple[serve_module.Composition, serve_module._BarLoop, list[Exception]]:
        """Return a loop in its boot catch-up with ``telegram`` wired and Sentry recorded."""
        sentry: list[Exception] = []
        monkeypatch.setattr(serve_module.sentry_integration, "capture", sentry.append)
        composition = dataclasses.replace(
            build(tmp_path / "test.sqlite3", FakeFeed(), BASE),
            telegram=telegram,  # type: ignore[arg-type]
        )
        loop = serve_module._BarLoop(composition, catching_up=True)
        return composition, loop, sentry

    def test_a_gap_in_the_boot_catch_up_is_handled_past_and_alerted_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bar still missing after a restart cannot crash-loop serve; Telegram and Sentry hear."""
        telegram = RecordingChannel()
        composition, loop, sentry = self._catching_up_with_telegram(tmp_path, monkeypatch, telegram)
        asyncio.run(loop.handle(h4_bar(0)))
        asyncio.run(loop.handle(h4_bar(2)))
        composition.sqlite_connection.close()

        assert (
            len(events(tmp_path / "test.sqlite3", "BarProcessed")),
            [(type(e), getattr(e, "source", None)) for e in telegram.seen],
            [type(e) for e in sentry],
        ) == (2, [(AnomalyDetected, "serve.bar_gap")], [serve_module.BarGapError])

    def test_a_failed_gap_alert_does_not_stop_the_boot_catch_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Telegram being down is logged; the bar after the gap is still handled."""
        telegram = RecordingChannel(raises=InfraError("telegram down"))
        composition, loop, _ = self._catching_up_with_telegram(tmp_path, monkeypatch, telegram)
        asyncio.run(loop.handle(h4_bar(0)))
        with capture_logs() as logs:
            asyncio.run(loop.handle(h4_bar(2)))
        composition.sqlite_connection.close()

        assert (
            len(events(tmp_path / "test.sqlite3", "BarProcessed")),
            [line["event"] for line in logs if line["log_level"] == "error"],
        ) == (2, ["serve.bar.gap_passed_over", "serve.bar.gap_alert_failed"])

    def test_each_stream_has_its_own_contiguity(self, tmp_path: Path) -> None:
        """A 1d bar does not count as the 4h stream's previous bar."""
        composition, loop = self._loop(tmp_path)
        asyncio.run(loop.handle(d1_bar(0)))
        asyncio.run(loop.handle(h4_bar(0)))
        composition.sqlite_connection.close()

        assert _cursor(tmp_path / "test.sqlite3") == [
            ("1d", "2024-01-01T23:59:59.999000+00:00"),
            ("4h", "2024-01-16T03:59:59.999000+00:00"),
        ]

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


class _EntryOnceStrategy(_RecordingStrategy):
    """Strategy fake that emits one long entry on the first bar it sees."""

    def __init__(self) -> None:
        """Start without having signalled."""
        super().__init__()
        self.fired = False

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Enter on the first bar, stay quiet afterwards."""
        if self.fired:
            return None
        self.fired = True
        return StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=bar.close,
            stop_price=bar.close - Decimal("10"),
            atr_context=Decimal("5"),
            signal_time=bar.close_time,
        )


def _cursor(db_path: Path) -> list[tuple[str, str]]:
    """Return ``(interval, occurred_at)`` of every ``BarProcessed`` cursor row, oldest first."""
    return [
        (json.loads(row["payload_json"])["interval"], row["occurred_at"])
        for row in events(db_path, "BarProcessed")
    ]


def _processed(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return the ``serve.bar.processed`` entries without structlog's own keys."""
    return [
        {k: v for k, v in entry.items() if k not in {"event", "log_level"}}
        for entry in logs
        if entry["event"] == "serve.bar.processed"
    ]


class TestBarLogging:
    """Every handled bar leaves a journal line, so a quiet market reads as alive (#371)."""

    def _settle(self, composition: serve_module.Composition) -> timedelta:
        """Return the configured closed-bar settle delay."""
        return timedelta(seconds=composition.config.market_data.settle_delay_seconds)

    def test_two_bars_log_two_processed_lines_with_exact_fields(self, tmp_path: Path) -> None:
        """Each bar logs pair, interval, open/close time, no signal and the next bar's due time."""
        composition = build(tmp_path / "test.sqlite3", FakeFeed(), BASE)
        composition = dataclasses.replace(composition, strategies={BTC: _RecordingStrategy()})
        loop = serve_module._BarLoop(composition)
        first, second = h4_bar(0), h4_bar(1)
        settle = self._settle(composition)

        with capture_logs() as logs:
            asyncio.run(loop.handle(first))
            asyncio.run(loop.handle(second))
        composition.sqlite_connection.close()

        assert _processed(logs) == [
            {
                "pair": "BTC/USDT",
                "interval": "4h",
                "open_time": bar.open_time.isoformat(),
                "close_time": bar.close_time.isoformat(),
                "signal": "none",
                "next_due": (bar.open_time + timedelta(hours=8) + settle).isoformat(),
            }
            for bar in (first, second)
        ]

    def test_a_signal_bar_logs_the_signal_kind(self, tmp_path: Path) -> None:
        """An entry on the bar is named in its line; the operator sees it without the ledger."""
        composition = build(tmp_path / "test.sqlite3", FakeFeed(), BASE)
        composition = dataclasses.replace(composition, strategies={BTC: _EntryOnceStrategy()})
        loop = serve_module._BarLoop(composition)

        with capture_logs() as logs:
            asyncio.run(loop.handle(h4_bar(0)))
            asyncio.run(loop.handle(h4_bar(1)))
        composition.sqlite_connection.close()

        assert [line["signal"] for line in _processed(logs)] == ["entry", "none"]

    def test_a_4h_entry_check_logs_each_condition_as_yes_or_no(self, tmp_path: Path) -> None:
        """Each §5.1 condition is logged under its own field, as yes or no (#656)."""
        composition = build(tmp_path / "test.sqlite3", FakeFeed(), BASE)
        strategy = _FixedEntryCheckStrategy(EntryCheck(breakout=True, momentum=False, regime=True))
        composition = dataclasses.replace(composition, strategies={BTC: strategy})
        loop = serve_module._BarLoop(composition)

        with capture_logs() as logs:
            asyncio.run(loop.handle(h4_bar(0)))
        composition.sqlite_connection.close()

        line = _processed(logs)[0]
        assert {k: line[k] for k in line if k.startswith("entry_")} == {
            "entry_breakout": "yes",
            "entry_momentum": "no",
            "entry_regime": "yes",
        }

    def test_a_bar_without_an_entry_check_logs_no_condition_fields(self, tmp_path: Path) -> None:
        """A daily bar is no entry check, so its line carries no stale yes/no (#656)."""
        composition = build(tmp_path / "test.sqlite3", FakeFeed(), BASE)
        composition = dataclasses.replace(composition, strategies={BTC: SwingStrategyAdapter()})
        loop = serve_module._BarLoop(composition)

        with capture_logs() as logs:
            asyncio.run(loop.handle(h4_bar(0)))
            asyncio.run(loop.handle(d1_bar(0)))
        composition.sqlite_connection.close()

        h4_line, d1_line = _processed(logs)
        assert [k for k in h4_line if k.startswith("entry_")] == [
            "entry_breakout",
            "entry_momentum",
            "entry_regime",
        ]
        assert [k for k in d1_line if k.startswith("entry_")] == []

    def test_a_repeated_bar_logs_nothing(self, tmp_path: Path) -> None:
        """A duplicate is skipped, so it must not read as a second processed bar."""
        composition = build(tmp_path / "test.sqlite3", FakeFeed(), BASE)
        loop = serve_module._BarLoop(composition)

        with capture_logs() as logs:
            asyncio.run(loop.handle(h4_bar(0)))
            asyncio.run(loop.handle(h4_bar(0)))
        composition.sqlite_connection.close()

        assert [entry["interval"] for entry in _processed(logs)] == ["4h"]

    def test_next_due_lists_every_stream_after_catch_up(self, tmp_path: Path) -> None:
        """Going live logs when each stream's next bar should arrive."""
        feed = FakeFeed(warm=history(10))
        composition = dataclasses.replace(
            build(tmp_path / "test.sqlite3", feed, datetime(2030, 1, 1, tzinfo=UTC)),
            strategies={BTC: _RecordingStrategy()},
        )
        loop = serve_module._BarLoop(composition)
        settle = self._settle(composition)
        asyncio.run(loop.warm_up_and_catch_up(asyncio.Event()))

        with capture_logs() as logs:
            loop.log_next_due()
        composition.sqlite_connection.close()

        assert [e["streams"] for e in logs if e["event"] == "serve.bar.next_due"] == [
            {
                "BTC/USDT 1d": (d1_bar(11).open_time + timedelta(days=2) + settle).isoformat(),
                "BTC/USDT 4h": (h4_bar(10).open_time + timedelta(hours=8) + settle).isoformat(),
            }
        ]


class TestFillKernelWiring:
    """serve wires the shared fill kernel and its experiment files (#353 D-1, D-6)."""

    def test_default_depth_source_is_the_keyless_binance_snapshot_adapter(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Without an override the executor walks Binance public depth."""
        composition = serve_module.build_composition(
            shadow_config,
            db_path=tmp_path / "test.sqlite3",
            env={},
            feed=FakeFeed(),
            new_ledger=True,
        )
        try:
            assert type(composition.orderbooks) is BinanceDepthSnapshotAdapter
        finally:
            asyncio.run(composition.orderbooks.aclose())
            composition.sqlite_connection.close()

    def test_executor_falls_back_to_the_backtest_execution_model(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """One §6.2 implementation: serve's fallback book is the backtest's class."""
        composition = build(tmp_path / "test.sqlite3", FakeFeed(), BASE)
        composition.sqlite_connection.close()

        assert type(composition.executor.model) is ReplayExecutionModel

    def test_the_v1_executor_prices_stop_exits_as_a_stop_order(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """The shipped roster runs v1, so serve's §6.2 model is v1's, unchanged."""
        composition = build(tmp_path / "test.sqlite3", FakeFeed(), BASE)
        composition.sqlite_connection.close()

        assert composition.executor.model == ExecutionModel(exit_pricing=ExitPricing.STOP_ORDER)

    def test_the_v2_executor_prices_every_exit_at_the_bar_close(
        self, v2_config: HermesConfig, tmp_path: Path
    ) -> None:
        """With v2 enabled serve's §6.2 model is the one the v2 backtest prices with."""
        composition = serve_module.build_composition(
            v2_config,
            db_path=tmp_path / "test.sqlite3",
            env={},
            feed=FakeFeed(),
            orderbooks=UnavailableBooks(),
            new_ledger=True,
        )
        composition.sqlite_connection.close()

        assert composition.executor.model == v2_config.strategies.strategies[1].execution_model()
        assert composition.executor.model.exit_pricing is ExitPricing.BAR_CLOSE

    def test_startup_refuses_a_release_without_the_experiment_files(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """A release dir without docs/experiments exits 3 instead of crash-looping."""
        with pytest.raises(serve_module.StartupError, match="experiment files missing"):
            serve_module.check_startup(shadow_config, {}, experiment_root=tmp_path)

    def test_startup_accepts_the_repository_as_experiment_root(
        self, shadow_config: HermesConfig
    ) -> None:
        """The repo carries both files, so development runs start."""
        assert_accepted(serve_module.check_startup, shadow_config, {}, experiment_root=REPO_ROOT)

    def test_build_refuses_an_experiment_root_without_the_pre_registration(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """The kernel's kill rules cannot be built without the signed file."""
        with pytest.raises(serve_module.StartupError, match="cannot read the experiment files"):
            serve_module.build_composition(
                shadow_config,
                db_path=tmp_path / "test.sqlite3",
                env={},
                feed=FakeFeed(),
                experiment_root=tmp_path,
                new_ledger=True,
            )

    def test_run_closes_the_depth_source(self, tmp_path: Path) -> None:
        """Shutdown releases the depth source with the other resources."""
        books = UnavailableBooks()
        closed: list[bool] = []

        async def _aclose() -> None:
            closed.append(True)

        books.aclose = _aclose  # type: ignore[method-assign]
        feed = FakeFeed()
        composition = build(tmp_path / "test.sqlite3", feed, BASE, books=books)
        shutdown = asyncio.Event()
        shutdown.set()

        asyncio.run(serve_module.run(composition, shutdown))

        assert closed == [True]


class _FixedEntryCheckStrategy(SwingStrategyAdapter):
    """A swing adapter that never signals and reports a fixed entry record."""

    def __init__(self, check: EntryCheck) -> None:
        super().__init__()
        self._check = check

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Return no signal, whatever the bar."""
        return None

    @property
    def last_entry_check(self) -> EntryCheck | None:
        """Return the fixed record."""
        return self._check


def _shut(composition: serve_module.Composition) -> None:
    """Close the ledger and release the serve lock, as a stopped serve does."""
    composition.sqlite_connection.close()
    if composition.serve_lock is not None:
        composition.serve_lock.release()


def _with_capital(config: HermesConfig, amount: str) -> HermesConfig:
    entry = config.strategies.strategies[0].model_copy(update={"capital_budget": Decimal(amount)})
    return config.model_copy(update={"strategies": StrategiesConfig(strategies=[entry])})


class TestWithOnlyEnabled:
    """#1076: ``--strategy`` makes the named roster entry the one enabled strategy."""

    def test_the_named_entry_is_the_only_one_enabled(self, shadow_config: HermesConfig) -> None:
        """Invariant: every later check reads the named entry as the enabled one."""
        chosen = serve_module.with_only_enabled(shadow_config, serve_module.V2_STRATEGY_NAME)
        enabled = [s.name for s in chosen.strategies.strategies if s.enabled]
        names = [s.name for s in chosen.strategies.strategies]
        assert (enabled, names) == (
            [serve_module.V2_STRATEGY_NAME],
            [s.name for s in shadow_config.strategies.strategies],
        )

    def test_the_rest_of_the_entry_is_unchanged(self, shadow_config: HermesConfig) -> None:
        """Invariant: only ``enabled`` moves; capital, sizing and pricing stay as signed."""
        chosen = serve_module.with_only_enabled(shadow_config, serve_module.V2_STRATEGY_NAME)
        assert chosen.strategies.strategies[1] == shadow_config.strategies.strategies[1].model_copy(
            update={"enabled": True}
        )

    def test_a_name_missing_from_the_roster_is_refused(self, shadow_config: HermesConfig) -> None:
        """Invariant: an instance never falls back to whatever the file enables."""
        with pytest.raises(serve_module.StartupError, match="has no 'no-such-shadow' entry"):
            serve_module.with_only_enabled(shadow_config, "no-such-shadow")


class TestLedgerAndSeedGuards:
    """Serve refuses a missing ledger and a capital that differs from its seed (issue #876)."""

    # SEV-1: no rubric row matches; a wrong --db must not drop the open positions
    def test_a_missing_ledger_is_refused_and_not_created(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """Without --new-ledger a ledger path that does not exist stops startup, naming it."""
        db_path = tmp_path / "unmounted" / "hermes.sqlite3"
        with pytest.raises(serve_module.StartupError) as refused:
            serve_module.build_composition(shadow_config, db_path=db_path, env={}, feed=FakeFeed())
        assert (str(refused.value).startswith(f"no ledger at {db_path}"), db_path.exists()) == (
            True,
            False,
        )

    def test_new_ledger_creates_the_ledger_and_records_the_seed(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """With --new-ledger the first boot creates the ledger and seeds the configured capital."""
        db_path = tmp_path / "hermes.sqlite3"
        composition = serve_module.build_composition(
            shadow_config, db_path=db_path, env={}, feed=FakeFeed(), new_ledger=True
        )
        try:
            seed = composition.ledger.capital_seed(StrategyId("swing-donchian-4h"))
        finally:
            _shut(composition)
        assert (db_path.exists(), seed.amount.amount if seed else None) == (True, Decimal(5000))

    # SEV-1: no rubric row matches; sizing must use the cash the ledger recorded
    def test_a_capital_that_differs_from_the_recorded_seed_is_refused_naming_both(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """A capital_budget changed after the first boot stops startup with both amounts."""
        db_path = tmp_path / "hermes.sqlite3"
        _shut(
            serve_module.build_composition(
                shadow_config, db_path=db_path, env={}, feed=FakeFeed(), new_ledger=True
            )
        )
        with pytest.raises(serve_module.StartupError) as refused:
            serve_module.build_composition(
                _with_capital(shadow_config, "6000"), db_path=db_path, env={}, feed=FakeFeed()
            )
        assert str(refused.value).startswith(
            "capital_budget 6000 USDT differs from the 5000 USDT seed this ledger recorded"
        )

    def test_the_same_capital_written_differently_still_boots(
        self, shadow_config: HermesConfig, tmp_path: Path
    ) -> None:
        """5000 and 5000.00 are the same capital, so the second boot goes ahead."""
        db_path = tmp_path / "hermes.sqlite3"
        _shut(
            serve_module.build_composition(
                shadow_config, db_path=db_path, env={}, feed=FakeFeed(), new_ledger=True
            )
        )
        composition = serve_module.build_composition(
            _with_capital(shadow_config, "5000.00"), db_path=db_path, env={}, feed=FakeFeed()
        )
        _shut(composition)
        assert composition.tracker.state().current_value == Decimal(5000)


def test_a_bad_notifier_api_base_fails_startup_instead_of_muting_the_channel() -> None:
    """Missing credentials quietly turn Telegram off; a mistyped endpoint must not (#970)."""
    env = {
        "HERMES_TELEGRAM__BOT_TOKEN": "123456:ABC-DEF",  # gitleaks:allow
        "HERMES_TELEGRAM__CHAT_ID": "-100",
        "HERMES_TELEGRAM__API_BASE": "http://api.telegram.org",
    }

    with pytest.raises(EndpointConfigError, match="HERMES_TELEGRAM__API_BASE"):
        serve_module._telegram_config(env)
