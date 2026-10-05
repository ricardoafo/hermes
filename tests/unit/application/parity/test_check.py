"""Tests for the one-day parity check.

Invariants protected here (openspec/changes/358-parity-check/change.md):

- the same bars through serve's recorded decisions and the replay are in parity;
- a delivery order that differs from serve's (open time instead of close
  time, the #343 R-4 bug) changes the replay's decisions, and the check names
  the bar where they diverge;
- a signal on one side only, or executed on one side and declined on the
  other, is a mismatch;
- the strategy is seeded with the bars before the day, never with the day's;
- a day with no stored bars, or too little history to warm up, is a data
  error and not a parity verdict.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from hermes.application.parity.check import HistoryFetcher, ParityDataError, check_day, day_window
from hermes.application.parity.decisions import ParityReport
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import order_for_delivery
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.pair import Pair
from hermes.ports.strategy import StrategyPort
from tests.support.fill_kernel import BTC_USDT, STRATEGY, fees, kernel, seed
from tests.support.parity import (
    D1,
    DAY_END,
    DAY_START,
    H4,
    AfterDailyStrategy,
    FakeSource,
    ScriptedExit,
    ScriptedStrategy,
    bar,
    day_bars,
    signal_event,
    trade_event,
    warmup_bars,
)

_DAY = date(2026, 10, 3)
_FIRST_FOUR_HOUR = DAY_START + timedelta(hours=4)
_SEC = timedelta(seconds=5)


def _fees_at(at: datetime, pair: Pair) -> FeeSchedule:
    _ = at
    return fees(pair)


def _check(
    source: FakeSource,
    make_strategy: Callable[[], StrategyPort],
    *,
    order: Callable[[Iterable[Bar]], tuple[Bar, ...]] = order_for_delivery,
    history: HistoryFetcher | None = None,
) -> ParityReport:
    return check_day(
        day=_DAY,
        source=source,
        strategy_id=STRATEGY,
        pairs=[BTC_USDT],
        intervals=[H4, D1],
        warmup_bars={H4: 2, D1: 1},
        min_warmup_bars={H4: 1, D1: 1},
        seed=seed(),
        make_strategy=make_strategy,
        make_kernel=kernel,
        fees_at=_fees_at,
        order=order,
        history=history,
        now=DAY_END + timedelta(minutes=10),
    )


def _source(
    *, signals: list[SignalEmitted] | None = None, fills: list[TradeExecuted] | None = None
) -> FakeSource:
    return FakeSource(bars=[*warmup_bars(), *day_bars()], signals=signals or [], fills=fills or [])


def test_day_window_is_the_utc_day_and_includes_its_closing_1d_bar() -> None:
    """Bars closing in (00:00, next 00:00] belong to the day, so its 1d bar is the last."""
    assert day_window(_DAY) == (DAY_START, DAY_END)


def test_same_bars_and_same_recorded_decisions_are_in_parity() -> None:
    """Serve recorded the entry the strategy produces and its fill: nothing differs."""
    at = _FIRST_FOUR_HOUR + timedelta(hours=4)
    source = _source(signals=[signal_event(at)], fills=[trade_event(at + _SEC)])

    report = _check(source, lambda: ScriptedStrategy(frozenset({at})))

    assert (report.in_parity, report.matched, report.mismatches) == (True, 1, ())


def test_a_quiet_day_is_in_parity_with_zero_signals() -> None:
    """No signal live and none in the replay."""
    report = _check(_source(), lambda: ScriptedStrategy(frozenset()))

    assert (report.in_parity, report.matched) == (True, 0)


def test_open_time_delivery_order_is_caught_and_the_bar_is_named() -> None:
    """Serve delivered in close-time order; a replay ordered by open time diverges.

    The order-sensitive strategy enters on the 4h bar right after a 1d bar. In
    close-time order that is the 4h bar closing with the 1d bar, at the day's
    end. In open-time order the 1d bar comes before every 4h bar, so it is the
    day's first 4h bar. Both bars must be reported.
    """
    source = _source(signals=[signal_event(DAY_END)], fills=[trade_event(DAY_END + _SEC)])

    def by_open_time(bars: Iterable[Bar]) -> tuple[Bar, ...]:
        return tuple(sorted(bars, key=lambda b: (b.open_time, b.interval is not D1)))

    assert _check(source, AfterDailyStrategy).in_parity
    report = _check(source, AfterDailyStrategy, order=by_open_time)

    assert [m.line() for m in report.mismatches] == [
        f"MISMATCH {_FIRST_FOUR_HOUR.isoformat()} BTC/USDT: "
        "serve no signal; replay entry long executed",
        f"MISMATCH {DAY_END.isoformat()} BTC/USDT: serve entry long executed; replay no signal",
    ]


def test_a_signal_serve_emitted_that_the_replay_did_not_is_a_mismatch() -> None:
    """Serve recorded an entry the strategy does not produce from the same bars."""
    source = _source(
        signals=[signal_event(_FIRST_FOUR_HOUR)], fills=[trade_event(_FIRST_FOUR_HOUR + _SEC)]
    )

    report = _check(source, lambda: ScriptedStrategy(frozenset()))

    assert [m.line() for m in report.mismatches] == [
        f"MISMATCH {_FIRST_FOUR_HOUR.isoformat()} BTC/USDT: "
        "serve entry long executed; replay no signal"
    ]


def test_a_signal_the_replay_produces_that_serve_did_not_emit_is_a_mismatch() -> None:
    """The strategy fires on the stored bars but serve recorded nothing."""
    report = _check(_source(), lambda: ScriptedStrategy(frozenset({_FIRST_FOUR_HOUR})))

    assert [m.line() for m in report.mismatches] == [
        f"MISMATCH {_FIRST_FOUR_HOUR.isoformat()} BTC/USDT: "
        "serve no signal; replay entry long executed"
    ]


def test_executed_in_the_replay_but_not_by_serve_is_a_mismatch() -> None:
    """Serve emitted the signal but recorded no fill; the replay fills it."""
    source = _source(signals=[signal_event(_FIRST_FOUR_HOUR)])

    report = _check(source, lambda: ScriptedStrategy(frozenset({_FIRST_FOUR_HOUR})))

    assert [m.line() for m in report.mismatches] == [
        f"MISMATCH {_FIRST_FOUR_HOUR.isoformat()} BTC/USDT: "
        "serve entry long not executed; replay entry long executed"
    ]


def test_executed_by_serve_but_declined_in_the_replay_names_the_replay_rule() -> None:
    """A zero-volume bar leaves the replay without a book, so it declines as no-book."""
    zero_volume = bar(_FIRST_FOUR_HOUR, volume="0")
    source = FakeSource(
        bars=[
            *warmup_bars(),
            *[b for b in day_bars() if b.close_time != _FIRST_FOUR_HOUR or b.interval is not H4],
            zero_volume,
        ],
        signals=[signal_event(_FIRST_FOUR_HOUR)],
        fills=[trade_event(_FIRST_FOUR_HOUR + _SEC)],
    )

    report = _check(source, lambda: ScriptedStrategy(frozenset({_FIRST_FOUR_HOUR})))

    assert [m.line() for m in report.mismatches] == [
        f"MISMATCH {_FIRST_FOUR_HOUR.isoformat()} BTC/USDT: "
        "serve entry long executed; replay entry long not executed (no-book)"
    ]


def test_a_fill_that_belongs_to_an_earlier_signal_does_not_count_for_a_later_one() -> None:
    """One fill is claimed by one signal: the second signal of the pair stays unexecuted."""
    second = _FIRST_FOUR_HOUR + timedelta(hours=8)
    source = _source(
        signals=[signal_event(_FIRST_FOUR_HOUR), signal_event(second)],
        fills=[trade_event(_FIRST_FOUR_HOUR + _SEC)],
    )

    report = _check(source, lambda: ScriptedStrategy(frozenset({_FIRST_FOUR_HOUR})))

    assert [m.bar_close for m in report.mismatches] == [second]
    assert report.mismatches[0].serve == "entry long not executed"


def test_the_strategy_is_seeded_with_the_bars_before_the_day_only() -> None:
    """Warm-up bars seed the strategy; the day's bars reach it through the replay."""
    seeded: list[ScriptedStrategy] = []

    def make() -> ScriptedStrategy:
        strategy = ScriptedStrategy(frozenset())
        seeded.append(strategy)
        return strategy

    _check(_source(), make)

    assert [b.close_time for b in seeded[0].seeded] == [
        DAY_START - timedelta(hours=4),
        DAY_START,
        DAY_START,
    ]
    assert [b.interval for b in seeded[0].seeded][-2:] == [D1, H4]


