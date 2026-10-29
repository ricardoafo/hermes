"""Seeded synthetic paths and the backtest runs the metamorphic relations compare.

One seed gives one pair's 4h bars (daily bars are built from them by
:class:`~tests.support.fake_market.script.MarketScript`): a long prelude that
drifts up, so the 1d EMA(200) regime is on when the window opens, then a
window of chop, rallies that break the Donchian channel with ADX above 25, and
drops that hit the chandelier stop. Every price is invented and the path ends
in 2022, far before the pre-registered test window (ADR-012).

The runs go through the production
:func:`~hermes.application.backtest.replay.replay` with the real
``SwingStrategyAdapter``, the real fill kernel and the pre-registered execution
model, as ``scripts/backtest/run_calibration_full.py`` wires them. More than
one pair goes through one strategy per pair and one shared portfolio, the way
serve and the parity check route bars by ``bar.pair``.
"""

import itertools
import random
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import cache

from returns.result import Success

from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.backtest.replay import BacktestRun, replay
from hermes.application.execution.kernel import FillKernel
from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.order_router.sizing import SizingPolicy
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.strategy_protocol.kill_trigger_evaluator import KillTriggerEvaluator
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import order_for_delivery
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.ports.strategy import StrategyPort
from tests.support.fake_market.script import BARS_PER_DAY, MarketScript
from tests.support.fill_kernel import STARTING_CASH, STRATEGY, fees_at, kill_evaluator

BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
SOL = Pair(base=Currency.SOL, quote=Currency.USDT)

START = datetime(2021, 1, 1, tzinfo=UTC)
"""First bar's open. A path plus the largest shift still ends before 2023."""

PRELUDE_DAYS = 410
"""Days before the window: more than the 300 daily bars the strategy buffers."""

WARM_UP_DAYS = 300
"""Days of warm-up a plain run gets: the ``warmup_bars_1d`` floor of 300 closes (#880)."""

LIVE_DAYS = 90
"""Days in the window."""

WINDOW_START = START + timedelta(days=PRELUDE_DAYS)
WINDOW_END = WINDOW_START + timedelta(days=LIVE_DAYS)

_TICK = Decimal("0.00000001")


def _dec(value: float) -> Decimal:
    return Decimal(f"{value:.8f}").quantize(_TICK)


def _segments(rng: random.Random, bars: int) -> Iterable[tuple[float, float]]:
    """Yield ``(drift, noise)`` per 4h bar of the window: chop, a rally, a drop, repeated."""
    made = 0
    while made < bars:
        for days, drift, noise in (
            (rng.uniform(3, 9), 0.0, 0.006),
            (rng.uniform(4, 8), 0.004, 0.002),
            (rng.uniform(1, 3), -0.012, 0.004),
        ):
            for _ in range(round(days * BARS_PER_DAY)):
                if made == bars:
                    return
                made += 1
                yield drift, noise


def _script(seed: int, pair: Pair) -> list[tuple[Decimal, Decimal, Decimal]]:
    """Return ``(close, high, low)`` for every 4h bar of ``pair``'s path from ``seed``."""
    rng = random.Random(f"{seed}:{pair}")  # noqa: S311 — reproducible test input, not a secret
    moves = [(0.0004, 0.004)] * (PRELUDE_DAYS * BARS_PER_DAY)
    moves += list(_segments(rng, LIVE_DAYS * BARS_PER_DAY))
    close = 100.0
    steps: list[tuple[Decimal, Decimal, Decimal]] = []
    for drift, noise in moves:
        previous = close
        close = max(previous * (1 + drift + rng.gauss(0, noise)), 1.0)
        high = max(previous, close) * (1 + rng.uniform(0, 0.003))
        low = min(previous, close) * (1 - rng.uniform(0, 0.003))
        steps.append((_dec(close), _dec(high), _dec(low)))
    return steps


@cache
def _built(seed: int, pairs: tuple[Pair, ...], flat: tuple[Pair, ...]) -> tuple[Bar, ...]:
    script = MarketScript(start=START, prices={p: Decimal(100) for p in (*pairs, *flat)})
    for pair in pairs:
        for close, high, low in _script(seed, pair):
            script.bar(pair, close=close, high=high, low=low)
    for pair in flat:
        script.hold(pair, days=PRELUDE_DAYS + LIVE_DAYS)
    market = script.build()
    every = [
        bar
        for pair in (*pairs, *flat)
        for interval in (KlineInterval.H4, KlineInterval.D1)
        for bar in market.bars(pair, interval)
    ]
    return order_for_delivery(every)


def path(
    seed: int,
    *,
    pairs: Sequence[Pair] = (BTC,),
    flat: Sequence[Pair] = (),
    warm_up_days: int = WARM_UP_DAYS,
) -> list[Bar]:
    """Return the bars of one seeded path in delivery order.

    Args:
        seed: The seed; the same seed always gives the same bars.
        pairs: Pairs that walk, rally and drop. Each pair's path depends on
            the seed and the pair only, never on the other pairs.
        flat: Pairs held at 100 the whole time: they never break out, so
            they never signal.
        warm_up_days: Days of bars kept before the window opens.

    Returns:
        Closed bars ordered by close time, 1d first on ties, then pair.

    """
    first = WINDOW_START - timedelta(days=warm_up_days)
    built = _built(seed, tuple(pairs), tuple(flat))
    return [bar for bar in built if bar.open_time >= first]


