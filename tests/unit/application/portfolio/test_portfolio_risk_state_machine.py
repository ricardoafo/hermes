"""Stateful property test: portfolio, sizing and risk engine under long call sequences (#844).

How does Hermes fail: entries on three pairs at moving prices, thin books,
entries refused by the §5.5 exposure cap, the SA-4 position cap, a stale-book
halt or the §10 kill, exits, bar marks and restarts, in any order. A
rule-based state machine drives the real :class:`FillKernel` (production
:class:`OrderRouter`, :class:`SizingCalculator`, the serve risk overlay from
:func:`shadow_risk_engine`, the signed kill thresholds, the order-book walk
fill model) over a real :class:`PortfolioTracker`. A small reference model
(cash, and per pair quantity, mark and stop) says what must happen.

Partial fills are not reachable: the walk fill model fills an order in full
or rejects it (``INSUFFICIENT_DEPTH``), and an exit always sells the whole
position. The thin-book rules cover that path.

Reference: pre-registration-v1.md §2, §5.4, §5.5, §10; SA-9 §5.5 (RO-04);
308-signal-event-portfolio-state change.md D-4, D-5; issues #844, #863.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Context, Decimal, localcontext

from hypothesis import event, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.application.execution.kernel import (
    Declined,
    Executed,
    FillKernel,
    Halted,
    MarketContext,
)
from hermes.application.execution.shadow import shadow_risk_engine
from hermes.application.order_router.sizing import SizingPolicy
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.strategy_protocol.kill_trigger_evaluator import ExperimentState
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.risk.state import MarketSnapshot
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from tests.support.fill_kernel import STARTING_CASH, STRATEGY, fees, kill_evaluator, seed

_EXPLORE = os.environ.get("HYPOTHESIS_PROFILE") == "explore"
_PAIRS = (
    Pair(base=Currency.BTC, quote=Currency.USDT),
    Pair(base=Currency.ETH, quote=Currency.USDT),
    Pair(base=Currency.SOL, quote=Currency.USDT),
)
_BASE_PRICE = {
    Currency.BTC: Decimal("60000"),
    Currency.ETH: Decimal("3000"),
    Currency.SOL: Decimal("150"),
}
_T0 = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)
_BAR = timedelta(hours=4)
_BPS = Decimal("10000")
_TAKER = Decimal("0.001")
_EXACT = Context(prec=80)
_ZERO = Decimal("0")
# The walk fill model divides cost by quantity at 28 digits, so a one-level
# VWAP can sit 1e-23 off its level; prices and exposure compare at 1e-12.
_PRICE_GRID = Decimal("1e-12")
# Pre-registration §5.4/§5.5 shares of the 5000 USDT capital: risk 1% (50),
# position cap 5% (250), exposure cap 15% (750). Stops of at most 20% below
# the entry put the risk notional at >= 250, so the position cap always binds.
_SIZING = SizingPolicy(
    capital=STARTING_CASH,
    risk_pct=Decimal("0.01"),
    max_position_pct=Decimal("0.05"),
    max_total_exposure_pct=Decimal("0.15"),
)
_POSITION_NOTIONAL = Decimal("250")
_EXPOSURE_CAP = Decimal("750")
_MAX_POSITIONS = 3
_KILL = kill_evaluator()

# Price moves are kept within -40%..+50% so the RO-08 drawdown (15% of equity)
# can never fire: at most 750 of 5000 is ever exposed.
_multipliers = st.integers(min_value=60, max_value=150).map(lambda n: Decimal(n) / 100)
_spreads = st.sampled_from((Decimal("0"), Decimal("5"), Decimal("30")))
_stop_fracs = st.sampled_from((Decimal("0.05"), Decimal("0.1"), Decimal("0.2")))
# One book in five is too thin to fill (the fill model refuses, no partial fill).
_thin = st.integers(min_value=0, max_value=4).map(lambda n: n == 0)


@dataclass(slots=True)
class _Held:
    qty: Decimal
    mark: Decimal
    last_fill: Decimal
    stop: Decimal


def _book(pair: Pair, ref: Decimal, spread_bps: Decimal, at: datetime, *, thin: bool) -> Orderbook:
    """Build a two-level book whose touch sits ``spread_bps`` from ``ref`` (0 = at ``ref``)."""
    depth = Decimal("0.00000001") if thin else Decimal("1000000")
    off = spread_bps / _BPS
    return Orderbook(
        pair=pair,
        bids=(
            OrderbookLevel(price=ref * (1 - off) - Decimal("0.01"), quantity=depth),
            OrderbookLevel(price=ref * (1 - off) - Decimal("0.02"), quantity=depth),
        ),
        asks=(
            OrderbookLevel(price=ref * (1 + off), quantity=depth),
            OrderbookLevel(price=ref * (1 + off) + Decimal("0.01"), quantity=depth),
        ),
        snapshot_time=at,
    )


class PortfolioRiskMachine(RuleBasedStateMachine):
    """Cash, positions, stops and exposure follow the reference model on every step.

    Invariants protected (pre-reg §5.4, §5.5, §10; change 308 D-5):

    - cash >= 0 and equals the model's cash;
    - equity == cash + Σ qty x mark, exactly;
    - the tracker holds exactly the model's positions, never more than three;
    - every open position has a stop, below its average entry price;
    - a refused entry (cap, halt, kill, thin book) leaves no position and no cash change;
    - right after an accepted entry, exposure at the marks stays within the
      750 USDT cap, whatever the spread (#864).
    """

    def __init__(self) -> None:
        """Seed 5000 USDT, no positions, no halt, no kill."""
        super().__init__()
        self.tracker = PortfolioTracker(seed())
        self.kernel = self._kernel(self.tracker)
        self.cash = STARTING_CASH
        self.held: dict[Pair, _Held] = {}
        self.trades: list[TradeExecuted] = []
        self.halted = False
        self.killed = False
        self.clock = _T0
        self.tags = 0

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _kernel(tracker: PortfolioTracker) -> FillKernel:
        return FillKernel(
            fill_model=OrderbookWalkFillModel(),
            risk_engine=shadow_risk_engine(),
            kill_evaluator=_KILL,
            tracker=tracker,
            strategy_id=STRATEGY,
            sizing=_SIZING,
        )

    def _market(self, pair: Pair, book: Orderbook) -> MarketContext:
        return MarketContext(
            orderbook=book,
            fees=fees(pair),
            at=self.clock,
            market_snapshot=MarketSnapshot(
                pair=pair,
                current_spread=Decimal("0.001"),
                median_spread_1h=Decimal("0.001"),
                # RO-04 halts on a book older than 2 seconds.
                orderbook_age_seconds=30.0 if self.halted else 0.0,
                orderbook_timestamp=self.clock,
                realized_slippage_last=_ZERO,
                stable_price=None,
                btc_log_return_1d=0.0,
                candidate_pair=pair,
            ),
            experiment_state=ExperimentState(
                max_drawdown=0.0, days_since_equity_high=0.0, sa9_halt=self.killed
            ),
        )

    def _exposure(self) -> Decimal:
        with localcontext(_EXACT):
            return sum((h.qty * h.mark for h in self.held.values()), _ZERO)

    def _tag(self) -> str:
        self.tags += 1
        return f"c-{self.tags}"

    def _snapshot_state(self) -> tuple[Decimal, dict[Pair, Decimal]]:
        snap = self.tracker.snapshot
        return snap.cash, {pair: p.quantity for pair, p in snap.positions.items()}

    # ------------------------------------------------------------------
    # rules
    # ------------------------------------------------------------------

    @rule(
        pair=st.sampled_from(_PAIRS),
        move=_multipliers,
        spread=_spreads,
        stop_frac=_stop_fracs,
        thin=_thin,
    )
    def enter(
        self, pair: Pair, move: Decimal, spread: Decimal, stop_frac: Decimal, thin: bool
    ) -> None:
        """Send an entry signal; predict fill or the first guard that refuses it.

        As serve does, the pair is marked at the bar close before the strategy
        sees the bar, so the signal's entry price is the pair's mark.
        """
        ref = _BASE_PRICE[pair.base] * move
        self._close_bar(pair, ref)
        stop = ref * (1 - stop_frac)
        signal = StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=ref,
            stop_price=stop,
            atr_context=Decimal("1"),
            signal_time=self.clock,
        )
        before = self._snapshot_state()
        open_before = self._exposure()
        outcome = self.kernel.execute(
            signal,
            pair,
            self._market(pair, _book(pair, ref, spread, self.clock, thin=thin)),
            correlation_id=self._tag(),
        )
        event(f"entry outcome: {type(outcome).__name__} {getattr(outcome, 'rule', '')}")
        if self.killed:
            assert isinstance(outcome, Halted), outcome
        elif len(self.held) >= _MAX_POSITIONS:
            assert isinstance(outcome, Declined) and outcome.rule == "orphan-position", outcome
        elif pair in self.held:
            # §5.4: one position per pair, whatever the strategy sends (#902).
            assert isinstance(outcome, Declined) and outcome.rule == "already-held", outcome
        elif self.halted:
            assert isinstance(outcome, Declined) and outcome.rule == "HALT", outcome
            assert outcome.reason.startswith("RO-04")
        elif open_before + _POSITION_NOTIONAL > _EXPOSURE_CAP:
            assert isinstance(outcome, Declined) and outcome.rule == "sizing", outcome
        elif thin:
            assert isinstance(outcome, Declined) and outcome.rule == "fill_model", outcome
        elif open_before + self._entry_cost_at_the_touch(ref, spread) > _EXPOSURE_CAP:
            # §5.5 at the price paid: fits at the signal price, not at the ask (#864).
            assert isinstance(outcome, Declined) and outcome.rule == "sizing", outcome
        else:
            assert isinstance(outcome, Executed), outcome
            fill = outcome.fill
            with localcontext() as ctx:
                ctx.rounding = ROUND_DOWN
                expected_qty = _POSITION_NOTIONAL / ref
            assert fill.executed_qty == expected_qty
            assert fill.avg_price.quantize(_PRICE_GRID) == (ref * (1 + spread / _BPS)).quantize(
                _PRICE_GRID
            )
            with localcontext(_EXACT):
                self.cash -= fill.executed_qty * fill.avg_price + fill.fee.amount
                # §5.5 as the sizing applies it: open exposure at the marks plus
                # the new notional at the signal price never passes the cap.
                assert open_before + fill.executed_qty * ref <= _EXPOSURE_CAP
            self.held[pair] = _Held(
                qty=fill.executed_qty, mark=fill.avg_price, last_fill=fill.avg_price, stop=stop
            )
            self.trades.append(outcome.trade)
            assert self._exposure() <= _EXPOSURE_CAP
            return
        assert self._snapshot_state() == before

    @staticmethod
    def _entry_cost_at_the_touch(ref: Decimal, spread: Decimal) -> Decimal:
        """Return what the sized entry costs on a deep book with the ask ``spread`` bps up."""
        with localcontext() as ctx:
            ctx.rounding = ROUND_DOWN
            qty = _POSITION_NOTIONAL / ref
        with localcontext(_EXACT):
            return qty * (ref * (1 + spread / _BPS))

    @rule(pair=st.sampled_from(_PAIRS), move=_multipliers, spread=_spreads, thin=_thin)
    def exit(self, pair: Pair, move: Decimal, spread: Decimal, thin: bool) -> None:
        """Send an exit signal: sells the whole position, kill or halt notwithstanding."""
        self.clock += _BAR
        ref = _BASE_PRICE[pair.base] * move
        signal = StrategySignal(
            kind=SignalKind.EXIT,
            side=SignalSide.LONG,
            entry_price=None,
            stop_price=ref * Decimal("0.9"),
            atr_context=Decimal("1"),
            signal_time=self.clock,
            exit_reason="chandelier",
        )
        before = self._snapshot_state()
        outcome = self.kernel.execute(
            signal,
            pair,
            self._market(pair, _book(pair, ref, spread, self.clock, thin=thin)),
            correlation_id=self._tag(),
        )
        event(f"exit outcome: {type(outcome).__name__} {getattr(outcome, 'rule', '')}")
        held = self.held.get(pair)
        if held is None:
            assert isinstance(outcome, Declined) and outcome.rule == "no-position", outcome
        elif thin:
            assert isinstance(outcome, Declined) and outcome.rule == "fill_model", outcome
        else:
            assert isinstance(outcome, Executed), outcome
            fill = outcome.fill
            assert fill.executed_qty == held.qty
            assert fill.avg_price.quantize(_PRICE_GRID) == (
                ref * (1 - spread / _BPS) - Decimal("0.01")
            ).quantize(_PRICE_GRID)
            with localcontext(_EXACT):
                self.cash += fill.executed_qty * fill.avg_price - fill.fee.amount
            del self.held[pair]
            self.trades.append(outcome.trade)
            return
        assert self._snapshot_state() == before

    @precondition(lambda self: bool(self.held))
    @rule(data=st.data(), move=_multipliers, spread=_spreads)
    def exit_held(self, data: st.DataObject, move: Decimal, spread: Decimal) -> None:
        """Exit a pair that is held, so round trips are common, not rare."""
        pair = data.draw(st.sampled_from(sorted(self.held, key=str)))
        self.exit(pair, move, spread, thin=False)

    @precondition(lambda self: len(self.held) == _MAX_POSITIONS)
    @rule(pair=st.sampled_from(_PAIRS), move=_multipliers, stop_frac=_stop_fracs)
    def enter_when_full(self, pair: Pair, move: Decimal, stop_frac: Decimal) -> None:
        """Enter with three positions open: the SA-4 guard (or the kill) refuses it."""
        self.enter(pair, move, Decimal("0"), stop_frac, thin=False)

    @rule(pair=st.sampled_from(_PAIRS), move=_multipliers)
    def mark(self, pair: Pair, move: Decimal) -> None:
        """Close a 4h bar: a held pair is re-marked at the close, cash never moves."""
        self._close_bar(pair, _BASE_PRICE[pair.base] * move)

    def _close_bar(self, pair: Pair, close: Decimal) -> None:
        self.clock += _BAR
        self.tracker.mark(
            Bar(
                pair=pair,
                interval=KlineInterval.H4,
                open_time=self.clock - _BAR,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=Decimal("1"),
                close_time=self.clock,
            )
        )
        if pair in self.held:
            self.held[pair].mark = close

    @precondition(lambda self: not self.halted)
    @rule()
    def halt(self) -> None:
        """Let the book go stale (RO-04): new entries are refused."""
        self.halted = True

    @precondition(lambda self: self.halted)
    @rule()
    def resume(self) -> None:
        """Serve a fresh book again: entries are allowed."""
        self.halted = False

    # Kill is permanent (§10); waiting for two fills keeps most runs alive.
    @precondition(lambda self: not self.killed and len(self.trades) >= 2)
    @rule()
    def kill(self) -> None:
        """Fire an SA-9 P0 HALT into the §10 kill evaluator: no new entries, ever."""
        self.killed = True

    @rule()
    def restart(self) -> None:
        """Rebuild the tracker from the recorded trades, as serve does on boot.

        Excluded behaviour (issue #863): after the rebuild every position is
        valued at its last fill price until its next bar, so the model resets
        its marks to the last fill instead of asserting the marks survive.
        """
        peak = self.tracker.snapshot.peak_equity
        rebuilt = PortfolioTracker.rebuilt(seed(), self.trades).unwrap()
        rebuilt.restore_peak(peak)
        self.tracker = rebuilt
        self.kernel = self._kernel(rebuilt)
        for held in self.held.values():
            held.mark = held.last_fill

    # ------------------------------------------------------------------
    # invariants
    # ------------------------------------------------------------------

    @invariant()
    def cash_is_never_negative_and_matches_the_model(self) -> None:
        """Keep cash >= 0 and equal to seed minus buys (with fees) plus sells (net of fees)."""
        cash = self.tracker.snapshot.cash
        assert cash >= _ZERO
        assert cash == self.cash

    @invariant()
    def equity_is_cash_plus_marked_positions(self) -> None:
        """Keep equity == cash + Σ qty x mark, with the model's quantities and marks."""
        with localcontext(_EXACT):
            assert self.tracker.snapshot.equity == self.cash + self._exposure()

    @invariant()
    def positions_match_the_model(self) -> None:
        """Hold exactly the model's pairs, quantities and marks, at most three."""
        positions = self.tracker.snapshot.positions
        assert {pair: p.quantity for pair, p in positions.items()} == {
            pair: h.qty for pair, h in self.held.items()
        }
        assert {pair: p.mark_price for pair, p in positions.items()} == {
            pair: h.mark for pair, h in self.held.items()
        }
        assert self.tracker.open_position_count() <= _MAX_POSITIONS

    @invariant()
    def every_open_position_has_a_stop_below_its_entry(self) -> None:
        """Each held pair came from an accepted entry whose stop sits below the average cost."""
        for pair, position in self.tracker.snapshot.positions.items():
            held = self.held[pair]
            with localcontext(_EXACT):
                average_entry = position.cost_basis / position.quantity
            assert _ZERO < held.stop < average_entry


PortfolioRiskMachine.TestCase.settings = settings(
    stateful_step_count=100 if _EXPLORE else 50,
)
TestPortfolioRiskMachine = PortfolioRiskMachine.TestCase


def _signal(kind: SignalKind, ref: Decimal, at: datetime) -> StrategySignal:
    return StrategySignal(
        kind=kind,
        side=SignalSide.LONG,
        entry_price=ref if kind is SignalKind.ENTRY else None,
        stop_price=ref * Decimal("0.9"),
        atr_context=Decimal("1"),
        signal_time=at,
        exit_reason=None if kind is SignalKind.ENTRY else "chandelier",
    )


def test_an_exit_sells_a_position_built_by_two_entries() -> None:
    """An exit sells the whole held quantity (FillKernel docstring; change D-2; issue #866).

    Shrunk from the machine: BTC entered at 36000 (qty 250/36000 to 28
    places), entered again at 39000 (qty 250/39000), then exited on a deep
    book. The held 0.013354700854700854700854700854 BTC needs 29 places;
    summing the walked parts in the default 28-place context would match
    0.01335470085470085470085470085 and decline, so the position could never
    be closed.
    """
    machine = PortfolioRiskMachine()
    btc = _PAIRS[0]
    # The kernel now refuses a second entry on a held pair (#902), so the two
    # buys reach the portfolio directly, as the issue #866 shrink left them.
    for move in (Decimal("0.6"), Decimal("0.65")):
        machine.clock += _BAR
        ref = _BASE_PRICE[btc.base] * move
        qty = Decimal(250) / ref
        machine.tracker.apply_fill(
            Fill(
                order=Order(
                    side=OrderSide.BUY,
                    pair=btc,
                    qty=Quantity(amount=qty, asset=btc.base),
                    strategy_id=STRATEGY,
                ),
                executed_qty=qty,
                avg_price=ref,
                fee=Money(amount=Decimal(0), currency=btc.quote),
                timestamp=machine.clock,
            )
        ).unwrap()
    held = machine.tracker.snapshot.positions[btc].quantity
    assert held == Decimal("0.013354700854700854700854700854")
    machine.clock += _BAR
    ref = _BASE_PRICE[btc.base] * Decimal("0.6")
    outcome = machine.kernel.execute(
        _signal(SignalKind.EXIT, ref, machine.clock),
        btc,
        machine._market(btc, _book(btc, ref, Decimal("0"), machine.clock, thin=False)),
        correlation_id="x",
    )
    assert isinstance(outcome, Executed), outcome
    assert outcome.fill.executed_qty == held
