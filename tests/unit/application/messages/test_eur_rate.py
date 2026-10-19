"""Tests for the notifier's EUR rate resolution.

Invariant protected: a missing, silent, invalid or failing lookup means "no
rate", so a push never fails or shows a wrong euro figure because of it.

Reference: openspec/archive/668-eur-prices-in-pushes/change.md (D-2, R-2).
"""

from decimal import Decimal

import pytest

from hermes.application.messages.eur_rate import resolve_eur_rate
from hermes.domain.events.base import DomainEvent
from tests.support.notification_samples import samples

_EVENT = samples()["signal-entry"]


def test_no_lookup_means_no_rate() -> None:
    """An adapter built without a lookup renders in the quote currency."""
    assert [resolve_eur_rate(None, _EVENT)] == [None]


def test_the_lookup_answer_is_returned_for_the_event_it_was_asked_about() -> None:
    """The lookup receives the very event being rendered and its rate is used as is."""
    seen: list[DomainEvent] = []

    def lookup(event: DomainEvent) -> Decimal | None:
        seen.append(event)
        return Decimal("1.1721")

    assert (resolve_eur_rate(lookup, _EVENT), seen) == (Decimal("1.1721"), [_EVENT])


def test_a_lookup_with_no_answer_means_no_rate() -> None:
    """A cold cache (None) falls back to the quote currency."""
    assert [resolve_eur_rate(lambda _event: None, _EVENT)] == [None]


@pytest.mark.parametrize("bad", [Decimal(0), Decimal("-1.1")])
def test_a_non_positive_rate_is_never_used(bad: Decimal) -> None:
    """Dividing by zero or a negative rate would print a wrong price, so it is dropped."""
    assert [resolve_eur_rate(lambda _event: bad, _EVENT)] == [None]


def test_a_raising_lookup_means_no_rate_and_does_not_propagate() -> None:
    """A broken lookup must not stop the push: the notification still goes out in USDT."""

    def boom(_event: DomainEvent) -> Decimal | None:
        raise RuntimeError("cache exploded")

    assert [resolve_eur_rate(boom, _EVENT)] == [None]
