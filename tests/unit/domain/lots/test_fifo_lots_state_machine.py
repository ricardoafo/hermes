"""Stateful property test: FIFO tax lots across several assets (issue #844).

How does Hermes fail: a long run of buys and partial or full sells over three
assets, sells that ask for more than is open, and sells timed between two buys.
A rule-based state machine drives the real lot code (:meth:`Lot.open`,
:func:`select_lots_fifo`, :meth:`Lot.consume`) and, beside it, the ledger's
replay (:func:`replay_fifo_eur`) over the same trade log. A small reference
model (a list of ``[lot, remaining]`` per asset in buy order) says what FIFO
must do. Invariants are checked after every step.

Reference: ADR-003 §03 (FIFO worked example), ADR-002 (Spanish FIFO), issue #844.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from returns.result import Failure, Success

from hermes.domain.lots.eur_fifo import EurTrade, replay_fifo_eur
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.selection import InsufficientInventory, select_lots_fifo
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_EXPLORE = os.environ.get("HYPOTHESIS_PROFILE") == "explore"
_ASSETS = (Currency.BTC, Currency.ETH, Currency.SOL)
_T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
_STRATEGY = StrategyId("swing")
_QTY_QUANTUM = Decimal("0.00000001")
_ZERO = Decimal("0")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SELL_PRICE = Money(amount=Decimal("100"), currency=Currency.EUR)
_COST = Money(amount=Decimal("90"), currency=Currency.EUR)


def _lot_id(seq: int) -> LotId:
    """Return a 26-character id whose lexical order is the order ``seq`` was minted in."""
    return LotId(value="".join(_CROCKFORD[(seq >> (5 * i)) & 0x1F] for i in reversed(range(26))))


@dataclass(slots=True)
class _ModelLot:
    lot_id: LotId
    opened_at: datetime
    remaining: Decimal


_quantities = st.integers(min_value=1, max_value=5_000_000).map(lambda n: Decimal(n) * _QTY_QUANTUM)
_minutes = st.integers(min_value=0, max_value=240)


class FifoLotsMachine(RuleBasedStateMachine):
    """FIFO lots stay consistent with a buy-order reference model across assets.

    Invariants protected (ADR-003 §03):

    - quantity is conserved per asset: bought minus sold equals what is open,
      in the real lots and in the ledger replay;
    - a sell draws the oldest lot first and drains it before the next one;
    - no lot opened after the sale is drawn;
    - a refused sell (more than is open) changes nothing;
    - every lot's remaining quantity stays within ``[0, original]``.
    """

    def __init__(self) -> None:
        """Start with no lots, an empty trade log and the clock at ``_T0``."""
        super().__init__()
        self.clock = _T0
        self.seq = 0
        self.real: dict[str, Lot] = {}
        self.model: dict[Currency, list[_ModelLot]] = {asset: [] for asset in _ASSETS}
        self.bought: dict[Currency, Decimal] = dict.fromkeys(_ASSETS, _ZERO)
        self.sold: dict[Currency, Decimal] = dict.fromkeys(_ASSETS, _ZERO)
        self.trades: dict[Currency, list[EurTrade]] = {asset: [] for asset in _ASSETS}
        self.ref_of_lot: dict[str, str] = {}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _open_lots(self) -> list[Lot]:
        # select_lots_fifo takes open lots only (its contract, and what
        # SqliteLedger.list_open_lots returns); a drained lot passed in makes it
        # try a zero-quantity consumption and refuse the whole sale.
        return [lot for lot in self.real.values() if not lot.is_closed()]

    def _open(self, asset: Currency) -> Decimal:
        return sum((m.remaining for m in self.model[asset]), _ZERO)

    def _log_trade(self, asset: Currency, side: OrderSide, qty: Decimal, at: datetime) -> str:
        ref = str(len(self.trades[asset]) + 1)
        self.trades[asset].append(
            EurTrade(
                ref=ref,
                side=side,
                qty=qty,
                executed_at=at,
                price_eur=Decimal("100"),
                fee_eur=_ZERO,
            )
        )
        return ref

    def _expected_draw(
        self, asset: Currency, qty: Decimal, at: datetime
    ) -> list[tuple[LotId, Decimal]]:
        left = qty
        draws: list[tuple[LotId, Decimal]] = []
        for m in self.model[asset]:
            if left == _ZERO:
                break
            if m.remaining == _ZERO or m.opened_at > at:
                continue
            take = min(m.remaining, left)
            draws.append((m.lot_id, take))
            left -= take
        return draws

    def _sell(self, asset: Currency, qty: Decimal, at: datetime) -> None:
        expected = self._expected_draw(asset, qty, at)
        result = select_lots_fifo(
            self._open_lots(), Quantity(amount=qty, asset=asset), _SELL_PRICE, at
        )
        assert isinstance(result, Success), f"sell of {qty} {asset} refused: {result}"
        consumptions = result.unwrap()
        assert [(c.lot_id, c.consumed_qty.amount) for c in consumptions] == expected
        for c in consumptions:
            assert c.consumed_qty.asset == asset
            assert c.consumed_at == at
            # gain = (100 - 90) x qty, no fee (ADR-016 item 3)
            assert c.realized_gain.amount == Decimal("10") * c.consumed_qty.amount
            self.real[c.lot_id.value] = self.real[c.lot_id.value].consume(c.consumed_qty).unwrap()
        by_id = {m.lot_id: m for m in self.model[asset]}
        for lot_id, take in expected:
            by_id[lot_id].remaining -= take
        self.sold[asset] += qty
        self._log_trade(asset, OrderSide.SELL, qty, at)

    # ------------------------------------------------------------------
    # rules
    # ------------------------------------------------------------------

    @rule(asset=st.sampled_from(_ASSETS), qty=_quantities, minutes=_minutes)
    def buy(self, asset: Currency, qty: Decimal, minutes: int) -> None:
        """Buy: open one lot at or after the previous event (ties allowed)."""
        self.clock += timedelta(minutes=minutes)
        self.seq += 1
        lot_id = _lot_id(self.seq)
        lot = Lot.open(
            lot_id=lot_id,
            asset=asset,
            qty=Quantity(amount=qty, asset=asset),
            cost_basis_per_unit=_COST,
            strategy_id=_STRATEGY,
            opened_at=self.clock,
        ).unwrap()
        self.real[lot_id.value] = lot
        self.model[asset].append(_ModelLot(lot_id=lot_id, opened_at=self.clock, remaining=qty))
        self.bought[asset] += qty
        self.ref_of_lot[lot_id.value] = self._log_trade(asset, OrderSide.BUY, qty, self.clock)

    @rule(
        asset=st.sampled_from(_ASSETS),
        percent=st.integers(min_value=1, max_value=100),
        minutes=_minutes,
    )
    def sell_part(self, asset: Currency, percent: int, minutes: int) -> None:
        """Sell 1-100% of what is open, FIFO; 100% closes every lot of the asset."""
        qty = (self._open(asset) * percent / 100).quantize(_QTY_QUANTUM, rounding=ROUND_DOWN)
        if qty == _ZERO:
            return
        self.clock += timedelta(minutes=minutes)
        self._sell(asset, qty, self.clock)

    @rule(asset=st.sampled_from(_ASSETS), extra=_quantities)
    def sell_too_much(self, asset: Currency, extra: Decimal) -> None:
        """Sell above the open quantity: refused, and no lot changes."""
        before = dict(self.real)
        wanted = self._open(asset) + extra
        result = select_lots_fifo(
            self._open_lots(), Quantity(amount=wanted, asset=asset), _SELL_PRICE, self.clock
        )
        assert isinstance(result, Failure)
        failure = result.failure()
        assert isinstance(failure, InsufficientInventory)
        assert failure.available == Quantity(amount=self._open(asset), asset=asset)
        assert self.real == before

    @precondition(lambda self: self.seq >= 2)
    @rule(asset=st.sampled_from(_ASSETS), percent=st.integers(min_value=1, max_value=100))
    def sell_between_buys(self, asset: Currency, percent: int) -> None:
        """Sell at a time before the newest lot: only lots opened by then are drawn.

        Excluded transition (suspected defect, pinned below): a sale bigger than
        the inventory opened by its time. :func:`select_lots_fifo` then draws the
        rest from a lot opened after the sale. The rule only sells what the
        older lots hold.
        """
        lots = self.model[asset]
        if len(lots) < 2 or lots[-1].opened_at == lots[0].opened_at:
            return
        at = lots[-1].opened_at - timedelta(seconds=1)
        older = sum((m.remaining for m in lots if m.opened_at <= at), _ZERO)
        qty = (older * percent / 100).quantize(_QTY_QUANTUM, rounding=ROUND_DOWN)
        if qty == _ZERO:
            return
        # The ledger replays trades in time order, so the log gets this sale at
        # its own time, ahead of the newer buys; the replay must still balance.
        self._sell(asset, qty, at)
        self.trades[asset].sort(key=lambda t: (t.executed_at, int(t.ref)))

    # ------------------------------------------------------------------
    # invariants
    # ------------------------------------------------------------------

    @invariant()
    def quantity_is_conserved_per_asset(self) -> None:
        """Bought minus sold equals the remaining quantity of the asset's real lots."""
        for asset in _ASSETS:
            open_real = sum(
                (lot.remaining_qty.amount for lot in self.real.values() if lot.asset == asset),
                _ZERO,
            )
            assert open_real == self.bought[asset] - self.sold[asset]

    @invariant()
    def real_lots_match_the_model(self) -> None:
        """Each real lot has exactly the model's remaining quantity (oldest drained first)."""
        for asset in _ASSETS:
            for m in self.model[asset]:
                assert self.real[m.lot_id.value].remaining_qty.amount == m.remaining

    @invariant()
    def at_most_one_partly_drawn_lot_per_asset(self) -> None:
        """FIFO leaves the drained lots strictly older than every untouched one.

        Among an asset's lots opened at the same time or earlier than a lot with
        quantity left, at most one is partly drawn: the boundary lot.
        """
        for asset in _ASSETS:
            partial = [
                m
                for m in self.model[asset]
                if _ZERO < m.remaining < self.real[m.lot_id.value].original_qty.amount
            ]
            assert len(partial) in {0, 1}, partial

    @invariant()
    def remaining_stays_within_bounds(self) -> None:
        """0 <= remaining <= original for every lot."""
        for lot in self.real.values():
            assert _ZERO <= lot.remaining_qty.amount <= lot.original_qty.amount

    @invariant()
    def ledger_replay_agrees(self) -> None:
        """Replay the same trades as the ledger does: same remainders, same sold total."""
        for asset in _ASSETS:
            trades = self.trades[asset]
            if not trades:
                continue
            projection = replay_fifo_eur(asset, trades).unwrap()
            remaining = {lot.buy_ref: lot.remaining_qty for lot in projection.lots}
            for m in self.model[asset]:
                assert remaining[self.ref_of_lot[m.lot_id.value]] == m.remaining
            sold_in_replay = sum((c.qty for c in projection.consumptions), _ZERO)
            assert sold_in_replay == self.sold[asset]


