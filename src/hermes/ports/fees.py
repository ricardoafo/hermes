"""Fee-schedule provider port — abstract Protocol for per-pair fee lookup.

Concrete implementations live under :mod:`hermes.adapters.exchanges`.
The first is :mod:`hermes.adapters.exchanges.kraken_public` for Kraken
public endpoints; a Binance sibling can slot in the same way when the
commission-modeller feature needs cross-venue schedules.

The interface is deliberately narrow — one lookup that returns a
:class:`~hermes.domain.fees.FeeSchedule` — because callers only need
"give me the fee tiers for this pair" semantics. Rate-limiting,
caching, HTTP transport, and error mapping are adapter concerns hidden
behind the port.

Reference: code-conventions.md §5 (Ports as Protocols),
docs/decisions/2026-08-24-kraken-public-adapter.md.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hermes.domain.fees import FeeSchedule
from hermes.domain.value_objects.pair import Pair

__all__ = ["FeeScheduleProviderPort"]


@runtime_checkable
class FeeScheduleProviderPort(Protocol):
    """Contract every fee-schedule provider adapter satisfies.

    Adapters SHOULD:

    - honour any external-API rate limits internally (token bucket or
      equivalent);
    - cache schedules with a TTL appropriate to how often the venue
      changes them (Kraken: weeks-to-months, so a 24h in-memory cache
      is safe and cheap);
    - convert wire-format numeric fields to :class:`~decimal.Decimal`
      and integer bps at the port boundary so callers never see
      float precision loss or venue-specific representations.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass — typically the venue-specific ``*PublicError``) on any
    wire, HTTP-status, or schema failure. Callers in the application
    layer convert to :class:`returns.result.Result` per code-conventions
    §2.
    """

    def get_pair_fees(self, pair: Pair) -> FeeSchedule:
        """Return the volume-tiered fee schedule for ``pair``.

        Args:
            pair: Trading pair to look up.

        Returns:
            A :class:`FeeSchedule` whose ``tiers`` are normalised
            ascending by ``volume_threshold_usd``. The standard
            (zero-volume) tier is always ``tiers[0]``.

        Raises:
            hermes.domain.errors.InfraError: On any adapter-level failure.
        """
        ...
