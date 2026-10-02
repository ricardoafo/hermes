"""SignalEmitted domain event — a strategy produced a trading signal.

The strategy still returns a :class:`~hermes.domain.strategy.signal.StrategySignal`
value object; the serve loop wraps it in this event and publishes it so the
signal reaches the ledger and any subscriber. The signal is the value, the
event is its persisted shape.

The event is flat: every ``StrategySignal`` field except ``signal_time`` is
copied as its own field, and two fields the signal lacks are added
(``strategy_id`` and ``pair``). ``occurred_at`` is the signal time, which is
the close time of the triggering bar, so the event carries no wall clock and
replays deterministically.

Reference: 308-signal-event-portfolio-state change.md D-1, D-2;
    ADR-011 D-03 (strategy_id on every record), D-15 (replay), D-16 (events);
    code-conventions.md §10 (event schema versioning).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["SignalEmitted"]


@dataclass(frozen=True, slots=True)
class SignalEmitted(DomainEvent):
    """A strategy emitted an entry or exit signal for one pair.

    Build it with :meth:`from_signal` so the field mapping and the time
    source live in one place.

    Fields:
        strategy_id: The strategy bucket that produced the signal.
        pair: The pair the signal applies to. Signals carry no pair; the
            caller knows it from the bar that triggered the signal.
        kind: ``entry`` opens a position, ``exit`` closes one.
        side: Direction of the signal.
        entry_price: Close price of the signal bar. ``None`` on pure exits.
        stop_price: Trailing-stop level on the signal bar.
        atr_context: ATR value on the signal bar used for sizing.
        exit_reason: Why an exit fired. ``None`` on entries.
        f_star_estimate: Kelly fraction estimate. ``None`` until the
            pre-registered trade-count threshold is met.

    Raises:
        ValueError: On a naive ``occurred_at`` or a negative ``event_seq``.

    Reference: 308-signal-event-portfolio-state change.md D-2.
    """

    __event_schema_version__: ClassVar[int] = 1

    strategy_id: StrategyId
    pair: Pair
    kind: SignalKind
    side: SignalSide
    entry_price: Decimal | None
    stop_price: Decimal
    atr_context: Decimal
    exit_reason: str | None = None
    f_star_estimate: Decimal | None = None

    def __post_init__(self) -> None:
        """Compute the payload hash, then require a UTC-aware ``occurred_at``.

        The base initialiser is called by name: ``super()`` does not work in a
        slotted dataclass, and skipping it leaves ``payload_sha256`` empty.

        Raises:
            ValueError: If ``event_seq`` is negative or ``occurred_at`` is naive.
        """
        DomainEvent.__post_init__(self)
        if self.occurred_at.tzinfo is None:
            raise ValueError("SignalEmitted.occurred_at must be UTC-aware")

    @classmethod
    def from_signal(
        cls,
        signal: StrategySignal,
        *,
        strategy_id: StrategyId,
        pair: Pair,
        correlation_id: str,
    ) -> SignalEmitted:
        """Wrap a strategy signal in an event.

        Args:
            signal: The signal the strategy returned.
            strategy_id: The strategy bucket that produced it.
            pair: The pair of the bar that triggered the signal.
            correlation_id: ULID that ties this event to its processing cycle.

        Returns:
            An event whose ``occurred_at`` is ``signal.signal_time``.
        """
        return cls(
            occurred_at=signal.signal_time,
            correlation_id=correlation_id,
            strategy_id=strategy_id,
            pair=pair,
            kind=signal.kind,
            side=signal.side,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            atr_context=signal.atr_context,
            exit_reason=signal.exit_reason,
            f_star_estimate=signal.f_star_estimate,
        )
