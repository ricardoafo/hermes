"""End-to-end: ``hermes serve`` running swing-mvp1-v2 sells a stop exit at the bar close.

Each test boots the real composition root on the repository's ``config/`` with
the shipped ``swing-mvp1-v2`` entry enabled in place of v1, passes
``check_startup``, and feeds the pre-registered swing strategy (its signed
periods, 300 daily bars and 45 4h bars of warm-up) a synthetic BTC/USDT market:
a steady 4h uptrend that enters on the first live bar, one bar held, then a bar
that opens over the chandelier stop, trades through it and closes far under it.
Only the bar source, the clock, the depth source and the FX provider are
replaced; no network is touched and no exchange is wired, so no order can be
placed (ADR-003 §00 I-08).

Invariants protected here (v2-strategy-config R-4, pre-registration v2 §5.3,
§6.2, §7):

- with no live book at the exit, serve sells the v2 stop exit at the exit
  bar's close x 0.9995, and records that price as the drift model price;
- with a live book at the exit, the fill takes the live bid and the drift
  model price is still the close x 0.9995;
- the same market under the shipped v1 entry still sells at the stop x
  0.9985, so v1's exit price is unchanged.
"""

import dataclasses
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.domain.errors import NetworkError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import close_time_of, delivery_key
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.strategy.swing.engines.chandelier import ChandelierEngine
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.loader import load_config
from tests.support.serve_loop import (
    BTC,
    PROJECT_ROOT,
    FakeClock,
    SteppingFeed,
    events,
    run_once,
)
from tests.unit.application.ledger.fake_fx import FakeFx

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(300)]

V2_ENV: Mapping[str, str] = {
    "HERMES_STRATEGIES__STRATEGIES__0__ENABLED": "false",
    "HERMES_STRATEGIES__STRATEGIES__1__ENABLED": "true",
}
"""The shipped roster with v1 (entry 0) off and swing-mvp1-v2 (entry 1) on."""

_DAY0 = datetime(2023, 1, 1, tzinfo=UTC)
_DAILY_BARS = 300
_H4_WARM_BARS = 45
_H4 = timedelta(hours=4)
_LIVE_FROM = _DAY0 + timedelta(days=_DAILY_BARS)
"""Open of the first live 4h bar: the UTC midnight the last warm-up daily bar closes at."""

_STEP = Decimal("3")
_H4_FIRST_CLOSE = Decimal("1500")
_DELIVERY_DELAY = timedelta(seconds=6)

CRASH_CLOSE = Decimal("1200")
"""Close of the exit bar, far under any stop the uptrend posts."""

V2_EXIT_PRICE = CRASH_CLOSE * Decimal("0.9995")
"""Pre-registration v2 §6.2: every exit 5 bps under the exit bar's close."""

_QUANTUM = Decimal("1E-8")


def _bar(interval: KlineInterval, open_time: datetime, close: Decimal) -> Bar:
    """Return a rising bar: it opens at its low, two under the close, and peaks two over."""
    return Bar(
        pair=BTC,
        interval=interval,
        open_time=open_time,
        open=close - 2,
        high=close + 2,
        low=close - 2,
        close=close,
        volume=Decimal("1000"),
        close_time=close_time_of(open_time, interval),
    )


def _daily() -> list[Bar]:
    """Return 300 daily bars rising 2 a day, the last closing at :data:`_LIVE_FROM`."""
    return [
        _bar(KlineInterval.D1, _DAY0 + timedelta(days=i), Decimal(1000 + 2 * i))
        for i in range(_DAILY_BARS)
    ]


def _h4(index: int) -> Bar:
    """Return uptrend 4h bar ``index``; 0..44 are warm-up, 45 opens at :data:`_LIVE_FROM`.

    Each close is 3 over the last, one over the last high, so every bar breaks
    the 20-bar Donchian high and ADX(14) runs at its maximum.
    """
    open_time = _LIVE_FROM - _H4 * _H4_WARM_BARS + _H4 * index
    return _bar(KlineInterval.H4, open_time, _H4_FIRST_CLOSE + _STEP * index)


def _crash(index: int) -> Bar:
    """Return 4h bar ``index`` opening on the uptrend, falling to 1000 and closing at 1200."""
    previous = _h4(index - 1)
    open_time = previous.open_time + _H4
    return Bar(
        pair=BTC,
        interval=KlineInterval.H4,
        open_time=open_time,
        open=previous.close,
        high=previous.close + 2,
        low=Decimal("1000"),
        close=CRASH_CLOSE,
        volume=Decimal("1000"),
        close_time=close_time_of(open_time, KlineInterval.H4),
    )


ENTRY_INDEX = _H4_WARM_BARS
EXIT_INDEX = _H4_WARM_BARS + 2
WARM = sorted([*_daily(), *(_h4(i) for i in range(_H4_WARM_BARS))], key=delivery_key)
LIVE = [_h4(ENTRY_INDEX), _h4(ENTRY_INDEX + 1), _crash(EXIT_INDEX)]


