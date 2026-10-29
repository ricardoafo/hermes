"""FX rate provider port — abstract Protocol for official FX rate sources.

Concrete implementations live under :mod:`hermes.adapters.fx`. The first is
:mod:`hermes.adapters.fx.frankfurter` for the public Frankfurter API (an
ECB-reference-rate wrapper — see the decisions journal entry
``2026-08-23-fx-rate-source-frankfurter.md``).

The interface is deliberately narrow — two lookup methods, ``get_latest``
and ``get_for_date`` — because callers only need "give me the rate for
this day" semantics. Rate-limiting, HTTP transport, and error mapping are
adapter concerns hidden behind the port.

Reference: code-conventions.md §5 (Ports as Protocols).
"""

from datetime import date
from typing import Protocol, runtime_checkable

from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.value_objects.currency import Currency

__all__ = ["FxRateProviderPort"]


@runtime_checkable
class FxRateProviderPort(Protocol):
    """Contract every FX rate provider adapter satisfies.

    Adapters SHOULD:

    - honour any external-API rate limits internally (Frankfurter has no
      published cap but polite adapters throttle anyway);
    - convert wire-format numeric fields to :class:`~decimal.Decimal` at
      the port boundary so callers never see float precision loss;
    - resolve "closest prior business day" when the source publishes
      rates only on trading days (ECB / Frankfurter behaviour) rather
      than raising on weekend or holiday requests.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass — typically :class:`~hermes.domain.errors.FxRateError`) on
    any wire, HTTP-status, or schema failure. Callers in the application
    layer convert to :class:`returns.result.Result` per code-conventions
    §2.
    """

    def get_latest(self, from_currency: Currency, to_currency: Currency) -> FxRate:
        """Return the most recent official rate for the given pair.

        Args:
            from_currency: Source currency of the returned :class:`FxRate`.
            to_currency: Target currency of the returned :class:`FxRate`.
                Must differ from ``from_currency`` — same-currency lookups
                are meaningless and MAY raise
                :class:`~hermes.domain.errors.FxRateError`.

        Returns:
            An :class:`FxRate` for ``(from_currency, to_currency)`` stamped
            with the date the upstream source published it (usually the
            most recent trading day).

        Raises:
            hermes.domain.errors.InfraError: On any adapter-level failure.
        """
        ...

    def get_for_date(self, from_currency: Currency, to_currency: Currency, on: date) -> FxRate:
        """Return the official rate for the given calendar date (or closest prior).

        Args:
            from_currency: Source currency of the returned :class:`FxRate`.
            to_currency: Target currency of the returned :class:`FxRate`.
            on: Calendar date to look up. When the source publishes rates
                only on business days, the returned :class:`FxRate` MAY
                carry an earlier date than requested (the closest prior
                trading day) — callers who need strict-day semantics MUST
                verify ``result.date == on`` themselves.

        Returns:
            An :class:`FxRate` for ``(from_currency, to_currency)`` stamped
            with the date the upstream source resolved the lookup to.

        Raises:
            hermes.domain.errors.InfraError: On any adapter-level failure.
        """
        ...
