"""Scripted FxRateProviderPort for the ledger use-case tests.

The seam it replaces is covered against recorded Frankfurter responses in
``tests/integration/adapters/fx/test_frankfurter_recorded.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from hermes.domain.errors import FxRateError
from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.value_objects.currency import Currency


@dataclass
class FakeFx:
    """Answers ``get_for_date`` from a table; unknown dates or ``down=True`` raise.

    Attributes:
        rates: ``{requested date: (rate, resolved date)}``.
        down: When ``True`` every call raises :class:`FxRateError`.
        calls: Every ``(from, to, on)`` received, in order.

    """

    rates: dict[date, tuple[Decimal, date]] = field(default_factory=dict)
    down: bool = False
    calls: list[tuple[Currency, Currency, date]] = field(default_factory=list)

    def get_latest(self, from_currency: Currency, to_currency: Currency) -> FxRate:
        """Not used by the ledger use cases."""
        raise FxRateError("FakeFx.get_latest is not scripted")

    def get_for_date(self, from_currency: Currency, to_currency: Currency, on: date) -> FxRate:
        """Return the scripted rate for ``on`` or raise like an outage."""
        self.calls.append((from_currency, to_currency, on))
        if self.down or on not in self.rates:
            raise FxRateError(f"FakeFx: no rate for {on}")
        rate, resolved = self.rates[on]
        return FxRate(
            from_currency=from_currency, to_currency=to_currency, rate=rate, date=resolved
        )
