"""Periodic depth sampler that records each pair's spread and 1-hour median (#835).

Serve reads a depth book only when it executes a signal, a few times a week,
so nothing in the loop sees enough books for RO-02's rolling 1-hour median.
This sampler reads one public depth book per pair every
:data:`SPREAD_SAMPLE_SECONDS` through the same snapshot adapter (and so the
same token bucket) the shadow executor uses, and records the spread with the
median of the pair's samples in the hour before it.

Rate budget: a depth request of up to 100 levels weighs 5, so three pairs
every 5 minutes cost 15 weight per 5 minutes, 3 per minute on average: 0.5
percent of the adapter's own 600-per-minute bucket and 0.05 percent of
Binance's 6000-per-minute IP limit. A 5-minute step still gives 12 samples per
hour, enough for a median that one odd book cannot move.

Off the critical path: the sampler runs as a sibling task of the bar loop. A
failed or slow sample is logged and skipped, never raised, so it can neither
stop serve nor hold back a bar. Record only: no rule reads the values.

Reference: GitHub issues #514, #835.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Final

import structlog

from hermes.domain.market_data.spread_sample import (
    SPREAD_MEDIAN_WINDOW,
    SpreadSample,
    median_spread,
)
from hermes.domain.trading.execution_quality import book_mid, book_spread
from hermes.domain.value_objects.pair import Pair
from hermes.ports.clock import ClockPort
from hermes.ports.orderbook_snapshot import OrderbookSnapshotPort
from hermes.ports.risk_inputs import RiskInputStorePort

__all__ = ["SPREAD_SAMPLE_SECONDS", "SPREAD_SAMPLE_TIMEOUT_SECONDS", "SpreadSampler"]

SPREAD_SAMPLE_SECONDS: Final[float] = 300.0
"""Seconds between two sampling rounds (one book per pair per round)."""

SPREAD_SAMPLE_TIMEOUT_SECONDS: Final[float] = 30.0
"""Longest one pair's sample may take before it is abandoned and skipped."""

_log = structlog.get_logger(__name__)


class SpreadSampler:
    """Read one book per pair on a fixed step and record its spread."""

    def __init__(
        self,
        *,
        books: OrderbookSnapshotPort,
        store: RiskInputStorePort,
        clock: ClockPort,
        pairs: Sequence[Pair],
        interval_seconds: float = SPREAD_SAMPLE_SECONDS,
        timeout_seconds: float = SPREAD_SAMPLE_TIMEOUT_SECONDS,
    ) -> None:
        """Wire the sampler.

        Args:
            books: Depth source, shared with the shadow executor.
            store: Where the samples go.
            clock: Stamps when each sample was asked for.
            pairs: Pairs to sample, in this order every round.
            interval_seconds: Seconds between rounds.
            timeout_seconds: Longest one pair's sample may take.

        Raises:
            ValueError: A step or timeout that is not positive.
        """
        if interval_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("interval_seconds and timeout_seconds must be > 0")
        self._books = books
        self._store = store
        self._clock = clock
        self._pairs = tuple(pairs)
        self._interval = interval_seconds
        self._timeout = timeout_seconds

    async def run(self, shutdown: asyncio.Event) -> None:
        """Sample every ``interval_seconds`` until ``shutdown`` is set.

        The first round runs one step after start, so a boot's catch-up keeps
        the depth budget to itself. Shutdown also cuts a round in flight, so
        it never waits on a slow book.

        Args:
            shutdown: Ends the loop.
        """
        while not shutdown.is_set():
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=self._interval)
            except TimeoutError:
                await self._round_or_stop(shutdown)

    async def _round_or_stop(self, shutdown: asyncio.Event) -> None:
        """Run one round, cancelling it if ``shutdown`` arrives first."""
        round_ = asyncio.ensure_future(self.sample_once())
        stop = asyncio.ensure_future(shutdown.wait())
        try:
            await asyncio.wait({round_, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (round_, stop):
                if not task.done():
                    task.cancel()
            await asyncio.gather(round_, stop, return_exceptions=True)

    async def sample_once(self) -> int:
        """Sample every pair once; a failed pair is logged and skipped.

        Returns:
            How many new samples were stored.
        """
        written = 0
        for pair in self._pairs:
            try:
                async with asyncio.timeout(self._timeout):
                    written += await self._sample(pair)
            except Exception as exc:
                _log.warning(
                    "serve.spread_sample.failed",
                    pair=str(pair),
                    error=type(exc).__name__,
                    detail=str(exc),
                )
        return written

    async def _sample(self, pair: Pair) -> int:
        """Read, measure and store one book of ``pair``; return 1 when stored."""
        sampled_at = self._clock.now()
        book = await self._books.snapshot(pair)
        if book_mid(book) is None:
            _log.warning("serve.spread_sample.one_sided_book", pair=str(pair))
            return 0
        spread = book_spread(book)
        earlier = self._store.spread_samples(
            pair, book.snapshot_time - SPREAD_MEDIAN_WINDOW, book.snapshot_time
        )
        window = [s.spread for s in earlier]
        window.append(spread)
        median = median_spread(window)
        if median is None:  # pragma: no cover - the window always holds this sample
            return 0
        stored = self._store.record_spread_sample(
            SpreadSample(
                pair=pair,
                sampled_at=sampled_at,
                book_time=book.snapshot_time,
                spread=spread,
                median_spread_1h=median,
                samples_1h=len(window),
            )
        )
        return int(stored)
