"""Invariant tests for the :class:`Kline` domain value object + interval enum.

Every test names one invariant Kline's ``__post_init__`` protects. The
docstring is the spec — the assertion is the enforcement.

Reference: docs/guides/ai-authored-tests.md.
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)


def _make_price(amount: str, pair: Pair = _BTC_USDT) -> Price:
    """Build a :class:`Price` with a decimal amount and given pair."""
    return Price(amount=Decimal(amount), pair=pair)


def _make_qty(amount: str, asset: Currency = Currency.BTC) -> Quantity:
    """Build a :class:`Quantity` with a decimal amount and given asset."""
    return Quantity(amount=Decimal(amount), asset=asset)


def _valid_kline(
    *,
    pair: Pair = _BTC_USDT,
    interval: KlineInterval = KlineInterval.M1,
    open_time: datetime | None = None,
) -> Kline:
    """Return a canonical valid :class:`Kline` for use as a baseline in tests."""
    open_time = open_time or datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    close_time = open_time + timedelta(minutes=1)
    return Kline(
        pair=pair,
        interval=interval,
        open_time=open_time,
        open=_make_price("50000", pair=pair),
        high=_make_price("50100", pair=pair),
        low=_make_price("49900", pair=pair),
        close=_make_price("50050", pair=pair),
        volume=_make_qty("1.5", asset=pair.base),
        close_time=close_time,
    )


class TestKlineIntervalEnum:
    """The :class:`KlineInterval` values match Binance interval strings verbatim."""

    def test_m1_value_matches_binance(self) -> None:
        """M1 stringifies to '1m' so it can be sent to Binance without translation."""
        assert KlineInterval.M1.value == "1m"

    def test_m5_value_matches_binance(self) -> None:
        """M5 stringifies to '5m' so it can be sent to Binance without translation."""
        assert KlineInterval.M5.value == "5m"

    def test_h1_value_matches_binance(self) -> None:
        """H1 stringifies to '1h' so it can be sent to Binance without translation."""
        assert KlineInterval.H1.value == "1h"

    def test_d1_value_matches_binance(self) -> None:
        """D1 stringifies to '1d' so it can be sent to Binance without translation."""
        assert KlineInterval.D1.value == "1d"


class TestKlineConstructionHappyPath:
    """A well-formed Kline constructs without raising and preserves every field."""

    def test_construction_preserves_pair_and_interval(self) -> None:
        """The pair and interval passed in are stored on the frozen instance."""
        kline = _valid_kline(pair=_ETH_USDT, interval=KlineInterval.H1)
        assert kline.pair == _ETH_USDT
        assert kline.interval == KlineInterval.H1

    def test_construction_preserves_ohlc_prices(self) -> None:
        """open/high/low/close prices round-trip losslessly through construction."""
        kline = _valid_kline()
        assert kline.open.amount == Decimal("50000")
        assert kline.high.amount == Decimal("50100")
        assert kline.low.amount == Decimal("49900")
        assert kline.close.amount == Decimal("50050")

    def test_construction_preserves_volume(self) -> None:
        """The volume Quantity is stored with the exact Decimal amount passed in."""
        kline = _valid_kline()
        assert kline.volume.amount == Decimal("1.5")
        assert kline.volume.asset == Currency.BTC

    def test_construction_preserves_utc_timestamps(self) -> None:
        """open_time and close_time round-trip with UTC tzinfo intact."""
        opened = datetime(2026, 3, 15, 12, 34, 56, tzinfo=UTC)
        kline = _valid_kline(open_time=opened)
        assert kline.open_time == opened
        assert kline.close_time == opened + timedelta(minutes=1)


class TestKlineNaiveDatetimeRejection:
    """Naive datetimes on open_time or close_time raise ValueError (§9 UTC-only)."""

    def test_naive_open_time_raises(self) -> None:
        """A naive open_time raises ValueError naming the field."""
        with pytest.raises(ValueError, match="open_time"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=datetime(2026, 1, 1, 0, 0),  # naive
                open=_make_price("1"),
                high=_make_price("2"),
                low=_make_price("1"),
                close=_make_price("1.5"),
                volume=_make_qty("1"),
                close_time=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )

    def test_naive_close_time_raises(self) -> None:
        """A naive close_time raises ValueError naming the field."""
        with pytest.raises(ValueError, match="close_time"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                open=_make_price("1"),
                high=_make_price("2"),
                low=_make_price("1"),
                close=_make_price("1.5"),
                volume=_make_qty("1"),
                close_time=datetime(2026, 1, 1, 0, 1),  # naive
            )

    def test_non_utc_open_time_raises(self) -> None:
        """A non-UTC tz-aware open_time raises ValueError — hermes stores UTC only."""
        madrid = timezone(timedelta(hours=1))
        with pytest.raises(ValueError, match="open_time"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=datetime(2026, 1, 1, 0, 0, tzinfo=madrid),
                open=_make_price("1"),
                high=_make_price("2"),
                low=_make_price("1"),
                close=_make_price("1.5"),
                volume=_make_qty("1"),
                close_time=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )


class TestKlineTimeOrdering:
    """close_time must be strictly greater than open_time — zero-width windows are invalid."""

    def test_close_equal_to_open_raises(self) -> None:
        """A close_time equal to open_time raises — a candle with zero duration is invalid."""
        same = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="strictly after"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=same,
                open=_make_price("1"),
                high=_make_price("1"),
                low=_make_price("1"),
                close=_make_price("1"),
                volume=_make_qty("1"),
                close_time=same,
            )

    def test_close_before_open_raises(self) -> None:
        """A close_time earlier than open_time raises — backwards-time candles are invalid."""
        opened = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        earlier = datetime(2026, 1, 1, 0, 4, tzinfo=UTC)
        with pytest.raises(ValueError, match="strictly after"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=opened,
                open=_make_price("1"),
                high=_make_price("1"),
                low=_make_price("1"),
                close=_make_price("1"),
                volume=_make_qty("1"),
                close_time=earlier,
            )


class TestKlinePairCoherence:
    """Every embedded Price must reference the same pair as the Kline itself."""

    def test_mismatched_open_pair_raises(self) -> None:
        """A Price whose pair differs from Kline.pair raises ValueError naming the field."""
        with pytest.raises(ValueError, match=r"Kline\.open\.pair"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=datetime(2026, 1, 1, tzinfo=UTC),
                open=_make_price("1", pair=_ETH_USDT),  # wrong pair
                high=_make_price("2"),
                low=_make_price("1"),
                close=_make_price("1.5"),
                volume=_make_qty("1"),
                close_time=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )

    def test_mismatched_close_pair_raises(self) -> None:
        """A Price whose pair differs on the close field raises with 'close.pair' in the message."""
        with pytest.raises(ValueError, match=r"Kline\.close\.pair"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=datetime(2026, 1, 1, tzinfo=UTC),
                open=_make_price("1"),
                high=_make_price("2"),
                low=_make_price("1"),
                close=_make_price("1.5", pair=_ETH_USDT),  # wrong pair
                volume=_make_qty("1"),
                close_time=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )

    def test_volume_asset_must_be_pair_base(self) -> None:
        """A volume whose asset differs from pair.base raises — volume is base-denominated."""
        with pytest.raises(ValueError, match=r"Kline\.volume\.asset"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=datetime(2026, 1, 1, tzinfo=UTC),
                open=_make_price("1"),
                high=_make_price("2"),
                low=_make_price("1"),
                close=_make_price("1.5"),
                volume=_make_qty("1", asset=Currency.ETH),  # wrong asset
                close_time=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )


class TestKlineOHLCBounds:
    """The high/low/open/close numeric relationships (high>=open,close; low<=open,close)."""

    def test_high_below_low_raises(self) -> None:
        """A high strictly below low raises ValueError — degenerate OHLC ordering."""
        with pytest.raises(ValueError, match=r"high.*low"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=datetime(2026, 1, 1, tzinfo=UTC),
                open=_make_price("50"),
                high=_make_price("40"),
                low=_make_price("45"),
                close=_make_price("50"),
                volume=_make_qty("1"),
                close_time=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )

    def test_high_below_open_raises(self) -> None:
        """A high below open raises — high must be the max of open and close."""
        with pytest.raises(ValueError, match="high"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=datetime(2026, 1, 1, tzinfo=UTC),
                open=_make_price("50100"),
                high=_make_price("50000"),  # below open
                low=_make_price("49900"),
                close=_make_price("50050"),
                volume=_make_qty("1"),
                close_time=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )

    def test_low_above_close_raises(self) -> None:
        """A low above close raises — low must be the min of open and close."""
        with pytest.raises(ValueError, match="low"):
            Kline(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=datetime(2026, 1, 1, tzinfo=UTC),
                open=_make_price("50000"),
                high=_make_price("50100"),
                low=_make_price("50050"),  # above close
                close=_make_price("49950"),
                volume=_make_qty("1"),
                close_time=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )

    def test_flat_candle_open_equals_high_equals_low_equals_close_ok(self) -> None:
        """A candle where all four OHLC values are equal is valid (thin-liquidity edge case)."""
        kline = Kline(
            pair=_BTC_USDT,
            interval=KlineInterval.M1,
            open_time=datetime(2026, 1, 1, tzinfo=UTC),
            open=_make_price("50000"),
            high=_make_price("50000"),
            low=_make_price("50000"),
            close=_make_price("50000"),
            volume=_make_qty("0"),
            close_time=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        )
        assert kline.high.amount == kline.low.amount
