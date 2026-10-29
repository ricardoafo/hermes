"""Builders shared by the parity check tests."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from tests.support.fill_kernel import BTC_USDT, STRATEGY

DAY_START = datetime(2026, 10, 3, tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)
CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"
H4 = KlineInterval.H4
D1 = KlineInterval.D1


def bar(
    close_time: datetime,
    interval: KlineInterval = H4,
    *,
    pair: Pair = BTC_USDT,
    volume: str = "1000",
) -> Bar:
    """Return a flat bar at 100 that closes at ``close_time``."""
    span = timedelta(days=1) if interval is D1 else timedelta(hours=4)
    price = Decimal("100")
    return Bar(
        pair=pair,
        interval=interval,
        open_time=close_time - span,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(volume),
        close_time=close_time,
    )


def day_bars(*, pair: Pair = BTC_USDT) -> list[Bar]:
    """Return the six 4h bars and the 1d bar that close on the test day."""
    fours = [bar(DAY_START + timedelta(hours=4 * n), pair=pair) for n in range(1, 7)]
    return [*fours, bar(DAY_END, D1, pair=pair)]


def warmup_bars(*, pair: Pair = BTC_USDT) -> list[Bar]:
    """Return two 4h bars and one 1d bar that closed before the test day."""
    return [
        bar(DAY_START - timedelta(hours=4), pair=pair),
        bar(DAY_START, pair=pair),
        bar(DAY_START, D1, pair=pair),
    ]


def signal_event(at: datetime, kind: SignalKind = SignalKind.ENTRY) -> SignalEmitted:
    """Return the ``SignalEmitted`` serve would store for a BTC signal at ``at``."""
    return SignalEmitted.from_signal(
        StrategySignal(
            kind=kind,
            side=SignalSide.LONG,
            entry_price=Decimal("2618.45") if kind is SignalKind.ENTRY else None,
            stop_price=Decimal("2548.45"),
            atr_context=Decimal("25"),
            signal_time=at,
            exit_reason=None if kind is SignalKind.ENTRY else "chandelier",
        ),
        strategy_id=STRATEGY,
        pair=BTC_USDT,
        correlation_id=CID,
    )


def trade_event(at: datetime, side: TradeSide = TradeSide.BUY) -> TradeExecuted:
    """Return a BTC fill recorded at ``at``."""
    return TradeExecuted(
        occurred_at=at,
        correlation_id=CID,
        pair=BTC_USDT,
        side=side,
        qty=Quantity(amount=Decimal("0.01"), asset=Currency.BTC),
        price=Price(amount=Decimal("100"), pair=BTC_USDT),
        fee=Money(amount=Decimal("0.001"), currency=Currency.USDT),
        strategy_id=STRATEGY,
    )


@dataclass
class FakeSource:
    """In-memory :class:`~hermes.ports.parity_source.ParitySourcePort`."""

    bars: list[Bar] = field(default_factory=list)
    signals: list[SignalEmitted] = field(default_factory=list)
    fills: list[TradeExecuted] = field(default_factory=list)
    missed: list[datetime] = field(default_factory=list)
    """Close times of the bars serve reached late."""

    def bars_closing_between(
        self, pair: Pair, interval: KlineInterval, after: datetime, upto: datetime
    ) -> tuple[Bar, ...]:
        """Return the bars of one stream closing in ``(after, upto]``."""
        return tuple(
            b
            for b in sorted(self.bars, key=lambda b: b.close_time)
            if b.pair == pair and b.interval is interval and after < b.close_time <= upto
        )

    def signals_between(
        self, strategy_id: object, after: datetime, upto: datetime
    ) -> tuple[SignalEmitted, ...]:
        """Return the signals with ``after < occurred_at <= upto``."""
        _ = strategy_id
        return tuple(s for s in self.signals if after < s.occurred_at <= upto)

    def latest_signal(
        self,
        strategy_id: object,
        pair: Pair,
        *,
        at_or_before: datetime,
        kind: SignalKind | None = None,
    ) -> SignalEmitted | None:
        """Return the newest signal of ``pair`` (of ``kind``, when given) up to the bound."""
        _ = strategy_id
        found = [
            s
            for s in self.signals
            if s.pair == pair and s.occurred_at <= at_or_before and (kind is None or s.kind is kind)
        ]
        return max(found, key=lambda s: s.occurred_at, default=None)

    def trades(self, strategy_id: object) -> tuple[TradeExecuted, ...]:
        """Return every recorded fill, oldest first."""
        _ = strategy_id
        return tuple(sorted(self.fills, key=lambda t: t.occurred_at))

    def missed_bars_between(self, after: datetime, upto: datetime) -> int:
        """Return how many missed bars closed in ``(after, upto]``."""
        return sum(after < at <= upto for at in self.missed)


@dataclass
class ScriptedStrategy:
    """Emits an entry on the bars closing at the scripted times; records its seed."""

    at: frozenset[datetime]
    seeded: list[Bar] = field(default_factory=list)

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Emit a long entry when ``bar`` is a scripted 4h close."""
        _ = portfolio_state
        if bar.interval is not H4 or bar.close_time not in self.at:
            return None
        return StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=bar.close,
            stop_price=bar.close * Decimal("0.9"),
            atr_context=Decimal("2"),
            signal_time=bar.close_time,
        )

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Remember what the check seeded."""
        _ = open_position
        self.seeded = list(bars)

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Ignore the restore: the entries are scripted."""
        _ = open_position


@dataclass
class AfterDailyStrategy:
    """Enters on a 4h bar iff the bar delivered just before it was a 1d bar.

    Sensitive to delivery order on purpose: it fires on the 4h bar that closes
    with a 1d bar when the 1d bar goes first (close-time order), and on the
    first 4h bar of the day when the 1d bar is delivered before every 4h bar
    (open-time order).
    """

    previous: KlineInterval | None = None

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Emit an entry on a 4h bar that directly follows a 1d bar."""
        _ = portfolio_state
        after_daily = self.previous is D1
        self.previous = bar.interval
        if bar.interval is not H4 or not after_daily:
            return None
        return StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=bar.close,
            stop_price=bar.close * Decimal("0.9"),
            atr_context=Decimal("2"),
            signal_time=bar.close_time,
        )

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Seeding never changes which interval came last."""
        _ = (bars, open_position)

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Ignore the restore: the entries depend only on delivery order."""
        _ = open_position


@dataclass
class ScriptedExit:
    """Emits a long exit on the 4h bar closing at ``at``; records seeded and restored positions."""

    at: datetime
    seeded_position: OpenPosition | None = None
    restored: list[OpenPosition | None] = field(default_factory=list)

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Emit the exit when ``bar`` is the scripted 4h close."""
        _ = portfolio_state
        if bar.interval is not H4 or bar.close_time != self.at:
            return None
        return StrategySignal(
            kind=SignalKind.EXIT,
            side=SignalSide.LONG,
            entry_price=None,
            stop_price=bar.close,
            atr_context=Decimal("2"),
            signal_time=bar.close_time,
            exit_reason="chandelier",
        )

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Remember the position seeded; the exit itself is scripted."""
        _ = bars
        self.seeded_position = open_position

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Record the position restored after an unfilled exit."""
        self.restored.append(open_position)
