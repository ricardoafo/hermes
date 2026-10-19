"""LiveTickerPort — the live public price the operator console shows between bars.

The engine acts only on closed bars. Between them, the console shows the
operator a live public price per coin (design spec "Live between bars", issue
#713). This port is the one call it needs: the current ticker of a pair.
``KrakenPublicAdapter.get_ticker`` satisfies it as it is; tests pass a fake.

Display only. Live prices exist in the console process alone: they are never
written to the ledger, never move a stop or an exit, and never reach the
supervisor, briefs, notifications or any AI. Import-linter contract
``live-prices-are-console-only`` keeps engine, supervisor and notification code
from importing this module.

Reference: openspec/archive/713-live-ticker/change.md (D-1, D-6).
"""

from typing import Protocol, runtime_checkable

from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.pair import Pair

__all__ = ["LiveTickerPort"]


@runtime_checkable
class LiveTickerPort(Protocol):
    """Read-only current ticker of a pair, for the console's display."""

    def get_ticker(self, pair: Pair) -> Ticker:
        """Return the current best bid, best ask and last trade of ``pair``.

        Blocking: the console calls it from a worker thread, never the UI thread.

        Raises:
            Exception: Any failure; the caller turns it into a failed tick.
        """
        ...
