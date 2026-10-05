"""EurPricer — looks up the EUR rate of a fill without ever failing it.

Wraps :class:`~hermes.ports.fx.FxRateProviderPort`: asks for the ECB rate of
``EUR -> quote`` on the fill's Madrid date, keeps final rates in a per-day
cache, and turns every lookup failure into ``None`` so the caller stores the
trade pending (change ``trade-eur-values`` D-1, D-2).

Reference: ADR-016 item 5.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Final

import structlog

from hermes.domain.errors import InfraError
from hermes.domain.fx.eur_pricing import EurPricing, rate_is_final
from hermes.domain.value_objects.currency import Currency
from hermes.ports.fx import FxRateProviderPort

__all__ = ["DEFAULT_EUR_SOURCE", "MAX_CACHED_RATE_AGE_DAYS", "EurPricer"]

DEFAULT_EUR_SOURCE: Final[str] = "ecb:frankfurter"

MAX_CACHED_RATE_AGE_DAYS: Final[int] = 7
"""Oldest rate, in days from the asked date, that :meth:`EurPricer.cached_rate` serves."""

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
        self._latest: dict[Currency, EurPricing] = {}

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
        self._latest[quote] = pricing
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

    def cached_rate(self, quote: Currency, on_date: date) -> Decimal | None:
        """Return an already-known rate of ``quote`` per euro, never fetching.

        Serves the notification path, which must not wait on a rate source
        (change ``668-eur-prices-in-pushes`` D-1). The final rate cached for
        ``on_date`` wins; otherwise the most recent rate fetched for ``quote``,
        final or provisional, is used while its date is within
        :data:`MAX_CACHED_RATE_AGE_DAYS` of ``on_date``.

        Args:
            quote: The pair's quote currency.
            on_date: Madrid calendar date the rate is wanted for.

        Returns:
            Units of ``quote`` per one EUR, or ``None`` when no recent enough
            rate is held. Never calls the provider and never raises.
        """
        if quote == Currency.EUR:
            return Decimal(1)
        exact = self._cache.get((quote, on_date))
        if exact is not None:
            return exact.rate
        latest = self._latest.get(quote)
        if latest is None or abs((on_date - latest.rate_date).days) > MAX_CACHED_RATE_AGE_DAYS:
            return None
        return latest.rate
