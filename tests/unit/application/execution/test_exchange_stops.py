"""Unit tests for :class:`ExchangeStopKeeper` (#1080).

Rule (ADR-024 §7, Code Owner decision 2026-10-19): every real-money position
rests a stop held by the exchange, at the strategy's own stop level, as a
backstop and never a new exit. Invariants under test:

- a held position gets one stop, at the strategy's stop rounded down to the
  exchange step, for the quantity held;
- a moved stop cancels the old one before it places the new one;
- a flat pair keeps no stop; a stop the ledger does not name is cancelled,
  or adopted when it already is the wanted one (a crash or a lost answer);
- a stop that filled on the exchange is recorded and never placed again for
  the same position;
- a lost order the exchange confirms it never saw is sent once more; an
  unknown status is never retried blindly;
- every failure is recorded with a critical alert, and no alert names a price.

The fake venue stands in for the exchange; the seam with the real adapter and
the fake market is ``tests/integration/scenarios/test_exchange_stops.py``.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.application.execution.exchange_stops import STOP_SOURCE, ExchangeStopKeeper
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.exchange_stop_changed import ExchangeStopChanged, StopAction
from hermes.domain.trading.exchange_stop import Protection
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
ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
STRATEGY = StrategyId("swing-donchian-4h")
ENTRY = datetime(2024, 10, 29, 3, 59, 59, 999000, tzinfo=UTC)
LATER_ENTRY = datetime(2024, 11, 20, 7, 59, 59, 999000, tzinfo=UTC)
CID = "01JABCDEFGHJKMNPQRSTVWXYZ0"


def _wanted(stop: str = "140.123456789", qty: str = "0.5", entry: datetime = ENTRY) -> Protection:
    return Protection(BTC, entry, Decimal(qty), Decimal(stop))


def _keeper(venue: FakeStopVenue, journal: MemoryStopJournal) -> ExchangeStopKeeper:
    return ExchangeStopKeeper(
        exchange=venue, journal=journal, strategy_id=STRATEGY, clock=FixedClock()
    )


def _keep(
    venue: FakeStopVenue, journal: MemoryStopJournal, wanted: Protection | None, pair: Pair = BTC
) -> tuple[DomainEvent, ...]:
    return _keeper(venue, journal).keep(pair, wanted, correlation_id=CID)


def _actions(events: tuple[DomainEvent, ...]) -> list[tuple[str, str | None]]:
    return [(e.action.value, e.reason) for e in events if isinstance(e, ExchangeStopChanged)]


def _alerts(events: tuple[DomainEvent, ...]) -> list[AnomalyDetected]:
    return [e for e in events if isinstance(e, AnomalyDetected)]


def _book(venue: FakeStopVenue) -> list[tuple[str, Decimal, Decimal]]:
    return sorted((str(s.pair), s.qty, s.stop_price) for s in venue.resting.values())


def _placed(venue: FakeStopVenue, journal: MemoryStopJournal) -> str:
    """Rest the wanted stop for the entry and return its order id."""
    _keep(venue, journal, _wanted())
    (order_id,) = venue.resting
    return order_id


# SEV-1: S12 — a real-money position left with no stop held by the exchange.
def test_a_held_position_rests_one_stop_at_the_strategy_stop_rounded_down() -> None:
    """140.123456789 rests at 140.12345678: never above the strategy's stop (#1080)."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    _keep(venue, journal, _wanted())
    assert _book(venue) == [("BTC/USDT", Decimal("0.5"), Decimal("140.12345678"))]


def test_a_placed_stop_is_recorded_with_its_order_and_entry() -> None:
    """The ledger names the order, the position it protects, and why it was placed."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    order_id = _placed(venue, journal)
    row = journal.latest_exchange_stop(STRATEGY, BTC)
    assert row is not None
    assert (row.action, row.order_id, row.entry_time, row.qty, row.stop_price, row.reason) == (
        StopAction.PLACED,
        order_id,
        ENTRY,
        Decimal("0.5"),
        Decimal("140.12345678"),
        "protect_position",
    )


def test_an_unchanged_stop_sends_no_order_and_records_nothing() -> None:
    """A second pass with the same stop only lists the book: no cancel, no place, no row."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    _placed(venue, journal)
    venue.calls.clear()
    events = _keep(venue, journal, _wanted())
    assert (venue.calls, events) == ([("list", "BTC/USDT")], ())


def test_a_moved_stop_cancels_the_old_one_before_placing_the_new_one() -> None:
    """The cancel frees the quantity the old stop locks, so it goes first (#1080 D-4)."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    old = _placed(venue, journal)
    venue.calls.clear()
    _keep(venue, journal, _wanted(stop="142"))
    assert venue.calls == [("list", "BTC/USDT"), ("cancel", old), ("place", "BTC/USDT")]


def test_a_moved_stop_leaves_only_the_new_level_resting() -> None:
    """After the strategy moves its stop to 142, the book holds 142 alone."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    _placed(venue, journal)
    events = _keep(venue, journal, _wanted(stop="142"))
    assert (_book(venue), _actions(events)) == (
        [("BTC/USDT", Decimal("0.5"), Decimal("142"))],
        [("cancelled", "stop_moved"), ("placed", "protect_position")],
    )


