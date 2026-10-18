"""Unit tests for scripts/backtest/stop_slippage.py (issue #1104).

Invariants:

- The stop resting during a 4h bar is ``previous close - m x ATR(22)`` over
  earlier bars only; the bar it is tested on never moves it.
- An event is a bar whose low reaches the stop; its model fill is the
  backtest's ``min(stop, open)``.
- The touch minute is the first 1-minute bar inside the 4h bar whose low
  reaches the stop. The optimistic bound fills at ``min(stop, minute open)``,
  the pessimistic bound at the minute's low, both in bps below the model fill.
- With trades, the trigger is the first trade inside the 4h bar at or below
  the stop, reported in bps below the model fill; sampled days are spread
  evenly and repeat on every run.
- Percentiles are nearest-rank; shares count values strictly above the
  threshold.
- The minute reader keeps open time, open and low even for rows ``Bar``
  refuses; grouping and the CLI stop at the Train end, so data past it adds
  nothing.
"""

import csv
import io
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "backtest"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import stop_slippage as ss  # noqa: E402 — the scripts directory is not a package

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_T0 = datetime(2021, 1, 1, tzinfo=UTC)
_FOUR_H = timedelta(hours=4)
_MINUTE = timedelta(minutes=1)
_TWO = Decimal("2.0")


def _bar(
    open_time: datetime,
    *,
    open_: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100",
) -> Bar:
    return Bar(
        pair=_BTC,
        interval=KlineInterval.H4,
        open_time=open_time,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(1),
        close_time=open_time + _FOUR_H,
    )


def _flat(count: int) -> list[Bar]:
    """Bars with true range 2 every bar, so ATR(22) is exactly 2."""
    return [_bar(_T0 + i * _FOUR_H) for i in range(count)]


def _minute(at: datetime, open_: str, low: str) -> ss.Minute:
    return ss.Minute(at, Decimal(open_), Decimal(low))


def _event(model_fill: str = "96") -> ss.StopEvent:
    return ss.StopEvent(_TWO, _T0, _T0 + _FOUR_H, Decimal(96), Decimal(model_fill))


def test_event_stop_is_previous_close_minus_m_atr() -> None:
    """Invariant: with ATR 2 and close 100, the m = 2 stop is 96 and a low of 95 reaches it."""
    bars = [*_flat(30), _bar(_T0 + 30 * _FOUR_H, low="95")]

    events = ss.stop_events(bars, [_TWO])

    assert [(e.stop, e.model_fill, e.bar_open) for e in events] == [
        (Decimal(96), Decimal(96), _T0 + 30 * _FOUR_H)
    ]


def test_tested_bar_never_moves_its_own_stop() -> None:
    """Invariant: a deeper low on the tested bar leaves its stop where earlier bars put it."""
    shallow = ss.stop_events([*_flat(30), _bar(_T0 + 30 * _FOUR_H, low="95")], [_TWO])
    deep = ss.stop_events([*_flat(30), _bar(_T0 + 30 * _FOUR_H, low="50")], [_TWO])

    assert shallow[0].stop == deep[0].stop == Decimal(96)


def test_low_above_stop_is_not_an_event() -> None:
    """Invariant: a low of 96.01 stays above the 96 stop, so nothing is recorded."""
    bars = [*_flat(30), _bar(_T0 + 30 * _FOUR_H, low="96.01")]

    assert ss.stop_events(bars, [_TWO]) == []


def test_bar_opening_below_stop_fills_model_at_open() -> None:
    """Invariant: a 4h bar that gaps to 94 fills the backtest at 94, not at the 96 stop."""
    bars = [*_flat(30), _bar(_T0 + 30 * _FOUR_H, open_="94", high="95", low="93", close="94")]

    assert ss.stop_events(bars, [_TWO])[0].model_fill == Decimal(94)


def test_touch_minute_opening_above_stop_bounds_zero_and_its_low() -> None:
    """Invariant: touch minute opens at 97 with low 95.04: optimistic 0, pessimistic 100 bps."""
    minutes = [_minute(_T0, "98", "97.5"), _minute(_T0 + _MINUTE, "97", "95.04")]

    assert ss.measure(_event(), minutes) == ss.Slippage(Decimal(0), Decimal(100))


def test_touch_minute_opening_through_stop_costs_the_gap() -> None:
    """Invariant: touch minute opens at 95.52 (below 96) with low 95.04: 50 and 100 bps."""
    minutes = [_minute(_T0, "98", "97.5"), _minute(_T0 + _MINUTE, "95.52", "95.04")]

    assert ss.measure(_event(), minutes) == ss.Slippage(Decimal(50), Decimal(100))


def test_only_the_first_touch_minute_counts() -> None:
    """Invariant: a later, deeper minute does not replace the first one that reached the stop."""
    minutes = [_minute(_T0, "97", "95.04"), _minute(_T0 + _MINUTE, "90", "80")]

    assert ss.measure(_event(), minutes) == ss.Slippage(Decimal(0), Decimal(100))