FifoLotsMachine.TestCase.settings = settings(
    stateful_step_count=80 if _EXPLORE else 30,
)
TestFifoLotsMachine = FifoLotsMachine.TestCase


@pytest.mark.xfail(
    strict=True,
    reason=(
        "suspected: select_lots_fifo draws a lot opened after the sale time when the "
        "older lots cannot cover the sale (selection.py filters by asset only)"
    ),
)
def test_fifo_never_draws_a_lot_opened_after_the_sale() -> None:
    """FIFO never draws from a lot dated after the sale (ADR-003 §03).

    Shrunk from the machine: buy 1 BTC at 09:00, buy 1 BTC at 10:00, sell
    1.5 BTC at 09:30. Only the 09:00 lot existed at the sale, so the sale must
    be refused; the function instead draws 0.5 from the 10:00 lot.
    """
    early = Lot.open(
        lot_id=_lot_id(1),
        asset=Currency.BTC,
        qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
        cost_basis_per_unit=_COST,
        strategy_id=_STRATEGY,
        opened_at=_T0,
    ).unwrap()
    late = Lot.open(
        lot_id=_lot_id(2),
        asset=Currency.BTC,
        qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
        cost_basis_per_unit=_COST,
        strategy_id=_STRATEGY,
        opened_at=_T0 + timedelta(hours=1),
    ).unwrap()
    result = select_lots_fifo(
        [early, late],
        Quantity(amount=Decimal("1.5"), asset=Currency.BTC),
        _SELL_PRICE,
        _T0 + timedelta(minutes=30),
    )
    assert isinstance(result, Failure)
    assert result.failure() == InsufficientInventory(
        requested=Quantity(amount=Decimal("1.5"), asset=Currency.BTC),
        available=Quantity(amount=Decimal("1"), asset=Currency.BTC),
    )
