"""EurPricer — looks up the EUR rate of a fill without ever failing it.

Wraps :class:`~hermes.ports.fx.FxRateProviderPort`: asks for the ECB rate of
``EUR -> quote`` on the fill's Madrid date, keeps final rates in a per-day
cache, and turns every lookup failure into ``None`` so the caller stores the
trade pending (change ``trade-eur-values`` D-1, D-2).

Reference: ADR-016 item 5.
"""

from __future__ import annotations

from datetime import date
from typing import Final

import structlog

from hermes.domain.errors import InfraError
from hermes.domain.fx.eur_pricing import EurPricing, rate_is_final
from hermes.domain.value_objects.currency import Currency
from hermes.ports.fx import FxRateProviderPort

__all__ = ["DEFAULT_EUR_SOURCE", "EurPricer"]

DEFAULT_EUR_SOURCE: Final[str] = "ecb:frankfurter"

_log = structlog.get_logger(__name__)


class EurPricer:
    """EUR rate lookups for the fill path and the backfill.

    Args:
        fx: Official rate provider. For USDT the composition root wires an
            adapter that reads USDT as USD (ADR-016 item 5).
        source: Name stored in ``trades.eur_source``.
    """

    def __init__(self, fx: FxRateProviderPort, *, source: str = DEFAULT_EUR_SOURCE) -> None:
        """Bind the provider and start an empty cache."""
        self._fx = fx
        self._source = source
        self._cache: dict[tuple[Currency, date], EurPricing] = {}

    def price(self, quote: Currency, fill_date: date, *, today: date) -> EurPricing | None:
        """Return the final EUR rate of ``quote`` for ``fill_date``, or ``None``.

        Args:
            quote: The pair's quote currency.
            fill_date: Madrid calendar date of the fill.
            today: Madrid calendar date of the lookup. A rate dated before
                ``fill_date`` is final only once ``fill_date < today``.

        Returns:
            The pricing, or ``None`` when the provider failed or its rate is
            not final yet. Never raises for a provider failure.
        """
        if quote == Currency.EUR:
            return EurPricing.identity(fill_date)
        cached = self._cache.get((quote, fill_date))
        if cached is not None:
            return cached
        try:
            fx = self._fx.get_for_date(Currency.EUR, quote, fill_date)
            pricing = EurPricing.from_fx_rate(fx, source=self._source)
        except (InfraError, ValueError) as exc:
            _log.warning(
                "eur_rate_lookup_failed",
                quote=quote.value,
                fill_date=fill_date.isoformat(),
                error=str(exc),
            )
            return None
        if not rate_is_final(fill_date=fill_date, rate_date=pricing.rate_date, today=today):
            _log.info(
                "eur_rate_not_final",
                quote=quote.value,
                fill_date=fill_date.isoformat(),
                rate_date=pricing.rate_date.isoformat(),
            )
            return None
        self._cache[(quote, fill_date)] = pricing
        return pricing
