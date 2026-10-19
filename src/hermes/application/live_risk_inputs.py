"""The live-phase risk and kill inputs serve feeds before real money (#1158).

Shadow keeps RO-02 (spread spike), RO-05 and RO-06 (depeg) and the §10
recovery kill on neutral inputs, as the backtest does (#514, the shadow set).
With ``allow_mainnet`` serve builds :class:`LedgerLiveRiskInputs` and hands it
to the executor, so each rule reads a real value:

- **RO-02**: the median of the pair's recorded spread samples (#835) in the
  hour before the order.
- **RO-05, RO-06**: the USDT mid on its USD book, polled every
  :data:`STABLE_PRICE_POLL_SECONDS` by :class:`StablePriceWatch`
  (pre-registration §5.5 sets a 60-second poll).
- **Recovery kill**: the days since the equity high, counted only once the
  drawdown since that high has reached §10's 8%: the kill is "more than 90
  days to recover from a drawdown of at least 8%", and the evaluator reads
  the days alone.

A missing input is never made up. No recent spread sample reads as a zero
median, which RO-02 halts on; no fresh stablecoin price reads as ``None``,
which the depeg rules skip, as they always have without a feed.

Reference: pre-registration v1 §5.5, §10; openspec/changes/1158-live-risk-inputs/change.md.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

import structlog

from hermes.application.risk_inputs.readings import RECOVERY_DRAWDOWN, recovery_reading
from hermes.domain.market_data.spread_sample import SPREAD_MEDIAN_WINDOW, median_spread
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.clock import ClockPort
from hermes.ports.portfolio_history import PortfolioHistoryPort
from hermes.ports.risk_inputs import RiskInputStorePort

__all__ = [
    "STABLE_PRICE_MAX_AGE",
    "STABLE_PRICE_POLL_SECONDS",
    "STABLE_PRICE_TIMEOUT_SECONDS",
    "LedgerLiveRiskInputs",
    "StablePriceWatch",
]

STABLE_PRICE_POLL_SECONDS: Final[float] = 60.0
"""Seconds between two stablecoin price polls (pre-registration §5.5)."""

STABLE_PRICE_TIMEOUT_SECONDS: Final[float] = 30.0
"""Longest one poll may take before it is abandoned and skipped."""

STABLE_PRICE_MAX_AGE: Final = timedelta(minutes=3)
"""Oldest reading the depeg rules may act on: three missed polls and it is stale."""

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_DAY: Final = timedelta(days=1)

_log = structlog.get_logger(__name__)


class StablePriceWatch:
    """Poll the stablecoin's USD price on a fixed step and keep the latest reading."""

    def __init__(
        self,
        *,
        fetch: Callable[[], Decimal],
        clock: ClockPort,
        interval_seconds: float = STABLE_PRICE_POLL_SECONDS,
        timeout_seconds: float = STABLE_PRICE_TIMEOUT_SECONDS,
        max_age: timedelta = STABLE_PRICE_MAX_AGE,
    ) -> None:
        """Wire the watch.

        Args:
            fetch: Blocking read of the USD price; run off the event loop.
            clock: Stamps each reading.
            interval_seconds: Seconds between polls.
            timeout_seconds: Longest one poll may take.
            max_age: Oldest reading :meth:`price` returns.

        Raises:
            ValueError: A step, timeout or age that is not positive.
        """
        if interval_seconds <= 0 or timeout_seconds <= 0 or max_age <= timedelta(0):
            raise ValueError("interval_seconds, timeout_seconds and max_age must be > 0")
        self._fetch = fetch
        self._clock = clock
        self._interval = interval_seconds
        self._timeout = timeout_seconds
        self._max_age = max_age
        self._latest: tuple[datetime, Decimal] | None = None

    def price(self, now: datetime) -> Decimal | None:
        """Return the latest reading, or ``None`` when none is younger than ``max_age``."""
        if self._latest is None:
            return None
        at, price = self._latest
        if now - at > self._max_age:
            return None
        return price

    async def run(self, shutdown: asyncio.Event) -> None:
        """Poll at once, then every ``interval_seconds`` until ``shutdown`` is set.

        The first poll runs at start: entries wait on no step for a price.

        Args:
            shutdown: Ends the loop.
        """
        while not shutdown.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=self._interval)
            except TimeoutError:
                continue

    async def poll_once(self) -> bool:
        """Read the price once; a failed read is logged and skipped.

        Returns:
            ``True`` when a reading was stored.
        """
        asked_at = self._clock.now()
        try:
            async with asyncio.timeout(self._timeout):
                price = await asyncio.to_thread(self._fetch)
        except Exception as exc:
            _log.warning("serve.stable_price.failed", error=type(exc).__name__, detail=str(exc))
            return False
        self._latest = (asked_at, price)
        return True


class LedgerLiveRiskInputs:
    """The live risk inputs, read from the ledger and the stablecoin watch.

    Satisfies :class:`~hermes.ports.live_risk_inputs.LiveRiskInputsPort`.
    """

    def __init__(
        self,
        *,
        spreads: RiskInputStorePort,
        marks: PortfolioHistoryPort,
        strategy_id: StrategyId,
        stable: StablePriceWatch,
    ) -> None:
        """Bind the stores.

        Args:
            spreads: The recorded spread samples.
            marks: The strategy's portfolio marks.
            strategy_id: The strategy whose equity high counts.
            stable: The stablecoin price watch.
        """
        self._spreads = spreads
        self._marks = marks
        self._strategy_id = strategy_id
        self._stable = stable

    def median_spread_1h(self, pair: Pair, now: datetime) -> Decimal | None:
        """Return the median spread of the pair's samples in the hour before ``now``."""
        samples = self._spreads.spread_samples(pair, now - SPREAD_MEDIAN_WINDOW, now)
        return median_spread([s.spread for s in samples])

    def stable_price(self, now: datetime) -> Decimal | None:
        """Return the stablecoin's fresh USD price, or ``None``."""
        return self._stable.price(now)

    def days_since_equity_high(self, now: datetime) -> float:
        """Return the days since the equity high once the drawdown since it reached 8%, else 0.0."""
        reading = recovery_reading(
            self._marks.portfolio_marks(self._strategy_id, _EPOCH, now), now=now
        )
        if reading is None or reading.deepest_drawdown < RECOVERY_DRAWDOWN:
            return 0.0
        return (now - reading.high_at) / _ONE_DAY
