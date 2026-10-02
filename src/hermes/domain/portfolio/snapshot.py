"""PortfolioSnapshot — immutable cash and positions with pure transitions.

A snapshot holds one strategy's cash, open long positions, realized PnL and
peak equity, all in a single quote currency. Transitions return a new
snapshot (or a ``Failure`` that leaves the original untouched): they take a
fill or a closed bar and never read a clock, a config or a venue.

Accounting is long-only spot with average cost (no lots; lots are the EUR tax
view, ADR-003). A position keeps its total cost; fees go to cash and to
realized PnL. Equity is cash plus quantity times mark, and the identity
``equity == starting_cash + realized_pnl + unrealized_pnl`` holds exactly.

``to_risk_state`` hands out the thin :class:`~hermes.domain.risk.state.PortfolioState`
the risk rules already consume, so that type is not edited.

Reference: 308-signal-event-portfolio-state change.md D-4, D-5.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from types import MappingProxyType

from returns.result import Failure, Result, Success

from hermes.domain.errors import CurrencyMismatch, DomainError, InvalidPortfolioTransition
from hermes.domain.market_data.bar import Bar
from hermes.domain.portfolio.position import Position
from hermes.domain.portfolio.precision import COST_QUANTUM, EXACT_CONTEXT
from hermes.domain.risk.state import PortfolioState
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["PortfolioSnapshot"]

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """One strategy's cash, positions and PnL at a point in time.

    Build the first snapshot with :meth:`seed`; later ones come from
    :meth:`after_fill` and :meth:`after_bar`.

    Attributes:
        strategy_id: The strategy bucket this snapshot belongs to.
        quote: The single currency of cash, costs and marks.
        starting_cash: Cash at the seed. Always > 0.
        cash: Free cash now. Never negative.
        positions: Open positions keyed by pair. Exposed read-only.
        realized_pnl: Realized PnL since the seed, net of every fee paid.
        peak_equity: Highest equity seen since the seed.

    Raises:
        ValueError: On non-positive ``starting_cash``, negative ``cash``, or a
            position keyed under a pair it does not carry or quoted in a
            currency other than ``quote``.
    """

    strategy_id: StrategyId
    quote: Currency
    starting_cash: Decimal
    cash: Decimal
    positions: Mapping[Pair, Position]
    realized_pnl: Decimal
    peak_equity: Decimal

    def __post_init__(self) -> None:
        """Validate the snapshot invariants and freeze the positions mapping.

        Raises:
            ValueError: See the class docstring.
        """
        if self.starting_cash <= _ZERO:
            raise ValueError(
                f"PortfolioSnapshot.starting_cash must be > 0, got {self.starting_cash}"
            )
        if self.cash < _ZERO:
            raise ValueError(f"PortfolioSnapshot.cash must be >= 0, got {self.cash}")
        for pair, position in self.positions.items():
            if pair != position.pair:
                raise ValueError(f"Position for {position.pair} is keyed under {pair}")
            if pair.quote != self.quote:
                raise ValueError(
                    f"Position for {pair} is not quoted in the snapshot currency {self.quote}"
                )
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))

    @classmethod
    def seed(cls, strategy_id: StrategyId, starting_cash: Money) -> PortfolioSnapshot:
        """Return the first snapshot: all cash, no positions.

        Args:
            strategy_id: The strategy bucket the snapshot belongs to.
            starting_cash: Seed cash. Its currency becomes the snapshot quote.

        Returns:
            A snapshot whose cash, starting cash and peak equity all equal
            ``starting_cash.amount``.

        Raises:
            ValueError: If ``starting_cash.amount`` is not positive.
        """
        if starting_cash.amount <= _ZERO:
            raise ValueError(f"PortfolioSnapshot.seed needs positive cash, got {starting_cash}")
        return cls(
            strategy_id=strategy_id,
            quote=starting_cash.currency,
            starting_cash=starting_cash.amount,
            cash=starting_cash.amount,
            positions={},
            realized_pnl=_ZERO,
            peak_equity=starting_cash.amount,
        )

    @property
    def market_value(self) -> Decimal:
        """Sum of position values at their latest marks, in the quote currency."""
        with localcontext(EXACT_CONTEXT):
            return sum((p.market_value for p in self.positions.values()), _ZERO)

    @property
    def unrealized_pnl(self) -> Decimal:
        """Sum of position PnL at their latest marks, in the quote currency."""
        with localcontext(EXACT_CONTEXT):
            return sum((p.unrealized_pnl for p in self.positions.values()), _ZERO)

    @property
    def equity(self) -> Decimal:
        """Cash plus the market value of every position."""
        with localcontext(EXACT_CONTEXT):
            return self.cash + self.market_value

    def after_fill(self, fill: Fill) -> Result[PortfolioSnapshot, DomainError]:
        """Apply a confirmed fill.

        BUY: cash falls by ``qty * price + fee``; the position gains the
        quantity and the notional as cost; the fee is booked as realized
        loss. SELL: cash rises by ``qty * price - fee``; the position loses
        the quantity and a proportional share of its cost; realized PnL
        gains proceeds minus the cost released minus the fee. The cost
        released on a partial SELL is rounded to the quote precision. The
        mark becomes the fill price at the fill time, and peak equity is
        updated.

        Args:
            fill: A fill in this snapshot's quote currency.

        Returns:
            ``Success(new snapshot)``, or ``Failure`` with the snapshot
            unchanged: ``CurrencyMismatch`` for another quote or fee
            currency; ``InvalidPortfolioTransition`` for a BUY that exceeds
            cash, a SELL without a position or above its quantity, or a SELL
            whose fee would take cash below zero.
        """
        order = fill.order
        for actual in (order.pair.quote, fill.fee.currency):
            if actual != self.quote:
                return Failure(CurrencyMismatch(expected=str(self.quote), actual=str(actual)))
        if order.side == OrderSide.BUY:
            return self._after_buy(fill)
        return self._after_sell(fill)

    def after_bar(self, bar: Bar) -> PortfolioSnapshot:
        """Mark the bar's pair at the bar close when the bar is newer than its mark.

        Args:
            bar: A closed bar.

        Returns:
            A snapshot with the position re-marked and peak equity updated,
            or this same snapshot when the pair is not held or the bar is not
            newer than the current mark (marking is idempotent). Cash never
            changes.
        """
        position = self.positions.get(bar.pair)
        if position is None or bar.close_time <= position.marked_at:
            return self
        marked = replace(position, mark_price=bar.close, marked_at=bar.close_time)
        return self._advance(
            cash=self.cash,
            positions={**self.positions, bar.pair: marked},
            realized_pnl=self.realized_pnl,
        )

    def to_risk_state(self) -> PortfolioState:
        """Return the thin state the risk rules consume.

        Peak and current value map to ``peak_equity`` and ``equity``.
        Cumulative slippage stays ``0``, correlations stay empty and the halt
        flag stays ``False``: nothing supplies them yet.

        Returns:
            A :class:`~hermes.domain.risk.state.PortfolioState`.
        """
        return PortfolioState(
            peak_value=self.peak_equity,
            current_value=self.equity,
            realized_slippage_cumulative=_ZERO,
            correlations={},
            dd_halt_required=False,
        )

    def _after_buy(self, fill: Fill) -> Result[PortfolioSnapshot, DomainError]:
        """Apply a BUY fill, refusing it when it exceeds cash."""
        pair = fill.order.pair
        with localcontext(EXACT_CONTEXT):
            notional = fill.executed_qty * fill.avg_price
            total = notional + fill.fee.amount
            if total > self.cash:
                return Failure(
                    InvalidPortfolioTransition(
                        reason=(
                            f"BUY of {fill.executed_qty} {pair} costs {total}, cash is {self.cash}"
                        )
                    )
                )
            held = self.positions.get(pair)
            position = Position(
                pair=pair,
                quantity=fill.executed_qty + (held.quantity if held else _ZERO),
                cost_basis=notional + (held.cost_basis if held else _ZERO),
                mark_price=fill.avg_price,
                marked_at=fill.timestamp,
            )
            return Success(
                self._advance(
                    cash=self.cash - total,
                    positions={**self.positions, pair: position},
                    realized_pnl=self.realized_pnl - fill.fee.amount,
                )
            )

    def _after_sell(self, fill: Fill) -> Result[PortfolioSnapshot, DomainError]:
        """Apply a SELL fill, refusing it when it exceeds the position or overdraws cash."""
        pair = fill.order.pair
        held = self.positions.get(pair)
        if held is None:
            return Failure(InvalidPortfolioTransition(reason=f"SELL of {pair} without a position"))
        qty = fill.executed_qty
        if qty > held.quantity:
            return Failure(
                InvalidPortfolioTransition(
                    reason=f"SELL of {qty} {pair} exceeds the position of {held.quantity}"
                )
            )
        with localcontext(EXACT_CONTEXT):
            proceeds = qty * fill.avg_price
            cash = self.cash + proceeds - fill.fee.amount
            if cash < _ZERO:
                return Failure(
                    InvalidPortfolioTransition(
                        reason=(
                            f"SELL of {qty} {pair} would take cash to {cash}: "
                            "the fee exceeds proceeds"
                        )
                    )
                )
            positions = dict(self.positions)
            if qty == held.quantity:
                released = held.cost_basis
                del positions[pair]
            else:
                # The rounded share can exceed the cost by one quantum when the
                # cost is off the grid; the clamp keeps the remaining cost >= 0.
                released = min(
                    (held.cost_basis * qty / held.quantity).quantize(COST_QUANTUM),
                    held.cost_basis,
                )
                positions[pair] = Position(
                    pair=pair,
                    quantity=held.quantity - qty,
                    cost_basis=held.cost_basis - released,
                    mark_price=fill.avg_price,
                    marked_at=fill.timestamp,
                )
            return Success(
                self._advance(
                    cash=cash,
                    positions=positions,
                    realized_pnl=self.realized_pnl + proceeds - released - fill.fee.amount,
                )
            )

    def _advance(
        self,
        *,
        cash: Decimal,
        positions: Mapping[Pair, Position],
        realized_pnl: Decimal,
    ) -> PortfolioSnapshot:
        """Return the successor snapshot with peak equity raised when equity set a new high."""
        successor = replace(self, cash=cash, positions=positions, realized_pnl=realized_pnl)
        return replace(successor, peak_equity=max(self.peak_equity, successor.equity))
