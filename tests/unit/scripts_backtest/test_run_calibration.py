"""Unit tests for scripts/backtest/run_calibration.py helper functions.

Covers the pure helpers (fill synthesis, symbol parsing, JSON serialisation).
The full end-to-end async run is smoke-tested manually via
``HERMES_DATA_DIR=D:/hermes-data python scripts/backtest/run_calibration.py``
because it depends on the ~30 MB Binance Vision klines archive.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.strategy.swing import adapter as swing_adapter
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.support.paths import REPO_ROOT

_STRATEGY_ID = StrategyId("swing-mvp1-phase1-btcusdt")


def _import_driver():
    """Import the driver module (not a proper package — same-dir import)."""
    root = REPO_ROOT
    module_path = root / "scripts" / "backtest" / "run_calibration.py"
    assert module_path.exists(), f"missing driver: {module_path}"
    spec = importlib.util.spec_from_file_location("run_calibration_driver", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_calibration_driver"] = module
    spec.loader.exec_module(module)
    return module


DRIVER = _import_driver()


def _btc_pair() -> Pair:
    """Return BTC/USDT pair used across tests."""
    return Pair(base=Currency.BTC, quote=Currency.USDT)


def test_parse_pair_btcusdt() -> None:
    """`--pair BTCUSDT` resolves to (BTC, USDT)."""
    pair = DRIVER._parse_pair("BTCUSDT")
    assert pair.base == Currency.BTC, f"expected BTC base, got {pair.base}"
    assert pair.quote == Currency.USDT, f"expected USDT quote, got {pair.quote}"


def test_parse_pair_ethusdt_case_insensitive() -> None:
    """Lowercase symbol is accepted (`btcusdt`, `ethusdt`, `solusdt`)."""
    pair = DRIVER._parse_pair("ethusdt")
    assert pair.base == Currency.ETH, f"expected ETH base, got {pair.base}"


def test_parse_pair_rejects_unknown_symbol() -> None:
    """An unknown symbol raises ValueError with the offending value in the message."""
    with pytest.raises(ValueError, match=r"unsupported symbol"):
        DRIVER._parse_pair("XRPUSDT")


def test_parse_iso_date_is_utc_aware() -> None:
    """`--start 2024-01-01` becomes a UTC-aware datetime at 00:00:00."""
    dt = DRIVER._parse_iso_date("2024-01-01")
    assert dt.tzinfo == UTC, f"expected UTC tz, got {dt.tzinfo}"
    assert dt.hour == 0, f"expected hour=0, got {dt.hour}"
    assert dt.year == 2024, f"expected year=2024, got {dt.year}"


def test_synthesise_fill_atr_sized_notional() -> None:
    """Entry signal with ATR present → notional derived from T-1 formula."""
    pair = _btc_pair()
    signal = StrategySignal(
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=Decimal("50000"),
        stop_price=Decimal("48000"),
        atr_context=Decimal("500"),
        signal_time=datetime(2024, 1, 15, tzinfo=UTC),
    )
    starting_equity = Decimal("10000")
    fill = DRIVER._synthesise_fill(
        signal, pair, fee_bps=10, starting_equity=starting_equity, strategy_id=_STRATEGY_ID
    )

    # Sanity: fill's avg_price matches the signal's entry_price.
    assert fill.avg_price == Decimal("50000"), f"avg_price drift: {fill.avg_price}"
    assert fill.strategy_id == _STRATEGY_ID, f"strategy_id drift: {fill.strategy_id}"
    assert fill.timestamp == datetime(2024, 1, 15, tzinfo=UTC), f"timestamp drift: {fill.timestamp}"
    # T-1 formula gives 6666.67, but capped at 5% * 10000 = 500 (hard cap wins).
    # qty = 500 / 50000 = 0.01
    assert fill.executed_qty == Decimal("0.01"), (
        f"expected qty=0.01 (hard-cap wins), got {fill.executed_qty}"
    )
    # Fee = 0.01 * 50000 * 10 / 10000 = 0.5 USDT.
    assert fill.fee.amount == Decimal("0.5"), f"expected fee=0.5 USDT, got {fill.fee.amount}"
    assert fill.fee.currency == Currency.USDT, f"expected USDT fee, got {fill.fee.currency}"


def test_synthesise_fill_zero_fee_promo_window() -> None:
    """During the BTC promo (2022-08-15), fee_bps=0 → fill.fee = 0 exactly."""
    pair = _btc_pair()
    signal = StrategySignal(
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=Decimal("20000"),
        stop_price=Decimal("19000"),
        atr_context=Decimal("400"),
        signal_time=datetime(2022, 8, 15, tzinfo=UTC),
    )
    fill = DRIVER._synthesise_fill(
        signal, pair, fee_bps=0, starting_equity=Decimal("10000"), strategy_id=_STRATEGY_ID
    )
    assert fill.fee.amount == Decimal("0"), (
        f"promo-window fill fee must be 0, got {fill.fee.amount}"
    )


def test_synthesise_fill_rejects_zero_entry_price() -> None:
    """A signal without an entry_price (e.g. malformed exit signal) raises loudly."""
    pair = _btc_pair()
    signal = StrategySignal(
        kind=SignalKind.EXIT,
        side=SignalSide.LONG,
        entry_price=None,
        stop_price=Decimal("48000"),
        atr_context=Decimal("500"),
        signal_time=datetime(2024, 1, 15, tzinfo=UTC),
        exit_reason="chandelier",
    )
    with pytest.raises(ValueError, match=r"entry_price must be > 0"):
        DRIVER._synthesise_fill(
            signal, pair, fee_bps=10, starting_equity=Decimal("10000"), strategy_id=_STRATEGY_ID
        )


def test_jsonify_handles_decimal_and_datetime() -> None:
    """_jsonify converts Decimal → str and datetime → ISO-8601 string."""
    payload = {
        "price": Decimal("50000.12345678"),
        "ts": datetime(2024, 1, 15, 12, 30, tzinfo=UTC),
        "nested": [{"inner": Decimal("0.001")}],
    }
    out = DRIVER._jsonify(payload)
    assert out["price"] == "50000.12345678", f"Decimal not stringified: {out['price']}"
    assert out["ts"] == "2024-01-15T12:30:00+00:00", f"datetime not ISO: {out['ts']}"
    assert out["nested"][0]["inner"] == "0.001", "nested Decimal not stringified"


def _day_bar(pair: Pair, open_time: datetime, interval: KlineInterval) -> Bar:
    hours = 4 if interval == KlineInterval.H4 else 24
    return Bar(
        pair=pair,
        interval=interval,
        open_time=open_time,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal("1"),
        close_time=open_time + timedelta(hours=hours),
    )


def test_merge_delivers_each_daily_bar_after_the_4h_bars_it_covers() -> None:
    """Issue #311: a 1D bar is delivered only once it has closed.

    It must follow the 4h bars that close before it and precede the 4h bar
    closing at the same instant, so no 4h bar sees a 1D close from its future.
    """
    pair = _btc_pair()
    day = datetime(2024, 1, 15, tzinfo=UTC)
    bars_4h = [_day_bar(pair, day + timedelta(hours=4 * i), KlineInterval.H4) for i in range(6)]
    daily = _day_bar(pair, day, KlineInterval.D1)

    merged = DRIVER._merge_by_close_time(bars_4h, [daily])

    assert merged == [*bars_4h[:5], daily, bars_4h[5]], (
        f"daily bar delivered at position {merged.index(daily)}, expected 5 (just before "
        "the 4h bar that closes at midnight)"
    )


def test_strategy_parameters_cover_every_keyword_and_build_the_adapter() -> None:
    """The trial records exactly the parameters the strategy was built with, no more, no less."""
    params = DRIVER._strategy_parameters()
    assert set(params) >= {"donchian_period", "adx_period", "ema_period", "time_stop_days"}
    assert all(value is not inspect.Parameter.empty for value in params.values())
    SwingStrategyAdapter(**params)


def test_strategy_parameters_are_the_adapters_own_keyword_defaults() -> None:
    """The trial records the defaults the adapter would run with, not a copy that can drift."""
    expected = {
        name: parameter.default
        for name, parameter in inspect.signature(SwingStrategyAdapter.__init__).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }
    assert DRIVER._strategy_parameters() == expected


def test_git_state_returns_a_full_commit_sha() -> None:
    """A trial pins the commit it ran on; the driver refuses to log without one."""
    sha, dirty = DRIVER._git_state()
    assert len(sha) == 40
    assert isinstance(dirty, bool)


def test_warm_up_fills_the_strategy_daily_buffer() -> None:
    """Issue #880: a window opens with as many daily bars as serve warms up with.

    The EMA regime filter's value depends on the daily bars it has seen until
    the strategy's daily buffer is full, so the backtest must load at least
    that many days before the window (serve loads 300).
    """
    assert DRIVER._WARMUP_DAYS >= swing_adapter._1D_BUFFER_CAP
