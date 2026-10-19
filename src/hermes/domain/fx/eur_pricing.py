"""EUR pricing of a fill — the official rate a trade row is converted with.

ADR-002 §3 requires EUR values on every trade row; ADR-016 item 5 names the
source: the ECB EUR/USD reference rate for the fill date (or the prior business
day), with USDT read as USD. An :class:`EurPricing` holds that rate exactly as
the ECB quotes it — units of the quote currency per one euro — so the stored
figure can be checked against the ECB publication.

Domain purity: stdlib and other domain value objects only.

Reference: ADR-002 §3, ADR-016 items 2 and 5, change ``trade-eur-values`` D-1, D-2.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.time import MADRID

__all__ = [
    "IDENTITY_SOURCE",
    "EurPricing",
    "EurStatus",
    "fill_date_madrid",
    "rate_is_final",
]

IDENTITY_SOURCE: Final[str] = "identity"


class EurStatus(StrEnum):
    """Whether a trade row carries its EUR values yet.

    :attr:`OK` — EUR columns are filled from a final rate.
    :attr:`PENDING` — the rate lookup failed or is not final; a backfill fills it.
    """

    OK = "ok"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class EurPricing:
    """The EUR rate one trade is converted with.

    Attributes:
        quote: Currency the rate converts from (the pair's quote, e.g. USDT).
        rate: Units of ``quote`` per one EUR, as the ECB quotes it
            (``1.1721`` means 1 EUR = 1.1721 USD). Strictly positive.
        rate_date: Business day the rate was published for.
        source: Where the rate came from (``ecb:frankfurter``, ``identity``).
    """

    quote: Currency
    rate: Decimal
    rate_date: date
    source: str

    def __post_init__(self) -> None:
        """Reject a non-positive rate, a blank source and an EUR rate other than 1.

        Raises:
            ValueError: On any violated invariant.
        """
        if self.rate <= Decimal("0"):
            raise ValueError(f"EurPricing.rate must be > 0, got {self.rate}")
        if not self.source.strip():
            raise ValueError("EurPricing.source must be non-empty")
        if self.quote == Currency.EUR and self.rate != Decimal("1"):
            raise ValueError(f"EurPricing for EUR must have rate 1, got {self.rate}")

    @classmethod
    def identity(cls, on: date) -> EurPricing:
        """Return the pricing of an EUR-quoted trade: rate 1, no lookup.

        Args:
            on: The fill date.

        Returns:
            An :class:`EurPricing` for EUR with rate 1 and source ``identity``.
        """
        return cls(quote=Currency.EUR, rate=Decimal("1"), rate_date=on, source=IDENTITY_SOURCE)

    @classmethod
    def from_fx_rate(cls, fx: FxRate, *, source: str) -> EurPricing:
        """Build a pricing from an ``EUR -> quote`` :class:`FxRate`.

        Args:
            fx: A rate whose ``from_currency`` is EUR, so ``fx.rate`` is quote
                units per euro.
            source: Name of the rate source, stored on the trade row.

        Returns:
            The pricing for ``fx.to_currency`` on ``fx.date``.

        Raises:
            ValueError: When ``fx.from_currency`` is not EUR.
        """
        if fx.from_currency != Currency.EUR:
            raise ValueError(
                f"EurPricing.from_fx_rate needs an EUR-based rate, got {fx.from_currency}"
            )
        return cls(quote=fx.to_currency, rate=fx.rate, rate_date=fx.date, source=source)

    def to_eur(self, amount: Money) -> Money:
        """Convert a quote-currency amount to EUR at full precision.

        Args:
            amount: Money in :attr:`quote`.

        Returns:
            ``amount / rate`` in EUR.

        Raises:
            ValueError: When ``amount`` is not in :attr:`quote`.
        """
        if amount.currency != self.quote:
            raise ValueError(
                f"EurPricing.to_eur: amount in {amount.currency}, pricing is for {self.quote}"
            )
        return Money(amount=amount.amount / self.rate, currency=Currency.EUR)


def fill_date_madrid(at: datetime) -> date:
    """Return the Europe/Madrid calendar date of an instant.

    ADR-016 item 2 fixes the tax calendar in Madrid time; the EUR rate is
    looked up for the same calendar date so a trade and its tax year agree.

    Args:
        at: Timezone-aware instant.

    Returns:
        The Madrid calendar date.

    Raises:
        ValueError: When ``at`` is naive.
    """
    if at.tzinfo is None:
        raise ValueError(f"fill_date_madrid needs a timezone-aware datetime, got {at!r}")
    return at.astimezone(MADRID).date()


def rate_is_final(*, fill_date: date, rate_date: date, today: date) -> bool:
    """Return whether a looked-up rate may be stored for a fill.

    The ECB publishes the day's rate in the afternoon; a same-day lookup
    before that returns the prior business day, which is not the fill
    date's rate. Once the fill date is in the past, the closest prior
    business day is the right rate (weekends and holidays).

    Args:
        fill_date: Madrid calendar date of the fill.
        rate_date: Date the source resolved the lookup to.
        today: Madrid calendar date of the lookup.

    Returns:
        ``True`` when the rate is for the fill date, or the fill date is
        before ``today``.
    """
    return rate_date == fill_date or fill_date < today