def test_a_flat_pair_cancels_its_stop() -> None:
    """Once the position exits, its stop is cancelled and the book is empty."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    _placed(venue, journal)
    events = _keep(venue, journal, None)
    assert (_book(venue), _actions(events)) == ([], [("cancelled", "position_closed")])


def test_a_stop_still_resting_when_the_strategy_exits_is_cancelled() -> None:
    """A stop-limit that gapped under its limit still rests; the strategy's exit runs (#1080).

    The keeper is told the pair is flat before the exit order, and cancels it.
    """
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    order_id = _placed(venue, journal)
    _keep(venue, journal, None)
    assert venue.calls[-1] == ("cancel", order_id)


def test_a_flat_pair_with_nothing_resting_records_nothing() -> None:
    """No stop, no position: one listing and no row."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    assert (_keep(venue, journal, None), venue.calls) == ((), [("list", "BTC/USDT")])


def test_a_resting_stop_the_ledger_does_not_name_is_adopted_when_it_is_the_wanted_one() -> None:
    """Placed, then a crash before the row: the restart keeps it and places no second one."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    found = venue.rest(BTC, "0.5", "140.12345678")
    events = _keep(venue, journal, _wanted())
    assert (list(venue.resting), _actions(events)) == ([found], [("adopted", "found_on_exchange")])


def test_a_resting_stop_the_ledger_does_not_name_is_cancelled_when_flat() -> None:
    """An orphan stop on a flat pair would sell coins hermes does not hold for it."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.rest(BTC, "0.5", "140")
    events = _keep(venue, journal, None)
    assert (_book(venue), _actions(events)) == ([], [("cancelled", "not_in_ledger")])


def test_a_second_resting_stop_is_cancelled_and_the_wanted_one_kept() -> None:
    """Two stops would lock twice the quantity; the one at the wanted level stays."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    kept = _placed(venue, journal)
    venue.rest(BTC, "0.5", "139")
    _keep(venue, journal, _wanted())
    assert list(venue.resting) == [kept]


def test_another_pairs_stop_is_left_alone() -> None:
    """Keeping BTC never cancels ETH's stop."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    eth = venue.rest(ETH, "2", "90")
    _keep(venue, journal, None)
    assert list(venue.resting) == [eth]


def test_a_stop_that_filled_on_the_exchange_is_recorded_filled() -> None:
    """The market reached the stop while hermes was down: the row says it sold."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.trigger(_placed(venue, journal))
    events = _keep(venue, journal, _wanted())
    assert _actions(events) == [("filled", "sold_on_exchange")]


# SEV-1: F5 — a position the exchange already sold is never protected (sold) twice.
def test_a_position_whose_stop_filled_gets_no_new_stop() -> None:
    """The exchange sold it; a new stop would sell coins that are gone."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.trigger(_placed(venue, journal))
    _keep(venue, journal, _wanted())
    _keep(venue, journal, _wanted())
    assert (_book(venue), [c for c in venue.calls if c[0] == "place"]) == (
        [],
        [("place", "BTC/USDT")],
    )


def test_a_later_position_after_a_filled_stop_gets_its_own_stop() -> None:
    """The filled row protects one entry only: the next entry rests a stop again."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.trigger(_placed(venue, journal))
    _keep(venue, journal, _wanted())
    _keep(venue, journal, _wanted(stop="150", entry=LATER_ENTRY))
    assert _book(venue) == [("BTC/USDT", Decimal("0.5"), Decimal("150"))]


def test_a_filled_stop_raises_a_warning_alert() -> None:
    """The operator learns the exchange sold, from the exchange stop alert source."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.trigger(_placed(venue, journal))
    alerts = _alerts(_keep(venue, journal, _wanted()))
    assert [(a.source, a.severity) for a in alerts] == [(STOP_SOURCE, AnomalySeverity.WARNING)]