class _Books:
    """Depth source answering a tight, deep book around the last delivered close.

    With ``down_at_exit`` it fails for the exit bar, like a depth outage, so
    serve falls back to the pre-registration §6.2 book.
    """

    def __init__(self, clock: FakeClock, *, down_at_exit: bool) -> None:
        """Bind the clock the feed moves; no price until the first bar arrives."""
        self.clock = clock
        self.down_at_exit = down_at_exit
        self.mid = Decimal("0")

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Return a two-level book one unit either side of ``mid``, or fail at the exit."""
        if self.down_at_exit and self.mid == CRASH_CLOSE:
            raise NetworkError(f"depth for {pair} unavailable")
        return Orderbook(
            pair=pair,
            bids=(
                OrderbookLevel(price=self.mid - 1, quantity=Decimal("10")),
                OrderbookLevel(price=self.mid - 2, quantity=Decimal("100")),
            ),
            asks=(
                OrderbookLevel(price=self.mid + 1, quantity=Decimal("10")),
                OrderbookLevel(price=self.mid + 2, quantity=Decimal("100")),
            ),
            snapshot_time=self.clock.current,
        )

    async def aclose(self) -> None:
        """Nothing to release."""


def _deliver(clock: FakeClock, books: _Books) -> Callable[[Bar], None]:
    """Return a feed step that moves clock and book mid to six seconds after each close."""

    def step(bar: Bar) -> None:
        clock.current = bar.close_time + _DELIVERY_DELAY
        books.mid = bar.close

    return step


def _serve(db: Path, env: Mapping[str, str], *, down_at_exit: bool) -> int:
    """Boot serve on the shipped config with ``env`` over the synthetic market; return its code."""
    config = load_config(PROJECT_ROOT / "config", env=dict(env))
    serve_module.check_startup(config, env={})
    clock = FakeClock(WARM[-1].close_time + _DELIVERY_DELAY)
    books = _Books(clock, down_at_exit=down_at_exit)
    feed = SteppingFeed(_deliver(clock, books), warm=WARM, live_bars=LIVE)
    composition = serve_module.build_composition(
        config,
        db_path=db,
        env={},
        feed=feed,
        clock=clock,
        orderbooks=books,
        fx=FakeFx(down=True),
        new_ledger=True,
    )
    btc_only = dataclasses.replace(
        composition, strategies={BTC: composition.strategies[BTC]}, pairs=(BTC,)
    )
    return run_once(btc_only, feed)


def _payloads(db: Path, event_type: str) -> list[dict[str, object]]:
    """Return the decoded payloads of one event type, oldest first."""
    return [json.loads(row["payload_json"]) for row in events(db, event_type)]


def _amount(value: object) -> Decimal:
    """Return a ledger money or price field, ``{"amount": ...}`` or a bare string, as a Decimal."""
    if isinstance(value, dict):
        return Decimal(str(value["amount"]))
    return Decimal(str(value))


def _sell(db: Path) -> tuple[Decimal, Decimal]:
    """Return the SELL trade's price and its quality row's model price, to 8 decimals."""
    (trade,) = [t for t in _payloads(db, "TradeExecuted") if t["side"] == "SELL"]
    (quality,) = [q for q in _payloads(db, "FillQualityRecorded") if q["side"] == "SELL"]
    return (
        _amount(trade["price"]).quantize(_QUANTUM),
        _amount(quality["model_price"]).quantize(_QUANTUM),
    )


def _signals(db: Path) -> list[tuple[object, object]]:
    """Return ``(kind, exit_reason)`` of every SignalEmitted row."""
    return [(p["kind"], p["exit_reason"]) for p in _payloads(db, "SignalEmitted")]


def test_v2_serve_sells_a_stop_exit_at_the_bar_close_without_a_live_book(
    tmp_path: Path,
) -> None:
    """v2 §5.3/§6.2: the chandelier exit sells at 1200 x 0.9995, also the drift model price."""
    db = tmp_path / "ledger.sqlite3"

    code = _serve(db, V2_ENV, down_at_exit=True)

    assert (code, _signals(db), _sell(db)) == (
        0,
        [("entry", None), ("exit", "chandelier")],
        (V2_EXIT_PRICE.quantize(_QUANTUM), V2_EXIT_PRICE.quantize(_QUANTUM)),
    )


def test_v2_serve_measures_a_live_stop_exit_against_the_bar_close(tmp_path: Path) -> None:
    """v2 §7: a live exit fills on the bid 1199 and its drift model price is 1200 x 0.9995."""
    db = tmp_path / "ledger.sqlite3"

    code = _serve(db, V2_ENV, down_at_exit=False)

    assert (code, _sell(db)) == (
        0,
        ((CRASH_CLOSE - 1).quantize(_QUANTUM), V2_EXIT_PRICE.quantize(_QUANTUM)),
    )


def test_v1_serve_still_sells_the_same_stop_exit_at_the_stop(tmp_path: Path) -> None:
    """v1 is unchanged: on the same market its stop-market exit sells at the stop x 0.9985."""
    db = tmp_path / "ledger.sqlite3"
    entry_history = [b for b in WARM if b.interval is KlineInterval.H4] + [LIVE[0]]
    stop = ChandelierEngine.trailing_stop(entry_history, period=22, atr_mult=Decimal("3.0"))
    assert stop is not None
    v1_price = (min(stop, LIVE[2].open) * Decimal("0.9985")).quantize(_QUANTUM)

    code = _serve(db, {}, down_at_exit=True)

    assert (code, _sell(db)) == (0, (v1_price, v1_price))
