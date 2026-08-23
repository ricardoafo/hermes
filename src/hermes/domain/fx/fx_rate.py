"""FX rate value object — one official rate for a currency pair on a given day.

An :class:`FxRate` binds three pieces of information together:

- The ordered pair ``(from_currency, to_currency)``.
- A positive :class:`~decimal.Decimal` conversion multiplier.
- The :class:`~datetime.date` the rate is authoritative for.

The domain guarantees that rates are always positive, never self-referential
(``from != to``), and that :meth:`FxRate.convert` refuses to touch a
:class:`~hermes.domain.value_objects.money.Money` whose currency does not match
``from_currency``. Every failure mode is a construction-time or call-time
:class:`ValueError` — arithmetic never silently coerces.

Reference: code-conventions.md §1 (Value Object), §6 (Decimal for ledger values),
docs/decisions/2026-08-23-fx-rate-source-frankfurter.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money

__all__ = ["FxRate"]


@dataclass(frozen=True, slots=True)
class FxRate:
    """Immutable official FX rate for one currency pair on one calendar day.

    Attributes:
        from_currency: Source currency — the currency :meth:`convert`
            accepts on its input :class:`Money`.
        to_currency: Target currency — the currency :meth:`convert`
            stamps on its output :class:`Money`.
        rate: Multiplier applied to convert one unit of ``from_currency``
            into ``to_currency``. Must be strictly positive; zero or
            negative rates are meaningless for currency conversion and
            rejected in :meth:`__post_init__`.
        date: The calendar date the rate is authoritative for. Frankfurter
            returns the closest prior business day when the caller asks
            for a weekend or holiday — that resolution is a wire concern,
            not a domain concern. Whatever date the adapter derives from
            the response is stored here verbatim.

    Reference: code-conventions.md §1 (Value Object).
    """

    from_currency: Currency
    to_currency: Currency
    rate: Decimal
    date: date

    def __post_init__(self) -> None:
        """Reject non-positive rates and self-referential pairs at construction time.

        Enforcing these invariants in the constructor means every
        :class:`FxRate` instance downstream can be trusted without
        re-validation — the domain model IS the contract.

        Raises:
            ValueError: On ``rate <= 0`` or ``from_currency == to_currency``.
        """
        if self.rate <= Decimal("0"):
            raise ValueError(
                f"FxRate.rate must be > 0, got {self.rate} "
                f"({self.from_currency} → {self.to_currency})"
            )
        if self.from_currency == self.to_currency:
            raise ValueError(
                f"FxRate.from_currency and to_currency must differ, got both = {self.from_currency}"
            )

    def convert(self, amount: Money) -> Money:
        """Convert ``amount`` from :attr:`from_currency` to :attr:`to_currency`.

        The conversion is a pure Decimal multiplication — no rounding, no
        precision loss. Callers that need a specific quantisation (e.g.
        2-decimal EUR for tax reports) MUST apply it themselves at the
        output edge; the FX layer intentionally preserves full precision
        so the same rate can drive both a display and a settlement without
        divergent rounding paths.

        Args:
            amount: :class:`Money` to convert. Its ``currency`` MUST equal
                :attr:`from_currency` — cross-rate conversion (e.g. USD →
                EUR via a GBP rate) is not attempted and never will be by
                this method.

        Returns:
            A new :class:`Money` whose ``currency`` is :attr:`to_currency`
            and whose ``amount`` is ``amount.amount * rate``.

        Raises:
            ValueError: When ``amount.currency`` does not match
                :attr:`from_currency`. Raised (not returned as ``Result``)
                because a mismatch is a programming error at the callsite,
                not an expected business outcome.
        """
        if amount.currency != self.from_currency:
            raise ValueError(
                f"FxRate.convert: amount currency {amount.currency} does not match "
                f"from_currency {self.from_currency}"
            )
        return Money(amount=amount.amount * self.rate, currency=self.to_currency)
