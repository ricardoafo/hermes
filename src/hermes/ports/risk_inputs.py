"""Risk input store port — recorded RO-01 and RO-02 inputs, read by no rule (#835).

The serve loop writes a spread sample per pair every few minutes and the
30-day pair correlations once per closed 1D bar; the reads exist for review.
No risk rule, strategy, sizing or kill check reads this store: RO-01 reads
the same coefficients computed in process from the 1D closes, as the backtest
does, and RO-02 stays off in shadow (issue #514). The concrete implementation is
:class:`hermes.adapters.ledger.sqlite_ledger.SqliteLedger`.

Reference: code-conventions.md §5 (Ports as Protocols), GitHub issue #835.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.market_data.spread_sample import SpreadSample
from hermes.domain.portfolio.correlation import PairCorrelation
from hermes.domain.value_objects.pair import Pair

__all__ = ["RiskInputStorePort"]


@runtime_checkable
class RiskInputStorePort(Protocol):
    """Contract for storing and reading the recorded risk inputs.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass) on any storage failure, and offer no update or delete.
    """

    def record_spread_sample(self, sample: SpreadSample) -> bool:
        """Store ``sample``; return ``False`` when its book is already recorded."""
        ...

    def spread_samples(
        self, pair: Pair, start: datetime, end: datetime
    ) -> tuple[SpreadSample, ...]:
        """Return the pair's samples with ``start <= book_time < end``, oldest first."""
        ...

    def record_pair_correlation(self, correlation: PairCorrelation) -> bool:
        """Store ``correlation``; return ``False`` when its key is already recorded."""
        ...

    def pair_correlations(self, start: datetime, end: datetime) -> tuple[PairCorrelation, ...]:
        """Return the correlations with ``start <= as_of < end``, oldest first."""
        ...