def test_a_position_serve_held_at_the_start_of_the_day_is_restored() -> None:
    """An exit serve filled on the day matches when the replay still holds the position."""
    entry_at = DAY_START - timedelta(hours=4)
    exit_at = _FIRST_FOUR_HOUR

    source = _source(
        signals=[signal_event(entry_at), signal_event(exit_at, SignalKind.EXIT)],
        fills=[
            trade_event(entry_at + _SEC),
            trade_event(exit_at + _SEC, TradeSide.SELL),
        ],
    )

    report = _check(source, lambda: ScriptedExit(exit_at))

    assert report.mismatches == ()
    assert report.matched == 1


def test_a_day_without_stored_bars_is_a_data_error() -> None:
    """Nothing closed on the day in the ledger: no verdict, not a parity pass."""
    source = FakeSource(bars=warmup_bars())

    with pytest.raises(ParityDataError, match=r"no stored BTC/USDT 4h bars close on 2026-10-03"):
        _check(source, lambda: ScriptedStrategy(frozenset()))


def test_too_little_history_to_warm_up_is_a_data_error() -> None:
    """Fewer stored 1d bars than the minimum would leave the replay under-seeded."""
    source = FakeSource(bars=[*[b for b in warmup_bars() if b.interval is H4], *day_bars()])

    with pytest.raises(ParityDataError, match=r"BTC/USDT 1d: 0 bars before 2026-10-03"):
        _check(source, lambda: ScriptedStrategy(frozenset()))


