"""Backtest/live parity check for one UTC day (#358).

Replays the bars the serve loop stored for a day through the same strategy and
the same fill kernel the backtest uses, then compares the *decisions* with the
ones serve recorded: which signals fired, and which were executed. A strategy
or ordering bug that only shows up live (the #343 R-4 class) makes the two
disagree.

The replay starts where serve was at the start of the day. The strategies are
seeded from the stored bars that closed before it, as serve seeds them at
warm-up (:meth:`StrategyPort.seed` never emits a signal), and the open position
and the portfolio are restored from the ledger as serve restores them. Only
the day's bars then go through :func:`~hermes.application.backtest.replay.replay`.
Everything is read: the ledger is never written.

Reference: openspec/changes/358-parity-check/change.md.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from returns.result import Failure

from hermes.application.backtest.replay import replay
from hermes.application.execution.kernel import FillKernel
from hermes.application.parity.decisions import Decision, Outcome, ParityReport, compare
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import interval_duration, order_for_delivery
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import (
    OpenPosition,
    SignalKind,
    StrategySignal,
)
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.parity_source import ParitySourcePort
from hermes.ports.strategy import StrategyPort

__all__ = ["HistoryFetcher", "ParityDataError", "check_day", "day_window"]

_EXECUTION_WINDOW = interval_duration(KlineInterval.H4)
"""A fill belongs to a signal when it lands within one 4h bar after the signal time."""


_MAX_FETCH = 999
"""Most closed bars one public klines request returns."""

HistoryFetcher = Callable[[Pair, KlineInterval, int], Sequence[Bar]]
"""Returns the latest ``count`` closed bars of a stream, oldest first (serve's warm-up read)."""


class ParityDataError(Exception):
    """The ledger lacks the data to run the check; the command exits 3."""


def day_window(day: date) -> tuple[datetime, datetime]:
    """Return ``(start, end)`` of a UTC day: bars closing in ``(start, end]`` belong to it.

    The 1d bar of ``day`` closes at ``end``, so it is the day's last bar.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


@dataclass
class _PairRouter:
    """Routes each bar to its pair's strategy and remembers the last signal."""

    strategies: Mapping[Pair, StrategyPort]
    last: StrategySignal | None = field(default=None)

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Hand ``bar`` to its pair's strategy, as serve does."""
        self.last = self.strategies[bar.pair].on_bar(bar, portfolio_state)
        return self.last

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Do nothing: each pair's strategy is seeded before the router is built."""
        _ = (bars, open_position)


def check_day(
    *,
    day: date,
    source: ParitySourcePort,
    strategy_id: StrategyId,
    pairs: Sequence[Pair],
    intervals: Sequence[KlineInterval],
    warmup_bars: Mapping[KlineInterval, int],
    min_warmup_bars: Mapping[KlineInterval, int],
    seed: PortfolioSnapshot,
    make_strategy: Callable[[], StrategyPort],
    make_kernel: Callable[[PortfolioTracker], FillKernel],
    fees_at: Callable[[datetime, Pair], FeeSchedule],
    order: Callable[[Iterable[Bar]], tuple[Bar, ...]] = order_for_delivery,
    history: HistoryFetcher | None = None,
    now: datetime | None = None,
) -> ParityReport:
    """Replay ``day`` from the stored bars and compare it with what serve recorded.

    Args:
        day: The UTC day to check.
        source: Read-only ledger view.
        strategy_id: The strategy bucket whose signals and trades are compared.
        pairs: Pairs serve follows.
        intervals: Intervals serve follows.
        warmup_bars: How many closed bars per interval serve warms up with;
            that many stored bars before the day are used to seed the replay.
        min_warmup_bars: Fewest stored bars per interval that still warm the
            strategy up fully.
        seed: The portfolio the strategy started from.
        make_strategy: Builds one fresh strategy per pair, as serve does.
        make_kernel: Builds the fill kernel over the given portfolio tracker.
        fees_at: Fee schedule for a pair at a time.
        order: Delivery order of the day's bars. Defaults to the one serve
            uses; tests swap it to prove a different order is caught.
        history: Source of closed bars to fill a warm-up shortfall, the same
            public klines read serve warms up with. ``None`` is offline: only
            stored bars are used. Stored bars win wherever both exist, and the
            day's own bars always come from the ledger.
        now: Current time, UTC-aware. Required with ``history``, to know how
            far back the exchange read has to reach.

    Returns:
        The :class:`ParityReport`.

    Raises:
        ParityDataError: A stream has no stored bar on ``day``, too few
            stored bars before it to warm up, or the recorded trades cannot be
            replayed into the portfolio.
        hermes.domain.errors.LedgerError: A ledger read failed.
    """
    start, end = day_window(day)
    day_bars: list[Bar] = []
    warm_bars: list[Bar] = []
    for pair in pairs:
        for interval in intervals:
            span = interval_duration(interval) * warmup_bars[interval]
            during = source.bars_closing_between(pair, interval, start, end)
            if not during:
                raise ParityDataError(f"no stored {pair} {interval.value} bars close on {day}")
            before = source.bars_closing_between(pair, interval, start - span, start)
            if len(before) < min_warmup_bars[interval] and history is not None:
                before = _fill_gap(
                    before,
                    history=history,
                    pair=pair,
                    interval=interval,
                    start=start,
                    now=now,
                    wanted=warmup_bars[interval],
                )
            if len(before) < min_warmup_bars[interval]:
                raise ParityDataError(
                    f"{pair} {interval.value}: {len(before)} bars before {day}, "
                    f"{min_warmup_bars[interval]} needed to warm the strategy up"
                )
            warm_bars.extend(before)
            day_bars.extend(during)

    served = source.signals_between(strategy_id, start, end)
    trades = source.trades(strategy_id)
    claimed = _claim_trades(served, trades)
    serve_decisions = [
        Decision(
            pair=s.pair,
            kind=s.kind,
            side=s.side,
            signal_time=s.occurred_at,
            outcome=Outcome.NOT_EXECUTED if claimed[i] is None else Outcome.EXECUTED,
        )
        for i, s in enumerate(served)
    ]

    taken = {id(t) for t in claimed if t is not None}
    earlier = [t for t in trades if t.occurred_at < end and id(t) not in taken]
    rebuilt = PortfolioTracker.rebuilt(seed, earlier)
    if isinstance(rebuilt, Failure):
        raise ParityDataError(f"the recorded trades cannot be replayed: {rebuilt.failure()}")
    tracker = rebuilt.unwrap()

    router = _PairRouter(
        _seeded_strategies(
            source,
            strategy_id=strategy_id,
            pairs=pairs,
            warm_bars=warm_bars,
            start=start,
            make_strategy=make_strategy,
        )
    )
    kernel = make_kernel(tracker)
    replayed = _replay_decisions(
        order(day_bars), router=router, kernel=kernel, start=start, end=end, fees_at=fees_at
    )
    return compare(day, serve_decisions, replayed)


def _fill_gap(
    stored: tuple[Bar, ...],
    *,
    history: HistoryFetcher,
    pair: Pair,
    interval: KlineInterval,
    start: datetime,
    now: datetime | None,
    wanted: int,
) -> tuple[Bar, ...]:
    """Add the exchange's bars that closed before ``start`` to the stored ones.

    The read returns the latest closed bars, so it reaches back past the day
    and the bars since. Stored bars are kept as they are; only open times the
    ledger lacks are taken from the exchange.
    """
    if now is None:
        raise ValueError("now is required to fill a warm-up gap from the exchange")
    duration = interval_duration(interval)
    since_start = -((start - now) // duration) + 1
    if since_start >= _MAX_FETCH:
        raise ParityDataError(
            f"{pair} {interval.value}: {start.date()} is too far back to read its warm-up "
            "from the exchange"
        )
    fetched = history(pair, interval, min(wanted + since_start, _MAX_FETCH))
    have = {b.open_time for b in stored}
    floor = start - duration * wanted
    extra = [b for b in fetched if floor < b.close_time <= start and b.open_time not in have]
    return tuple(sorted([*stored, *extra], key=lambda b: b.close_time))


def _seeded_strategies(
    source: ParitySourcePort,
    *,
    strategy_id: StrategyId,
    pairs: Sequence[Pair],
    warm_bars: Sequence[Bar],
    start: datetime,
    make_strategy: Callable[[], StrategyPort],
) -> dict[Pair, StrategyPort]:
    """Return one strategy per pair, seeded as serve seeds it at warm-up."""
    ordered = order_for_delivery(warm_bars)
    strategies: dict[Pair, StrategyPort] = {}
    for pair in pairs:
        latest = source.latest_signal(strategy_id, pair, at_or_before=start)
        position = (
            OpenPosition(entry_time=latest.occurred_at, entry_price=latest.entry_price)
            if latest is not None
            and latest.kind is SignalKind.ENTRY
            and latest.entry_price is not None
            else None
        )
        strategy = make_strategy()
        strategy.seed([b for b in ordered if b.pair == pair], open_position=position)
        strategies[pair] = strategy
    return strategies


def _replay_decisions(
    bars: Sequence[Bar],
    *,
    router: _PairRouter,
    kernel: FillKernel,
    start: datetime,
    end: datetime,
    fees_at: Callable[[datetime, Pair], FeeSchedule],
) -> list[Decision]:
    """Replay ``bars`` one at a time so each signal's outcome is known on its own."""
    decisions: list[Decision] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"01HZ{counter:022d}"

    for bar in bars:
        router.last = None
        run = replay(
            [bar],
            strategy=router,
            kernel=kernel,
            window_start=start,
            window_end=end,
            fees_at=fees_at,
            correlation_id=next_id,
        )
        signal = router.last
        if signal is None:
            continue
        executed = bool(run.trades)
        decisions.append(
            Decision(
                pair=bar.pair,
                kind=signal.kind,
                side=signal.side,
                signal_time=signal.signal_time,
                outcome=Outcome.EXECUTED if executed else Outcome.NOT_EXECUTED,
                reason=None if executed else next(iter(run.declined), None),
            )
        )
    return decisions


def _claim_trades(
    signals: Sequence[SignalEmitted], trades: Sequence[TradeExecuted]
) -> list[TradeExecuted | None]:
    """Pair each signal with the first unclaimed trade of its pair and side after it."""
    taken: set[int] = set()
    claimed: list[TradeExecuted | None] = []
    for signal in signals:
        side = TradeSide.BUY if signal.kind is SignalKind.ENTRY else TradeSide.SELL
        match = next(
            (
                t
                for t in trades
                if id(t) not in taken
                and t.pair == signal.pair
                and t.side is side
                and signal.occurred_at <= t.occurred_at < signal.occurred_at + _EXECUTION_WINDOW
            ),
            None,
        )
        if match is not None:
            taken.add(id(match))
        claimed.append(match)
    return claimed
