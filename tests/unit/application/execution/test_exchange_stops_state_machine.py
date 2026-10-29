"""State machine for :class:`ExchangeStopKeeper` (#1080): any order of moves, fills and faults.

Rule (ADR-024 §7): after every pass that records no failure, the exchange
holds exactly the stop the held position wants, at the strategy's level
rounded down, and nothing when flat or when the exchange already sold the
position. A pass that fails leaves at most the stops that were there, never
an extra one, and the next clean pass repairs the book.

The reference model is the wanted stop and whether its entry was sold; the
fake venue stands in for the exchange (seam:
``tests/integration/scenarios/test_exchange_stops.py``).
"""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from hermes.application.execution.exchange_stops import ExchangeStopKeeper
from hermes.domain.events.exchange_stop_changed import ExchangeStopChanged, StopAction
from hermes.domain.trading.exchange_stop import EXCHANGE_STEP, Protection
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.support.exchange_stops import (
    FakeStopVenue,
    FixedClock,
    MemoryStopJournal,
    not_placed,
    refusal,
    status_unknown,
)

BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
STRATEGY = StrategyId("swing-donchian-4h")
FIRST_ENTRY = datetime(2024, 10, 29, 3, 59, 59, 999000, tzinfo=UTC)

_stops = st.decimals(min_value=Decimal(50), max_value=Decimal(200), places=10)
_qtys = st.decimals(min_value=Decimal("0.001"), max_value=Decimal(5), places=9)
_faults = st.sampled_from(["list", "place", "place_twice", "unknown", "cancel", "fate"])


class KeeperMachine(RuleBasedStateMachine):
    """Drive one pair through entries, moved stops, exits, fills, drops and faults."""

    def __init__(self) -> None:
        """Start flat, with an empty book and ledger."""
        super().__init__()
        self.venue = FakeStopVenue()
        self.journal = MemoryStopJournal()
        self.keeper = ExchangeStopKeeper(
            exchange=self.venue, journal=self.journal, strategy_id=STRATEGY, clock=FixedClock()
        )
        self.wanted: Protection | None = None
        self.sold: set[datetime] = set()
        self.entries = 0
        self.last_failed = False
        self.kept = False
        self.before: set[str] = set()

    @rule(stop=_stops, qty=_qtys)
    def enter(self, stop: Decimal, qty: Decimal) -> None:
        """Open a new position (a new entry time)."""
        self.kept = False
        self.entries += 1
        entry = FIRST_ENTRY + timedelta(days=self.entries)
        self.wanted = Protection(BTC, entry, qty, stop)

    @precondition(lambda self: self.wanted is not None)
    @rule(stop=_stops)
    def move(self, stop: Decimal) -> None:
        """Move the held position's stop, as the strategy does."""
        self.kept = False
        assert self.wanted is not None
        self.wanted = Protection(BTC, self.wanted.entry_time, self.wanted.qty, stop)

    @rule()
    def exit(self) -> None:
        """Exit the position: flat."""
        self.kept = False
        self.wanted = None

    @rule()
    def market_reaches_the_stop(self) -> None:
        """Every resting stop fills, as a crash through the level would."""
        self.kept = False
        for order_id in list(self.venue.resting):
            row = self.journal.latest_exchange_stop(STRATEGY, BTC)
            if row is not None and row.order_id == order_id and row.entry_time is not None:
                self.sold.add(row.entry_time)
            self.venue.trigger(order_id)

    @rule()
    def cancelled_outside_hermes(self) -> None:
        """Someone cancels every resting stop on the exchange."""
        self.kept = False
        for order_id in list(self.venue.resting):
            self.venue.drop(order_id)

    @rule(fault=_faults)
    def fault(self, fault: str) -> None:
        """Make the next call of a kind fail."""
        self.kept = False
        errors = {
            "list": ("list", refusal("HTTP 503")),
            "place": ("place", refusal()),
            "place_twice": ("place", not_placed()),
            "unknown": ("place", status_unknown()),
            "cancel": ("cancel", refusal()),
            "fate": ("fate", refusal("HTTP 503")),
        }
        method, error = errors[fault]
        self.venue.fail_next(method, error)
        if fault == "place_twice":
            self.venue.fail_next(method, not_placed())

    @rule()
    def keep(self) -> None:
        """One keeper pass."""
        self.before = set(self.venue.resting)
        events = self.keeper.keep(BTC, self.wanted, correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0")
        self.last_failed = any(
            isinstance(e, ExchangeStopChanged) and e.action is StopAction.FAILED for e in events
        )
        self.venue.failures.clear()
        self.kept = not self.last_failed

    @invariant()
    def a_clean_pass_leaves_exactly_the_wanted_stop(self) -> None:
        """Right after a pass without failure the book is the wanted stop, or empty."""
        if not self.kept:
            return
        book = sorted((s.qty, s.stop_price) for s in self.venue.resting.values())
        wanted = self.wanted
        expected = (
            []
            if wanted is None or wanted.entry_time in self.sold
            else [
                (
                    wanted.qty.quantize(EXCHANGE_STEP, rounding=ROUND_DOWN),
                    wanted.stop_price.quantize(EXCHANGE_STEP, rounding=ROUND_DOWN),
                )
            ]
        )
        assert book == expected

    @invariant()
    def a_failed_pass_never_adds_a_stop_it_did_not_record(self) -> None:
        """Allow a failed pass at most one new stop, the one whose fate is unknown."""
        if self.last_failed:
            assert len(set(self.venue.resting) - self.before) <= 1


TestKeeperMachine = KeeperMachine.TestCase
TestKeeperMachine.settings = settings(max_examples=150, stateful_step_count=30, deadline=None)