def _only_the_day() -> FakeSource:
    return FakeSource(bars=day_bars())


def _make(seeded: list[ScriptedStrategy]) -> Callable[[], ScriptedStrategy]:
    def make() -> ScriptedStrategy:
        strategy = ScriptedStrategy(frozenset())
        seeded.append(strategy)
        return strategy

    return make


def test_a_warm_up_shortfall_is_filled_from_the_exchange_and_gives_a_verdict() -> None:
    """Stored bars cover only the day; the exchange read supplies the history: a verdict."""
    asked: list[tuple[KlineInterval, int]] = []
    seeded: list[ScriptedStrategy] = []

    def fetch(pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
        asked.append((interval, count))
        return [b for b in [*warmup_bars(), *day_bars()] if b.interval is interval]

    report = _check(_only_the_day(), _make(seeded), history=fetch)

    assert (report.in_parity, report.matched) == (True, 0)
    assert sorted(asked, key=lambda a: a[0].value) == [(D1, 4), (H4, 10)]
    assert [(b.interval, b.close_time) for b in seeded[0].seeded] == [
        (H4, DAY_START - timedelta(hours=4)),
        (D1, DAY_START),
        (H4, DAY_START),
    ]


def test_stored_bars_win_over_the_exchange_where_both_exist() -> None:
    """A bar stored for the same open time is kept as stored, not replaced by the exchange."""
    stored = bar(DAY_START - timedelta(hours=4))
    other = replace(stored, close=stored.close + 1, high=stored.high + 1)
    seeded: list[ScriptedStrategy] = []

    def fetch(pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
        return [other] if interval is H4 else [bar(DAY_START, D1)]

    _check(FakeSource(bars=[stored, *day_bars()]), _make(seeded), history=fetch)

    assert [b for b in seeded[0].seeded if b.interval is H4] == [stored]


def test_the_exchange_is_not_read_when_the_stored_bars_are_enough() -> None:
    """No shortfall, no request."""

    def fetch(pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
        raise AssertionError("stored bars were enough")

    report = _check(_source(), lambda: ScriptedStrategy(frozenset()), history=fetch)

    assert report.in_parity


def test_bars_the_exchange_returns_from_the_day_itself_are_never_used() -> None:
    """The day bars come from the ledger only; the exchange later bars are ignored."""
    seeded: list[ScriptedStrategy] = []

    def fetch(pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
        return [b for b in [*warmup_bars(), *day_bars()] if b.interval is interval]

    _check(_only_the_day(), _make(seeded), history=fetch)

    assert max(b.close_time for b in seeded[0].seeded) == DAY_START


def test_still_short_after_the_exchange_read_is_a_data_error() -> None:
    """An exchange that has nothing older leaves the shortfall reported."""

    def fetch(pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
        return []

    with pytest.raises(ParityDataError, match=r"BTC/USDT 4h: 0 bars before 2026-10-03"):
        _check(_only_the_day(), lambda: ScriptedStrategy(frozenset()), history=fetch)