def test_minutes_outside_the_bar_are_ignored() -> None:
    """Invariant: a deeper minute at the 4h close (exclusive) is the next bar's and never counts."""
    minutes = [_minute(_T0 + _MINUTE, "97", "95.04"), _minute(_T0 + _FOUR_H, "90", "80")]

    assert ss.measure(_event(), minutes[1:]) != ss.measure(_event(), minutes)
    assert ss.measure(_event(), minutes) == ss.Slippage(Decimal(0), Decimal(100))


def test_gap_bar_first_minute_costs_nothing_beyond_model() -> None:
    """Invariant: when the 4h bar opens at 94, its first minute opens there too: optimistic 0."""
    minutes = [_minute(_T0, "94", "94")]

    assert ss.measure(_event(model_fill="94"), minutes) == ss.Slippage(Decimal(0), Decimal(0))


def test_minutes_group_into_their_event_bar_only() -> None:
    """Invariant: minutes inside an event's 4h bar group under it; minutes outside are dropped."""
    event = _event()
    inside = _minute(_T0 + 5 * _MINUTE, "97", "96")
    outside = _minute(_T0 + _FOUR_H, "97", "96")

    grouped = ss.minutes_by_bar([inside, outside], [event], _T0, _T0 + 10 * _FOUR_H)

    assert grouped == {_T0: [inside]}


def test_minutes_after_train_end_are_never_grouped() -> None:
    """Invariant: inside the same event bar, a minute after the Train end is left out."""
    in_train = _minute(_T0, "97", "96")
    after_end = _minute(_T0 + 2 * _MINUTE, "90", "80")

    grouped = ss.minutes_by_bar([in_train, after_end], [_event()], _T0, _T0 + _MINUTE)

    assert grouped == {_T0: [in_train]}


def test_reader_skips_headers_and_keeps_rows_bar_would_refuse(tmp_path: Path) -> None:
    """Invariant: a header is skipped and a row whose close time precedes its open time is kept.

    The 2020-12-21 BTCUSDT archive has such a row; only open time, open and low matter here.
    """
    at = datetime(2020, 12, 21, 14, 9, tzinfo=UTC)
    start = int(at.timestamp() * 1000)
    body = (
        "open_time,open,high,low,close\n" + f"{start},97,98,95.5,97,1,{start - 60_000},1,1,1,1,0\n"
    )
    folder = tmp_path / "2020" / "12"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "BTCUSDT-1m-2020-12.zip", "w") as archive:
        archive.writestr("BTCUSDT-1m-2020-12.csv", body)

    assert list(ss.read_minutes(tmp_path)) == [ss.Minute(at, Decimal(97), Decimal("95.5"))]


def test_percentile_is_nearest_rank() -> None:
    """Invariant: of 1..10, p50 is 5, p90 is 9 and p99 is 10 (nearest rank)."""
    values = [Decimal(v) for v in range(10, 0, -1)]

    assert [ss.percentile(values, q) for q in (50, 90, 99)] == [Decimal(5), Decimal(9), Decimal(10)]


@given(st.lists(st.integers(min_value=0, max_value=10_000), min_size=1, max_size=50))
def test_percentile_is_a_member_and_grows_with_q(raw: list[int]) -> None:
    """Invariant: every percentile is one of the values, and p50 <= p90 <= p99."""
    values = [Decimal(v) for v in raw]
    p50, p90, p99 = (ss.percentile(values, q) for q in (50, 90, 99))

    assert {p50, p90, p99} <= set(values)
    assert p50 <= p90 <= p99


def test_summary_counts_unmatched_and_shares_strictly_above_threshold() -> None:
    """Invariant: of 0, 15 and 30 bps, only 30 is above 15 (one third); a None is unmatched."""
    measured = [
        ss.Slippage(Decimal(0), Decimal(0)),
        ss.Slippage(Decimal(15), Decimal(15)),
        ss.Slippage(Decimal(30), Decimal(30)),
        None,
    ]

    summary = ss.summarize("BTC/USDT", _TWO, measured, Decimal(15))

    assert (summary.events, summary.unmatched) == (3, 1)
    assert summary.pessimistic_over == Decimal(1) / Decimal(3)


def _write_klines(
    root: Path, interval: str, bars: list[tuple[datetime, str, str, str, str]]
) -> None:
    month = bars[0][0]
    folder = root / "klines" / "BTCUSDT" / interval / f"{month:%Y}" / f"{month:%m}"
    folder.mkdir(parents=True, exist_ok=True)
    length = _FOUR_H if interval == "4h" else _MINUTE
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for at, o, h, lo, c in bars:
        start = int(at.timestamp() * 1000)
        end = int((at + length).timestamp() * 1000) - 1
        writer.writerow([start, o, h, lo, c, "1", end, "1", "1", "1", "1", "0"])
    with zipfile.ZipFile(folder / f"BTCUSDT-{interval}-{month:%Y-%m}.zip", "w") as archive:
        archive.writestr(f"BTCUSDT-{interval}-{month:%Y-%m}.csv", buffer.getvalue())


