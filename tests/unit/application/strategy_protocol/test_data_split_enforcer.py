"""Unit tests for DataSplitEnforcer and certification_context.

Tests cover:
- OOS bar raises OOSDataLeakageError outside certification mode.
- OOS bar succeeds inside certification_context().
- Train/validation bars always succeed regardless of mode.
- Bar exactly at OOS start boundary raises.
- Bar one day before OOS start succeeds.
- certification_context restores False on exit (no context leak).
- OOS trade raises OOSDataLeakageError outside certification mode.
- OOS trade succeeds inside certification_context().
- is_certification_mode() reflects the context variable correctly.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.application.strategy_protocol.data_split_enforcer import (
    DataSplitEnforcer,
    certification_context,
    is_certification_mode,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.errors import OOSDataLeakageError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.trade import Trade, TradeSide
from hermes.domain.value_objects.pair import Pair
from tests.support.paths import REPO_ROOT

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OOS_START = datetime(2025, 9, 1, tzinfo=UTC)
_TRAIN_TS = datetime(2023, 6, 15, tzinfo=UTC)
_VAL_TS = datetime(2025, 1, 15, tzinfo=UTC)
_OOS_MID_TS = datetime(2026, 3, 1, tzinfo=UTC)
_ONE_DAY_BEFORE_OOS = _OOS_START - timedelta(days=1)

_PAIR = Pair.from_str("BTC/USDT")


# ---------------------------------------------------------------------------
# Fake MarketDataPort for testing
# ---------------------------------------------------------------------------


class _FakeMarketDataPort:
    """Minimal fake port that yields bars/trades with configurable timestamps."""

    def __init__(self, bar_timestamp: datetime | None = None) -> None:
        """Initialise fake port with optional timestamp for yielded items."""
        self._bar_ts = bar_timestamp

    async def stream_bars(self, pair: Pair, interval: KlineInterval) -> AsyncIterator[Bar]:
        """Yield one bar at the configured timestamp if set."""
        if self._bar_ts is not None:
            close_ts = self._bar_ts + timedelta(hours=4)
            yield Bar(
                pair=pair,
                interval=interval,
                open_time=self._bar_ts,
                close_time=close_ts,
                open=Decimal("50000"),
                high=Decimal("51000"),
                low=Decimal("49000"),
                close=Decimal("50500"),
                volume=Decimal("100"),
            )

    async def stream_trades(self, pair: Pair) -> AsyncIterator[Trade]:
        """Yield one trade at the configured timestamp if set."""
        if self._bar_ts is not None:
            yield Trade(
                pair=pair,
                trade_id=1,
                price=Decimal("50000"),
                quantity=Decimal("0.01"),
                trade_time=self._bar_ts,
                side=TradeSide.BUY,
            )

    async def stream_orderbook_diffs(self, pair: Pair) -> AsyncIterator:
        """Yield no diffs — not needed for enforcer tests."""
        return
        yield  # make it an async generator

    async def get_snapshot(self, pair: Pair) -> Orderbook:
        """Return a minimal snapshot."""
        return Orderbook(pair=pair, bids=(), asks=(), last_update_id=1)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pre_reg(tmp_path_factory: pytest.TempPathFactory) -> PreRegistration:
    """Return a PreRegistration instance pointed at the real repo file."""
    return PreRegistration(project_root=REPO_ROOT)


def make_enforcer(ts: datetime, pre_reg: PreRegistration) -> DataSplitEnforcer:
    """Return a DataSplitEnforcer wrapping a fake port that yields items at ts."""
    return DataSplitEnforcer(inner=_FakeMarketDataPort(ts), pre_reg=pre_reg)


# ---------------------------------------------------------------------------
# Tests — is_certification_mode
# ---------------------------------------------------------------------------


class TestIsCertificationMode:
    """is_certification_mode() reflects the ContextVar state."""

    def test_false_outside_context(self) -> None:
        """is_certification_mode() must return False when no certification_context is active."""
        assert is_certification_mode() is False, (
            "is_certification_mode() must return False outside certification_context()."
        )

    def test_true_inside_context(self) -> None:
        """is_certification_mode() must return True inside certification_context()."""
        with certification_context():
            assert is_certification_mode() is True, (
                "is_certification_mode() must return True inside certification_context()."
            )

    def test_restored_after_context_exit(self) -> None:
        """certification_context() must restore False on __exit__ (no context leak)."""
        with certification_context():
            pass
        assert is_certification_mode() is False, (
            "is_certification_mode() must be restored to False after certification_context() exits."
        )


# ---------------------------------------------------------------------------
# Tests — bar enforcement
# ---------------------------------------------------------------------------


class TestDataSplitEnforcerBars:
    """stream_bars() blocks OOS bars outside certification_context."""

    def test_oos_bar_raises_outside_certification(self, pre_reg: PreRegistration) -> None:
        """OOS bar (2026-03-01) must raise OOSDataLeakageError outside certification mode."""
        enforcer = make_enforcer(_OOS_MID_TS, pre_reg)

        async def run() -> None:
            async for _ in enforcer.stream_bars(_PAIR, KlineInterval.H1):
                pass

        with pytest.raises(OOSDataLeakageError) as exc_info:
            asyncio.run(run())
        assert "2026-03-01" in str(exc_info.value), (
            f"OOSDataLeakageError message must include the attempted timestamp. "
            f"Got: {exc_info.value}"
        )

    def test_oos_bar_at_start_boundary_raises(self, pre_reg: PreRegistration) -> None:
        """Bar exactly at OOS_START (2025-09-01) must raise OOSDataLeakageError."""
        enforcer = make_enforcer(_OOS_START, pre_reg)

        async def run() -> None:
            async for _ in enforcer.stream_bars(_PAIR, KlineInterval.H1):
                pass

        with pytest.raises(OOSDataLeakageError, match="OOS data leakage prevented"):
            asyncio.run(run())

    def test_bar_one_day_before_oos_succeeds(self, pre_reg: PreRegistration) -> None:
        """Bar one day before OOS_START (validation window) must not be blocked."""
        enforcer = make_enforcer(_ONE_DAY_BEFORE_OOS, pre_reg)
        bars_received: list[Bar] = []

        async def run() -> None:
            bars_received.extend(
                [bar async for bar in enforcer.stream_bars(_PAIR, KlineInterval.H1)]
            )

        asyncio.run(run())
        assert [(item.pair, item.open_time) for item in bars_received] == [
            (_PAIR, _ONE_DAY_BEFORE_OOS)
        ]

    def test_train_bar_always_succeeds(self, pre_reg: PreRegistration) -> None:
        """Train window bar (2023-06-15) must always pass the enforcer."""
        enforcer = make_enforcer(_TRAIN_TS, pre_reg)
        bars_received: list[Bar] = []

        async def run() -> None:
            bars_received.extend(
                [bar async for bar in enforcer.stream_bars(_PAIR, KlineInterval.H1)]
            )

        asyncio.run(run())
        assert [(item.pair, item.open_time) for item in bars_received] == [(_PAIR, _TRAIN_TS)]

    def test_validation_bar_succeeds(self, pre_reg: PreRegistration) -> None:
        """Validation window bar (2025-01-15) must pass the enforcer."""
        enforcer = make_enforcer(_VAL_TS, pre_reg)
        bars_received: list[Bar] = []

        async def run() -> None:
            bars_received.extend(
                [bar async for bar in enforcer.stream_bars(_PAIR, KlineInterval.H1)]
            )

        asyncio.run(run())
        assert [(item.pair, item.open_time) for item in bars_received] == [(_PAIR, _VAL_TS)]

    def test_oos_bar_succeeds_in_certification_context(self, pre_reg: PreRegistration) -> None:
        """OOS bar must pass inside certification_context() — the read refusal is suspended."""
        enforcer = make_enforcer(_OOS_MID_TS, pre_reg)
        bars_received: list[Bar] = []

        async def run() -> None:
            with certification_context():
                bars_received.extend(
                    [bar async for bar in enforcer.stream_bars(_PAIR, KlineInterval.H1)]
                )

        asyncio.run(run())
        assert [(item.pair, item.open_time) for item in bars_received] == [(_PAIR, _OOS_MID_TS)]


# ---------------------------------------------------------------------------
# Tests — trade enforcement
# ---------------------------------------------------------------------------


class TestDataSplitEnforcerTrades:
    """stream_trades() blocks OOS trades outside certification_context."""

    def test_oos_trade_raises_outside_certification(self, pre_reg: PreRegistration) -> None:
        """OOS trade must raise OOSDataLeakageError outside certification mode."""
        enforcer = make_enforcer(_OOS_MID_TS, pre_reg)

        async def run() -> None:
            async for _ in enforcer.stream_trades(_PAIR):
                pass

        with pytest.raises(OOSDataLeakageError, match="OOS data leakage prevented"):
            asyncio.run(run())

    def test_oos_trade_succeeds_in_certification_context(self, pre_reg: PreRegistration) -> None:
        """OOS trade must pass inside certification_context()."""
        enforcer = make_enforcer(_OOS_MID_TS, pre_reg)
        trades_received: list[Trade] = []

        async def run() -> None:
            with certification_context():
                trades_received.extend([trade async for trade in enforcer.stream_trades(_PAIR)])

        asyncio.run(run())
        assert [(item.pair, item.trade_time) for item in trades_received] == [(_PAIR, _OOS_MID_TS)]

    def test_train_trade_always_succeeds(self, pre_reg: PreRegistration) -> None:
        """Train window trade must always pass the enforcer."""
        enforcer = make_enforcer(_TRAIN_TS, pre_reg)
        trades_received: list[Trade] = []

        async def run() -> None:
            trades_received.extend([trade async for trade in enforcer.stream_trades(_PAIR)])

        asyncio.run(run())
        assert [(item.pair, item.trade_time) for item in trades_received] == [(_PAIR, _TRAIN_TS)]


def test_frozen_domain_error_raised_inside_certification_context_propagates() -> None:
    """A frozen DomainError raised in the block propagates as itself, not FrozenInstanceError."""
    with pytest.raises(OOSDataLeakageError) as exc, certification_context():
        raise OOSDataLeakageError(attempted_timestamp="t", oos_start="s")
    assert exc.value.attempted_timestamp == "t"
    assert is_certification_mode() is False