def test_a_stop_gone_from_the_exchange_is_recorded_and_placed_again() -> None:
    """Cancelled outside hermes: nothing sold, so the held position rests a new stop."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.drop(_placed(venue, journal))
    events = _keep(venue, journal, _wanted())
    assert (_actions(events), _book(venue)) == (
        [("gone", "no_longer_on_exchange"), ("placed", "protect_position")],
        [("BTC/USDT", Decimal("0.5"), Decimal("140.12345678"))],
    )


def test_a_placement_the_exchange_never_saw_is_sent_once_more() -> None:
    """Confirmed not placed (#858) is safe to retry: one stop rests after the retry."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.fail_next("place", not_placed())
    events = _keep(venue, journal, _wanted())
    assert (len(venue.resting), _actions(events)) == (1, [("placed", "protect_position")])


def test_a_placement_never_seen_twice_fails_with_a_critical_alert() -> None:
    """After the one retry the keeper records the failure and alerts; no stop rests."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.fail_next("place", not_placed())
    venue.fail_next("place", not_placed())
    events = _keep(venue, journal, _wanted())
    assert (
        _book(venue),
        _actions(events),
        [a.severity for a in _alerts(events)],
    ) == ([], [("failed", "not_placed")], [AnomalySeverity.CRITICAL])


def test_an_unknown_status_is_not_retried() -> None:
    """The stop may rest: a blind retry could rest two. One placement call, one stop."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.fail_next("place", status_unknown())
    events = _keep(venue, journal, _wanted())
    assert (
        [c for c in venue.calls if c[0] == "place"],
        len(venue.resting),
        _actions(events),
    ) == ([("place", "BTC/USDT")], 1, [("failed", "status_unknown")])


def test_an_unknown_status_alert_names_the_client_order_id() -> None:
    """The operator can look the order up on the exchange by its client id."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.fail_next("place", status_unknown())
    (alert,) = _alerts(_keep(venue, journal, _wanted()))
    assert "look up client order id hermes-bbbb" in alert.reason


def test_the_stop_whose_status_was_unknown_is_adopted_on_the_next_pass() -> None:
    """The next bar finds it resting at the wanted level and records it, placing nothing."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.fail_next("place", status_unknown())
    _keep(venue, journal, _wanted())
    events = _keep(venue, journal, _wanted())
    assert (len(venue.resting), _actions(events)) == (1, [("adopted", "found_on_exchange")])


def test_a_refused_placement_fails_with_a_critical_alert() -> None:
    """A short balance or a stop above the market leaves the position on its own exit."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.fail_next("place", refusal())
    events = _keep(venue, journal, _wanted())
    assert (_actions(events), [a.severity for a in _alerts(events)]) == (
        [("failed", "refused")],
        [AnomalySeverity.CRITICAL],
    )


def test_an_unreadable_book_fails_without_cancelling_or_placing() -> None:
    """With the listing down, the keeper cannot know what rests, so it sends nothing."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.fail_next("list", refusal("HTTP 503"))
    events = _keep(venue, journal, _wanted())
    assert (venue.calls, _actions(events)) == (
        [("list", "BTC/USDT")],
        [("failed", "exchange_unreadable")],
    )


def test_a_refused_cancel_while_moving_places_no_second_stop() -> None:
    """The old stop may still lock the quantity: the new one waits for the next pass."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    old = _placed(venue, journal)
    venue.fail_next("cancel", refusal())
    events = _keep(venue, journal, _wanted(stop="142"))
    assert (list(venue.resting), _actions(events)) == ([old], [("failed", "cancel_failed")])


def test_an_unreadable_fate_fails_and_places_nothing() -> None:
    """The ledger's stop left the book and the exchange cannot say why: no new stop yet."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    venue.trigger(_placed(venue, journal))
    venue.fail_next("fate", refusal("HTTP 503"))
    events = _keep(venue, journal, _wanted())
    assert (_book(venue), _actions(events)) == ([], [("failed", "fate_unreadable")])


def test_a_failure_does_not_hide_the_resting_row_from_the_next_pass() -> None:
    """After a failed move the ledger still names the old stop, so the next pass moves it."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    old = _placed(venue, journal)
    venue.fail_next("cancel", refusal())
    _keep(venue, journal, _wanted(stop="142"))
    row = journal.latest_exchange_stop(STRATEGY, BTC)
    assert row is not None
    assert (row.action, row.order_id) == (StopAction.PLACED, old)


def test_a_quantity_below_the_exchange_step_fails_without_an_order() -> None:
    """0.000000001 BTC rounds to zero: nothing can rest, and the operator is told."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    events = _keep(venue, journal, _wanted(qty="0.000000001"))
    assert (venue.calls, _actions(events)) == ([], [("failed", "below_exchange_step")])


@pytest.mark.parametrize(
    "setup",
    ["filled", "gone", "refused", "unknown", "not_placed", "unreadable"],
)
def test_no_alert_names_a_price(setup: str) -> None:
    """Neither the stop level nor the quantity reaches an alert's text (S8, ADR-012 D-02)."""
    venue, journal = FakeStopVenue(), MemoryStopJournal()
    if setup in {"filled", "gone"}:
        order_id = _placed(venue, journal)
        (venue.trigger if setup == "filled" else venue.drop)(order_id)
    errors = {
        "refused": ("place", refusal()),
        "unknown": ("place", status_unknown()),
        "not_placed": ("place", not_placed()),
        "unreadable": ("list", refusal()),
    }
    if setup in errors:
        method, error = errors[setup]
        venue.fail_next(method, error)
        if setup == "not_placed":
            venue.fail_next(method, not_placed())
    (reason,) = [a.reason for a in _alerts(_keep(venue, journal, _wanted()))]
    assert ("140" in reason, "0.5" in reason) == (False, False)
