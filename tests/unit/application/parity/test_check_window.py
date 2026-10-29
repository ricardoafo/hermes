"""Tests for the whole-window replay behind M-09 and M-10 (#965).

Invariants protected here (openspec/archive/965-shadow-parity-window/change.md):

- the replay starts flat at day 0 and executes only signals after it;
- it never reads serve's trades, so its fills are priced from the bars and not
  copied from the ledger (R-3, an independent replay);
- it hands back the window's stored bars in delivery order, for the shadow's
  rebuilt curve;
- a window without stored bars, or without enough bars before it to warm up,
  is a data error.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from hermes.application.parity.check import ParityDataError, WindowReplay, replay_window
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.market_data.bar import Bar
from hermes.domain.value_objects.price import Price
from tests.support.fill_kernel import BTC_USDT, fees_at, kernel, seed
from tests.support.parity import (
    D1,
    DAY_END,
    DAY_START,
    H4,
    FakeSource,
    ScriptedStrategy,
    bar,
    day_bars,
    trade_event,
    warmup_bars,
)

_FIRST = DAY_START + timedelta(hours=4)


def _replay(
    source: FakeSource, entries: frozenset[datetime], *, min_warmup: int = 1
) -> WindowReplay:
    return replay_window(
        start=DAY_START,
        end=DAY_END,
        source=source,
        pairs=[BTC_USDT],
        intervals=[H4, D1],
        warmup_bars={H4: 2, D1: 1},
        min_warmup_bars={H4: min_warmup, D1: 1},
        seed=seed(),
        make_strategy=lambda: ScriptedStrategy(entries),
        make_kernel=kernel,
        fees_at=fees_at,
    )


def _source(bars: list[Bar] | None = None) -> FakeSource:
    return FakeSource(bars=bars if bars is not None else [*warmup_bars(), *day_bars()])


def test_a_signal_after_day_zero_is_executed_from_flat() -> None:
    """The scripted entry on the first window bar fills once, as a BUY."""
    replayed = _replay(_source(), frozenset({_FIRST}))

    assert [(t.side, t.occurred_at) for t in replayed.run.trades] == [(TradeSide.BUY, _FIRST)]


def test_a_signal_at_or_before_day_zero_is_warm_up_and_not_executed() -> None:
    """The bars closing at and before day 0 warm the strategy up; their entries never fill."""
    replayed = _replay(_source(), frozenset({DAY_START, DAY_START - timedelta(hours=4)}))

    assert replayed.run.trades == []


def test_the_replay_prices_fills_from_the_bars_not_from_serve_trades() -> None:
    """Serve's ledger holds a fill at 2618; the replay's own fill is priced near the bar's 100."""
    source = _source()
    serve_fill = replace(trade_event(_FIRST), price=Price(amount=Decimal(2618), pair=BTC_USDT))
    source.fills = [serve_fill]

    replayed = _replay(source, frozenset({_FIRST}))

    assert [t.price.amount < Decimal(200) for t in replayed.run.trades] == [True]


def test_the_window_bars_come_back_in_delivery_order_without_the_warm_up() -> None:
    """Six 4h bars and the day's 1D bar, 1D first where both close at the day's end."""
    replayed = _replay(_source(), frozenset())

    assert [(b.close_time, b.interval) for b in replayed.bars] == [
        *[(DAY_START + timedelta(hours=4 * n), H4) for n in range(1, 6)],
        (DAY_END, D1),
        (DAY_END, H4),
    ]


def test_the_replay_curve_has_the_1d_closes_after_day_zero_only() -> None:
    """The warm-up 1D bar closes at day 0 and is not on the curve."""
    replayed = _replay(_source(), frozenset())

    assert [at for at, _ in replayed.run.daily_equity] == [DAY_END]


def test_a_window_without_stored_bars_is_a_data_error() -> None:
    """Only warm-up bars are stored: there is nothing to replay."""
    with pytest.raises(ParityDataError, match="no stored BTC/USDT 4h bars"):
        _replay(_source(warmup_bars()), frozenset())


def test_too_few_bars_before_day_zero_is_a_data_error() -> None:
    """Two 4h warm-up bars are stored and three are needed."""
    with pytest.raises(ParityDataError, match="2 bars before"):
        _replay(_source(), frozenset(), min_warmup=3)


def test_the_flat_seed_is_where_the_replay_starts() -> None:
    """With no signal, the curve's only point is the seed's cash: nothing was carried in."""
    flat = bar(DAY_END, D1)
    replayed = _replay(_source([*warmup_bars(), *day_bars()[:-1], flat]), frozenset())

    assert replayed.run.daily_equity == [(DAY_END, seed().equity)]
