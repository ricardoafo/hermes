"""Unit tests for scripts/backtest/run_calibration.py helper functions.

Covers the pure helpers (fill synthesis, symbol parsing, JSON serialisation).
The full end-to-end async run is smoke-tested manually via
``HERMES_DATA_DIR=D:/hermes-data python scripts/backtest/run_calibration.py``
because it depends on the ~30 MB Binance Vision klines archive.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair


def _import_driver():
    """Import the driver module (not a proper package — same-dir import)."""
    root = Path(__file__).resolve().parents[3]
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
    fill = DRIVER._synthesise_fill(signal, pair, fee_bps=10, starting_equity=starting_equity)

    # Sanity: fill's avg_price matches the signal's entry_price.
    assert fill.avg_price == Decimal("50000"), f"avg_price drift: {fill.avg_price}"
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
    fill = DRIVER._synthesise_fill(signal, pair, fee_bps=0, starting_equity=Decimal("10000"))
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
        DRIVER._synthesise_fill(signal, pair, fee_bps=10, starting_equity=Decimal("10000"))


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
