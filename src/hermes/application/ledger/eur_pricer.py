"""EurPricer — looks up the EUR rate of a fill without ever failing it.

Wraps :class:`~hermes.ports.fx.FxRateProviderPort`: asks for the ECB rate of
``EUR -> quote`` on the fill's Madrid date, keeps final rates in a per-day
cache, and turns every lookup failure into ``None`` so the caller stores the
trade pending (change ``trade-eur-values`` D-1, D-2).

Reference: ADR-016 item 5.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Final

import structlog

from hermes.domain.errors import FxRateError, InfraError
from hermes.domain.fx.eur_pricing import EurPricing, rate_is_final
from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.value_objects.currency import Currency
from hermes.ports.fx import FxRateProviderPort

__all__ = ["DEFAULT_EUR_SOURCE", "MAX_CACHED_RATE_AGE_DAYS", "EurPricer", "LookupBudget"]

DEFAULT_EUR_SOURCE: Final[str] = "ecb:frankfurter"

MAX_CACHED_RATE_AGE_DAYS: Final[int] = 7
"""Oldest rate, in days from the asked date, that :meth:`EurPricer.cached_rate` serves."""

# Why 300: a (quote, date) the provider just failed on is not asked again for
# five minutes, so one unreachable rate costs one timeout instead of one per
# pending trade, yet a fill after the outage ends is still priced in-process.
FAILURE_RETRY_SEC: Final[float] = 300.0

_log = structlog.get_logger(__name__)


@dataclass
class LookupBudget:
    """Caps one batch of provider lookups: calls, wall time and an abort flag.

    Attributes:
        max_calls: Most provider calls the batch may make.
        max_seconds: Wall-clock allowance, counted from construction.
        stop: Set by the caller to abort the batch at the next lookup.
    """

    max_calls: int
    max_seconds: float
    monotonic: Callable[[], float] = time.monotonic
    stop: threading.Event = field(default_factory=threading.Event)
    _started: float = field(init=False)

    def __post_init__(self) -> None:
        """Start the wall clock."""
        self._started = self.monotonic()

    def exhausted(self, calls_made: int) -> bool:
        """Return whether the batch must make no further provider call."""
        return (
            self.stop.is_set()
            or calls_made >= self.max_calls
            or self.monotonic() - self._started >= self.max_seconds
        )


class EurPricer:
    """EUR rate lookups for the fill path and the backfill.

    Args:
        fx: Official rate provider. For USDT the composition root wires an
            adapter that reads USDT as USD (ADR-016 item 5).
        source: Name stored in ``trades.eur_source``.
    """

    def __init__(
        self,
        fx: FxRateProviderPort,
        *,
        source: str = DEFAULT_EUR_SOURCE,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind the provider and start empty caches."""
        self._fx = fx
        self._source = source
        self._monotonic = monotonic
        # The boot warm-up writes these from a worker thread while the loop may
        # read them. Each access is one dict get or set, which the GIL keeps
        # atomic; a stale read only costs an extra lookup, never a wrong rate.
        self._cache: dict[tuple[Currency, date], EurPricing] = {}
        self._latest: dict[Currency, EurPricing] = {}
        self._failed_until: dict[tuple[Currency, date], float] = {}
        self.provider_calls = 0

    def cached_only(self) -> EurPricer:
        """Return a view that shares the rate cache but never calls the provider."""
        view = EurPricer(_NoProvider(), source=self._source, monotonic=self._monotonic)
        view._cache = self._cache
        return view

    def warm(
        self,
        keys: Iterable[tuple[Currency, date]],
        *,
        today: date,
        budget: LookupBudget,
    ) -> int:
        """Look up each distinct ``(quote, date)`` once, within ``budget``.

        Blocking: meant to run off the event loop. Keys left over when the
        budget runs out stay uncached and their trades stay pending.

        Returns:
            How many keys were skipped because the budget ran out.
        """
        start = self.provider_calls
        pending = list(dict.fromkeys(keys))
        for index, (quote, fill_date) in enumerate(pending):
            if budget.exhausted(self.provider_calls - start):
                return len(pending) - index
            self.price(quote, fill_date, today=today)
        return 0

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
        if self._failed_until.get((quote, fill_date), 0.0) > self._monotonic():
            return None
        self.provider_calls += 1
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
            self._remember_failure(quote, fill_date)
            return None
        self._latest[quote] = pricing
        if not rate_is_final(fill_date=fill_date, rate_date=pricing.rate_date, today=today):
            _log.info(
                "eur_rate_not_final",
                quote=quote.value,
                fill_date=fill_date.isoformat(),
                rate_date=pricing.rate_date.isoformat(),
            )
            self._remember_failure(quote, fill_date)
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

    def _remember_failure(self, quote: Currency, fill_date: date) -> None:
        """Skip the provider for this key until ``FAILURE_RETRY_SEC`` has passed."""
        self._failed_until[(quote, fill_date)] = self._monotonic() + FAILURE_RETRY_SEC


class _NoProvider:
    """Provider of the cached-only view: every lookup is an outage."""

    def get_latest(self, from_currency: Currency, to_currency: Currency) -> FxRate:
        """Never reached: the view only reads the cache."""
        raise FxRateError("cached-only pricer has no provider")

    def get_for_date(self, from_currency: Currency, to_currency: Currency, on: date) -> FxRate:
        """Fail like an outage so the trade stays pending."""
        raise FxRateError("rate not looked up in this run")