def test_cli_prints_the_measured_event(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Invariant: one m = 2 event whose touch minute has low 95.04 prints 100.0 bps pessimistic."""
    four_h = [(_T0 + i * _FOUR_H, "100", "101", "99", "100") for i in range(30)]
    four_h.append((_T0 + 30 * _FOUR_H, "100", "101", "95", "100"))
    _write_klines(tmp_path, "4h", four_h)
    _write_klines(tmp_path, "1m", [(_T0 + 30 * _FOUR_H, "97", "101", "95.04", "97")])

    code = ss.main(["--data-dir", str(tmp_path), "--pair", "BTC/USDT", "--multiple", "2.0"])

    row = capsys.readouterr().out.splitlines()[-1]
    assert code == 0
    assert row.startswith(
        "| BTC/USDT | 2.0 | 1 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 100.0 | 100.0 | 100.0 |"
    )


def test_cli_stops_at_train_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Invariant: a July 2024 (Validation) crash bar in the same directory adds no event.

    The default ingest keeps Validation months next to Train ones; the run must
    measure only Train and still succeed.
    """
    four_h = [(_T0 + i * _FOUR_H, "100", "101", "99", "100") for i in range(30)]
    four_h.append((_T0 + 30 * _FOUR_H, "100", "101", "95", "100"))
    _write_klines(tmp_path, "4h", four_h)
    _write_klines(tmp_path, "1m", [(_T0 + 30 * _FOUR_H, "97", "101", "95.04", "97")])
    july = datetime(2024, 7, 1, tzinfo=UTC)
    _write_klines(tmp_path, "4h", [(july, "100", "101", "50", "60")])
    _write_klines(tmp_path, "1m", [(july, "100", "101", "50", "60")])

    code = ss.main(["--data-dir", str(tmp_path), "--pair", "BTC/USDT", "--multiple", "2.0"])

    row = capsys.readouterr().out.splitlines()[-1]
    assert code == 0
    assert row.startswith("| BTC/USDT | 2.0 | 1 | 0 |")


def _trade(minutes: int, price: str) -> tuple[datetime, Decimal]:
    return _T0 + timedelta(minutes=minutes), Decimal(price)


def test_trigger_is_first_trade_at_or_below_stop_inside_the_bar() -> None:
    """Invariant: trades at 96.5, 95.52, 94 after the open trigger at 95.52, the first <= 96."""
    trades = [_trade(-1, "90"), _trade(1, "96.5"), _trade(2, "95.52"), _trade(3, "94")]

    assert ss.first_trade_at_or_below(trades, _event()) == Decimal("95.52")


def test_trade_exactly_at_stop_triggers() -> None:
    """Invariant: a trade printing exactly the 96 stop is the trigger (at or below)."""
    assert ss.first_trade_at_or_below([_trade(1, "96")], _event()) == Decimal(96)


def test_trade_at_bar_close_belongs_to_the_next_bar() -> None:
    """Invariant: a trade at 90 at the close (exclusive) triggers the next bar, not this one."""
    trades = [_trade(1, "97"), _trade(240, "90")]
    later = ss.StopEvent(_TWO, _T0 + _FOUR_H, _T0 + 2 * _FOUR_H, Decimal(96), Decimal(96))

    found = (
        ss.first_trade_at_or_below(trades, _event()),
        ss.first_trade_at_or_below(trades, later),
    )

    assert found == (None, Decimal(90))


def test_trigger_slippage_is_bps_below_model_fill() -> None:
    """Invariant: a 95.52 trigger against a 96 model fill is 50 bps."""
    assert ss.trigger_slippage(_event(), Decimal("95.52")) == Decimal(50)


def test_sample_days_are_spread_evenly_and_distinct() -> None:
    """Invariant: 3 of 9 event days (two events each) are days 0, 3 and 6; more gives all 9."""
    events = [
        ss.StopEvent(_TWO, _T0 + timedelta(days=d, hours=h), _T0, Decimal(1), Decimal(1))
        for d in range(9)
        for h in (0, 4)
    ]

    assert ss.sample_days(events, 3) == [(_T0 + timedelta(days=d)).date() for d in (0, 3, 6)]
    assert ss.sample_days(events, 50) == [(_T0 + timedelta(days=d)).date() for d in range(9)]


def test_trade_reader_skips_header_and_reads_time_and_price(tmp_path: Path) -> None:
    """Invariant: an aggTrades row yields (transact_time, price); a header row is skipped."""
    stamp = int(_T0.timestamp() * 1000)
    body = "agg_trade_id,price,qty,first,last,time,maker\n" + f"7,95.52,1,1,1,{stamp},True\n"
    archive = tmp_path / "BTCUSDT-aggTrades-2021-01-01.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("BTCUSDT-aggTrades-2021-01-01.csv", body)

    assert list(ss.read_trades(archive)) == [(_T0, Decimal("95.52"))]


def test_trade_summary_counts_unmatched() -> None:
    """Invariant: of 10 and 20 bps plus one unmatched, p50 is 10 and half are above 15."""
    summary = ss.summarize_trades("BTC/USDT", _TWO, [Decimal(10), Decimal(20), None], Decimal(15))

    assert (summary.events, summary.unmatched, summary.slippage[50]) == (2, 1, Decimal(10))
    assert summary.over == Decimal("0.5")
