"""RiskHaltRecorded domain event — a risk-overlay HALT or a §10 kill refused an entry.

Record-only (#749): the event says that a halt or a kill stopped an entry
signal. It decides nothing; whether and when halts and kills fire stays with
the order router and the kill evaluators.

The reason is value-free: it names the rule, never a price, a drawdown or a
Sharpe, so the row can reach notifications and ``hermes status`` while the
strategy is uncertified (ADR-012 D-06).

Reference: GitHub issue #749; pre-registration-v1.md §10.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final

from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["HaltKind", "RiskHaltRecorded"]

_RULE_SHAPE: Final = re.compile(r"^(RO-\d{2}|[a-z0-9]+(_[a-z0-9]+)*)$")
_REASON_SHAPE: Final = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
_REF_SHAPE: Final = re.compile(r"^§\d+(\.\d+)*$")


class HaltKind(StrEnum):
    """What stopped the entry."""

    HALT = "halt"
    """A risk-overlay rule returned HALT for this one entry."""

    KILL = "kill"
    """A pre-registration §10 kill criterion fired; no new entries while it holds."""


@dataclass(frozen=True, slots=True)
class RiskHaltRecorded(DomainEvent):
    """An entry signal was refused by a risk halt or a kill.

    ``occurred_at`` is the signal time, so evaluating the same bar again
    after a restart yields the same event and the ledger keeps one row.

    Fields:
        strategy_id: The strategy whose entry was refused.
        pair: The pair of the refused entry.
        kind: Halt or kill.
        rule: Stable rule id, such as ``RO-08`` or ``max_drawdown``.
        reason: Fixed snake_case reason, such as ``drawdown_limit``.
        pre_registration_ref: The pre-registration section the rule comes
            from (``§10``), or ``None`` when the rule cites none.

    Raises:
        ValueError: ``rule``, ``reason`` or ``pre_registration_ref`` is not
            of its fixed shape, so free text with figures cannot be recorded.
    """

    __event_schema_version__: ClassVar[int] = 1

    strategy_id: StrategyId
    pair: Pair
    kind: HaltKind
    rule: str
    reason: str
    pre_registration_ref: str | None

    def __post_init__(self) -> None:
        """Refuse free text in the value-free fields, then compute the payload hash."""
        if not _RULE_SHAPE.match(self.rule):
            raise ValueError(f"RiskHaltRecorded.rule must be a rule id: {self.rule!r}")
        if not _REASON_SHAPE.match(self.reason):
            raise ValueError(f"RiskHaltRecorded.reason must be a snake_case word: {self.reason!r}")
        if self.pre_registration_ref is not None and not _REF_SHAPE.match(
            self.pre_registration_ref
        ):
            raise ValueError(
                f"RiskHaltRecorded.pre_registration_ref must read §N: {self.pre_registration_ref!r}"
            )
        DomainEvent.__post_init__(self)