def regime_edge(calm_days: int = 100) -> list[Bar]:
    """Return a path whose first entry depends on how many daily bars warmed the EMA up.

    ``calm_days`` days at 100, 200 days at 200, then a dip to 190 and a
    three-day climb to 199. When the climb starts, the EMA(200) over the last
    200 daily closes is 200.00, so the regime is off; over the last 300 (the
    strategy's buffer and the ``warmup_bars_1d`` floor, #880) it is 181.61, so
    the regime is on and the climb's breakout enters. With the default 100
    calm days the window opens at day 300 (:data:`REGIME_EDGE_WINDOW`).
    """
    calm = Decimal("0.002")
    script = MarketScript(start=START, prices={BTC: Decimal(100)})
    script.hold(BTC, days=calm_days, spread=calm)
    script.ramp(BTC, days=1 / 6, to=Decimal(200), spread=calm)
    script.hold(BTC, days=200 - 1 / 6, spread=calm)
    script.ramp(BTC, days=1, to=Decimal(190), spread=calm)
    script.hold(BTC, days=2, spread=calm)
    script.ramp(BTC, days=3, to=Decimal(199), spread=calm)
    script.hold(BTC, days=10, spread=calm)
    market = script.build()
    return list(
        order_for_delivery(
            [*market.bars(BTC, KlineInterval.H4), *market.bars(BTC, KlineInterval.D1)]
        )
    )


REGIME_EDGE_WINDOW = START + timedelta(days=300)
"""Window start of :func:`regime_edge` with its default 100 calm days: 300 daily bars before."""


def shifted(bars: Iterable[Bar], by: timedelta) -> list[Bar]:
    """Return ``bars`` with every open and close time moved by ``by``."""
    return [
        Bar(
            pair=b.pair,
            interval=b.interval,
            open_time=b.open_time + by,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            close_time=b.close_time + by,
        )
        for b in bars
    ]


def scaled(bars: Iterable[Bar], k: Decimal) -> list[Bar]:
    """Return ``bars`` with every price multiplied by ``k``; volumes stay in base units."""
    return [
        Bar(
            pair=b.pair,
            interval=b.interval,
            open_time=b.open_time,
            open=b.open * k,
            high=b.high * k,
            low=b.low * k,
            close=b.close * k,
            volume=b.volume,
            close_time=b.close_time,
        )
        for b in bars
    ]


@cache
def _kills() -> KillTriggerEvaluator:
    return kill_evaluator()


def seed_snapshot(capital: Decimal = STARTING_CASH) -> PortfolioSnapshot:
    """Return the USDT seed the swing strategy starts from."""
    return PortfolioSnapshot.seed(STRATEGY, Money(capital, Currency.USDT))


def make_kernel(tracker: PortfolioTracker, capital: Decimal = STARTING_CASH) -> FillKernel:
    """Return the backtest's kernel: the walk fill model and the §5.4/§5.5 limits on ``capital``."""
    return FillKernel(
        fill_model=OrderbookWalkFillModel(),
        risk_engine=RiskEngine(rules=[]),
        kill_evaluator=_kills(),
        tracker=tracker,
        strategy_id=STRATEGY,
        sizing=SizingPolicy(
            capital=capital,
            risk_pct=Decimal("0.01"),
            max_position_pct=Decimal("0.05"),
            max_total_exposure_pct=Decimal("0.15"),
        ),
    )


def correlation_ids() -> Callable[[], str]:
    """Return a source of ULID-shaped ids that counts from one, so two runs share ids."""
    counter = itertools.count(1)
    return lambda: f"01HZ{next(counter):022d}"


