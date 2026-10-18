"""Tests for the one-day parity check.

Invariants protected here (openspec/changes/358-parity-check/change.md):

- the same bars through serve's recorded decisions and the replay are in parity;
- a delivery order that differs from serve's (open time instead of close
  time, the #343 R-4 bug) changes the replay's decisions, and the check names
  the bar where they diverge;
- a signal on one side only, or executed on one side and declined on the
  other, is a mismatch;
- the strategy is seeded with the bars before the day, never with the day's;
- as in serve, a held position is seeded from its entry past any exit declined
  after it, and an exit declined on the day returns the strategy to that
  entry (#706);
- a day with no stored bars, or too little history to warm up, is a data
  error and not a parity verdict;
- the replay's RO-01 reads the daily closes before the day, so a third
  correlated entry serve refused is refused in the replay too (#514).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.application.execution.kernel import FillKernel
from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.parity.check import HistoryFetcher, ParityDataError, check_day, day_window
from hermes.application.parity.decisions import ParityReport
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import order_for_delivery
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.ports.strategy import StrategyPort
from tests.support.fill_kernel import (
    BTC_USDT,
    SIZING,
    STRATEGY,
    fees,
    kernel,
    kill_evaluator,
    seed,
)
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


def _exit_maker(exit_at: datetime, made: list[ScriptedExit]) -> Callable[[], ScriptedExit]:
    def make() -> ScriptedExit:
        strategy = ScriptedExit(exit_at)
        made.append(strategy)
        return strategy

    return make


def test_a_held_position_is_seeded_from_its_entry_past_a_declined_exit() -> None:
    """Serve declined an exit before the day; the coin is held, so the entry is seeded (#706)."""
    entry_at = DAY_START - timedelta(hours=8)
    declined_at = DAY_START - timedelta(hours=4)
    exit_at = _FIRST_FOUR_HOUR
    source = _source(
        signals=[
            signal_event(entry_at),
            signal_event(declined_at, SignalKind.EXIT),
            signal_event(exit_at, SignalKind.EXIT),
        ],
        fills=[trade_event(entry_at + _SEC), trade_event(exit_at + _SEC, TradeSide.SELL)],
    )
    made: list[ScriptedExit] = []

    report = _check(source, _exit_maker(exit_at, made))

    assert made[0].seeded_position == OpenPosition(
        entry_time=entry_at, entry_price=Decimal("2618.45")
    )
    assert (report.in_parity, report.matched) == (True, 1)


def test_an_exit_declined_on_the_day_returns_the_strategy_to_its_entry() -> None:
    """A no-book exit leaves the coin held, so the strategy gets its entry back (#706)."""
    entry_at = DAY_START - timedelta(hours=4)
    exit_at = _FIRST_FOUR_HOUR
    bars = [
        replace(b, volume=Decimal("0")) if b.interval is H4 and b.close_time == exit_at else b
        for b in day_bars()
    ]
    source = FakeSource(
        bars=[*warmup_bars(), *bars],
        signals=[signal_event(entry_at), signal_event(exit_at, SignalKind.EXIT)],
        fills=[trade_event(entry_at + _SEC)],
    )
    made: list[ScriptedExit] = []

    report = _check(source, _exit_maker(exit_at, made))

    assert made[0].restored == [OpenPosition(entry_time=entry_at, entry_price=Decimal("2618.45"))]
    assert (report.in_parity, report.matched) == (True, 1)


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


_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL_USDT = Pair(base=Currency.SOL, quote=Currency.USDT)
_PAIRS = (BTC_USDT, _ETH_USDT, _SOL_USDT)


@dataclass
class _BtcEntry:
    """Enters BTC/USDT on the first 4h close of the day; the other pairs never signal."""

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Emit the BTC entry on the scripted bar."""
        if bar.pair != BTC_USDT:
            return None
        return ScriptedStrategy(frozenset({_FIRST_FOUR_HOUR})).on_bar(bar, portfolio_state)

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Seeding changes nothing; the entry is scripted."""
        _ = (bars, open_position)

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Ignore the restore: the entry is scripted."""
        _ = open_position


def _moving_together(pair: Pair) -> list[Bar]:
    """Return 32 daily bars up to the day whose closes move the same way for every pair."""
    closes = [Decimal(100 + n % 3) for n in range(32)]
    return [
        replace(
            bar(DAY_START - timedelta(days=n), D1, pair=pair),
            open=close,
            high=close,
            low=close,
            close=close,
        )
        for n, close in enumerate(closes)
    ]


def _held(pair: Pair) -> TradeExecuted:
    """Return a buy of ``pair`` recorded the day before the checked day."""
    return replace(
        trade_event(DAY_START - timedelta(days=1)),
        pair=pair,
        qty=Quantity(amount=Decimal("0.01"), asset=pair.base),
        price=Price(amount=Decimal("100"), pair=pair),
    )


def test_ro01_refuses_in_the_replay_on_the_closes_before_the_day_as_in_serve() -> None:
    """#514: serve's RO-01 refusal of a third correlated entry is in parity with the replay.

    ETH and SOL are held and all three pairs' daily closes before the day move
    together, so serve declined the BTC entry on RO-01. The replay only sees
    the day's bars one at a time; it is seeded with the closes before the day
    and refuses the same entry.
    """
    source = FakeSource(
        bars=[
            b
            for pair in _PAIRS
            for b in (
                *[w for w in warmup_bars(pair=pair) if w.interval is H4],
                *_moving_together(pair),
                *day_bars(pair=pair),
            )
        ],
        signals=[signal_event(_FIRST_FOUR_HOUR)],
        fills=[_held(_ETH_USDT), _held(_SOL_USDT)],
    )

    report = check_day(
        day=_DAY,
        source=source,
        strategy_id=STRATEGY,
        pairs=list(_PAIRS),
        intervals=[H4, D1],
        warmup_bars={H4: 2, D1: 40},
        min_warmup_bars={H4: 1, D1: 1},
        seed=seed(),
        make_strategy=_BtcEntry,
        make_kernel=lambda tracker: FillKernel(
            fill_model=OrderbookWalkFillModel(),
            risk_engine=RiskEngine(rules=[CorrelationLock()]),
            kill_evaluator=kill_evaluator(),
            tracker=tracker,
            strategy_id=STRATEGY,
            sizing=SIZING,
        ),
        fees_at=_fees_at,
    )

    assert (report.matched, report.mismatches) == (1, ())


def test_a_day_with_missed_bars_is_not_compared() -> None:
    """#1078: a missed entry is left unfilled by rule, so the outage day raises no mismatch.

    The same day without the missed bar is the mismatch above; the missed bar
    alone turns it into a report that says the day was not compared.
    """
    source = _source(signals=[signal_event(_FIRST_FOUR_HOUR)])
    source.missed.append(_FIRST_FOUR_HOUR)

    report = _check(source, lambda: ScriptedStrategy(frozenset({_FIRST_FOUR_HOUR})))

    assert (report.missed_bars, report.matched, report.mismatches) == (1, 0, ())


def test_a_missed_bar_on_the_previous_day_does_not_skip_this_one() -> None:
    """#1078: only bars closing inside the checked day count; the day's start belongs before it."""
    source = _source(signals=[signal_event(_FIRST_FOUR_HOUR)])
    source.missed.append(DAY_START)

    report = _check(source, lambda: ScriptedStrategy(frozenset({_FIRST_FOUR_HOUR})))

    assert (report.missed_bars, len(report.mismatches)) == (0, 1)
