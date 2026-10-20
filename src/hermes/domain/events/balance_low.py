"""BalanceLow domain event — emitted when a wallet balance falls below its floor.

Fires when a scheduled balance check finds the current holding of an
asset has crossed under the configured threshold. Downstream subscribers
notify the operator (Telegram push per the 2026-08-23 decisions journal)
so they can top up before the next trade cycle stalls.

Reference: ADR-003 §02 (balance guards), code-conventions.md §10 + §3.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money

__all__ = ["BalanceLow"]


@dataclass(frozen=True, slots=True)
class BalanceLow(DomainEvent):
    """A wallet holding fell below the configured threshold for its asset.

    Emitted from the balance-monitor use case. ``__post_init__`` enforces
    the tri-currency invariant: ``current.currency`` must equal both
    ``threshold.currency`` and ``asset`` — a mismatched trio is a domain
    modelling error and MUST fail fast.

    Fields:
        asset: The asset that ran low (e.g. ``Currency.USDT``).
        current: The current wallet balance in ``asset``. May be zero.
        threshold: The configured floor; balances at or below trigger
            the event. Must share ``asset``'s currency.
        checked_at: UTC-aware datetime when the check ran.

    Raises:
        ValueError: When ``current.currency`` or ``threshold.currency``
            do not match ``asset`` — construction-time invariant.

    Reference: ADR-003 §02 — balance guards.
    """

    __event_schema_version__: ClassVar[int] = 1

    asset: Currency
    current: Money
    threshold: Money
    checked_at: datetime

    def __post_init__(self) -> None:
        """Compute the payload hash, then enforce the tri-currency invariant.

        Domain invariant I-01 (code-conventions §1): value objects reject
        invalid states at construction. A mismatched trio here would
        silently corrupt downstream Money arithmetic and notification
        rendering — fail loudly instead.

        The base initialiser is called by name: ``super()`` does not work in a
        slotted dataclass, and skipping it leaves ``payload_sha256`` empty.
        """
        DomainEvent.__post_init__(self)
        if self.current.currency != self.asset:
            raise ValueError(
                f"BalanceLow.current.currency {self.current.currency!r} does "
                f"not match asset {self.asset!r}"
            )
        if self.threshold.currency != self.asset:
            raise ValueError(
                f"BalanceLow.threshold.currency {self.threshold.currency!r} does "
                f"not match asset {self.asset!r}"
            )