@dataclass
class PairRouter:
    """One strategy per pair behind one :class:`StrategyPort`, as serve routes bars.

    ``replay`` restores a position right after the bar whose signal did not
    fill, so the restore goes to the strategy that saw the last bar (the
    parity check routes the same way).
    """

    strategies: Mapping[Pair, StrategyPort]
    last_pair: Pair | None = field(default=None)

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Hand ``bar`` to its pair's strategy."""
        self.last_pair = bar.pair
        return self.strategies[bar.pair].on_bar(bar, portfolio_state)

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Do nothing: each pair's strategy is seeded on its own."""
        _ = (bars, open_position)

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Restore the position of the strategy that saw the last bar."""
        if self.last_pair is not None:
            self.strategies[self.last_pair].restore_position(open_position)


def backtest(
    bars: Sequence[Bar],
    *,
    pairs: Sequence[Pair] | None = None,
    window_start: datetime = WINDOW_START,
    window_end: datetime = WINDOW_END,
    capital: Decimal = STARTING_CASH,
) -> BacktestRun:
    """Replay ``bars`` from a flat portfolio of ``capital`` USDT.

    Args:
        bars: Closed bars in delivery order.
        pairs: The configured pairs, in config order; one strategy each behind
            a :class:`PairRouter`. ``None`` runs one plain strategy, as the
            calibration script does for its one pair.
        window_start: First signal time that is executed.
        window_end: Last 1d close recorded in the equity curve.
        capital: Starting cash and sizing capital.

    Returns:
        The replay's :class:`BacktestRun`.

    """
    strategy: StrategyPort = (
        SwingStrategyAdapter()
        if pairs is None
        else PairRouter({pair: SwingStrategyAdapter() for pair in pairs})
    )
    return replay(
        bars,
        strategy=strategy,
        kernel=make_kernel(PortfolioTracker(seed_snapshot(capital)), capital),
        window_start=window_start,
        window_end=window_end,
        fees_at=fees_at,
        correlation_id=correlation_ids(),
    )


def backtest_with_restart(bars: Sequence[Bar], *, restart_at: datetime) -> BacktestRun:
    """Replay ``bars`` in two legs with a serve-style restart at ``restart_at``.

    The first leg replays the bars closing by ``restart_at``. The second leg
    starts from nothing in memory, as a booted serve does: the portfolio is
    rebuilt from the first leg's trades, a fresh strategy is seeded from the
    last 300 bars of each interval (``warmup_bars_4h`` / ``warmup_bars_1d``)
    with the held position and its last stop, and a fresh kernel trades.

    Returns:
        One :class:`BacktestRun` holding both legs, in order.

    """
    ids = correlation_ids()
    entries: dict[Pair, OpenPosition] = {}
    before = [b for b in bars if b.close_time <= restart_at]
    after = [b for b in bars if b.close_time > restart_at]
    first_strategy = SwingStrategyAdapter()
    first = replay(
        before,
        strategy=first_strategy,
        kernel=make_kernel(PortfolioTracker(seed_snapshot())),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        fees_at=fees_at,
        correlation_id=ids,
        entries=entries,
    )
    rebuilt = PortfolioTracker.rebuilt(seed_snapshot(), first.trades)
    assert isinstance(rebuilt, Success), rebuilt
    held = next(iter(entries.values()), None)
    stop = first_strategy.last_stop_record
    if held is not None and stop is not None:
        held = OpenPosition(
            entry_time=held.entry_time, entry_price=held.entry_price, last_stop=stop.stop_price
        )
    warm = [
        bar
        for interval in (KlineInterval.H4, KlineInterval.D1)
        for bar in [b for b in before if b.interval is interval][-300:]
    ]
    second_strategy = SwingStrategyAdapter()
    second_strategy.seed(order_for_delivery(warm), open_position=held)
    second = replay(
        after,
        strategy=second_strategy,
        kernel=make_kernel(rebuilt.unwrap()),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        fees_at=fees_at,
        correlation_id=ids,
        entries=entries,
    )
    return BacktestRun(
        signals_seen=first.signals_seen + second.signals_seen,
        fills=[*first.fills, *second.fills],
        reference_prices=[*first.reference_prices, *second.reference_prices],
        rejections=[*first.rejections, *second.rejections],
        declined=first.declined + second.declined,
        trades=[*first.trades, *second.trades],
        daily_equity=[*first.daily_equity, *second.daily_equity],
    )


_WALL_CLOCK_COLUMNS = frozenset({"recorded_at", "applied_at"})
"""Columns the ledger stamps with the wall clock when a row is written, not with run time."""


def ledger_bytes(trades: Iterable[TradeExecuted]) -> bytes:
    """Return every table of an in-memory ledger holding ``trades``, as bytes.

    The schema and every row are dumped, except the columns that stamp the
    wall clock at write time (``events.recorded_at``,
    ``schema_migrations.applied_at``): two runs a millisecond apart differ
    there by design, and nothing the run decided is in them.
    """
    connection = sqlite3.connect(":memory:")
    try:
        ledger = SqliteLedger(connection)
        for trade in trades:
            ledger.append_event(trade)
        lines: list[str] = []
        tables = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        for name, sql in tables:
            lines.append(sql)
            columns = [
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{name}")')
                if row[1] not in _WALL_CLOCK_COLUMNS
            ]
            listed = ", ".join(f'"{c}"' for c in columns)
            order = ", ".join(str(i + 1) for i in range(len(columns)))
            lines.extend(
                repr(row)
                for row in connection.execute(
                    f'SELECT {listed} FROM "{name}" ORDER BY {order}'  # noqa: S608 — names from sqlite_master
                )
            )
        return "\n".join(lines).encode()
    finally:
        connection.close()


type TradeRow = tuple[str, str, Decimal, Decimal, Decimal, datetime]


def trade_rows(run: BacktestRun, pair: Pair | None = None) -> list[TradeRow]:
    """Return ``(base, side, qty, price, fee, time)`` per trade, optionally of one pair."""
    return [
        (
            t.pair.base.value,
            t.side.value,
            t.qty.amount,
            t.price.amount,
            t.fee.amount,
            t.occurred_at,
        )
        for t in run.trades
        if pair is None or t.pair == pair
    ]
