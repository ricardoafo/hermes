"""Portfolio mark: the strategy's portfolio as it stood after one processed bar.

The serve loop stores one per handled bar (#387) so a separate process (the
console) can read value, cash versus invested and history without access to the
tracker's memory.

Reference: GitHub issue #387, openspec/archive/387-portfolio-marks/change.md.
"""

from dataclasses import dataclass
from datetime import datetime

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.value_objects.pair import Pair

__all__ = ["PortfolioMark"]


@dataclass(frozen=True, slots=True)
class PortfolioMark:
    """A portfolio snapshot tied to the bar that produced it.

    Args:
        snapshot: The tracker's snapshot after the bar was marked and evaluated.
        pair: Pair of the bar.
        interval: Interval of the bar.
        bar_close_time: Close time of the bar (UTC).
    """

    snapshot: PortfolioSnapshot
    pair: Pair
    interval: KlineInterval
    bar_close_time: datetime

    def __post_init__(self) -> None:
        """Require a timezone-aware close time."""
        if self.bar_close_time.tzinfo is None or self.bar_close_time.utcoffset() is None:
            raise ValueError("PortfolioMark.bar_close_time must be timezone-aware UTC")
