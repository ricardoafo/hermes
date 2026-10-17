"""Record the 30-day pair correlations once per closed 1D bar (#835).

Reads the closed 1D bars serve stored in ``live_bars`` and records, for every
two followed pairs, Pearson's correlation of their daily close-to-close
returns over the window that ends at the newest bar (pre-registration §5.5,
definition in :mod:`hermes.domain.portfolio.correlation`).

All pairs' 1D bars close at the same instant but reach the loop one by one,
so a day's correlations are computed when the last pair's bar of that day is
stored; an earlier call sees a pair without that bar and records nothing.
Recording is keyed on the bar's close time, so a replay writes nothing.

Bars handled before the ledger existed are not in ``live_bars``, so a fresh
ledger covers fewer than 30 returns at first; each row records its count.
Record only: RO-01 computes the same coefficients in process from the 1D
closes (:class:`~hermes.domain.portfolio.correlation.DailyCloses`, #514), so
these rows are for review.

Reference: GitHub issues #514, #835.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import combinations

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.correlation import (
    CORRELATION_WINDOW_DAYS,
    PairCorrelation,
    aligned_daily_returns,
    pearson,
)
from hermes.domain.value_objects.pair import Pair
from hermes.ports.live_bars import LiveBarStorePort
from hermes.ports.risk_inputs import RiskInputStorePort

__all__ = ["CorrelationRecorder"]

_ONE_DAY = timedelta(days=1)
_MIN_PAIRS = 2


class CorrelationRecorder:
    """Compute and store pair correlations from the stored 1D bars."""

    def __init__(
        self,
        *,
        bars: LiveBarStorePort,
        store: RiskInputStorePort,
        pairs: Sequence[Pair],
        window_days: int = CORRELATION_WINDOW_DAYS,
    ) -> None:
        """Wire the recorder.

        Args:
            bars: The stored live bars.
            store: Where the correlations go.
            pairs: Followed pairs; every two of them get a value.
            window_days: Daily returns per window.

        Raises:
            ValueError: ``window_days`` is below 1.
        """
        if window_days < 1:
            raise ValueError(f"window_days must be >= 1, got {window_days}")
        self._bars = bars
        self._store = store
        self._pairs = tuple(sorted(set(pairs), key=str))
        self._window = window_days

    def record_for(self, bar: Bar) -> int:
        """Record the correlations of the day ``bar`` closes, once all pairs have it.

        Args:
            bar: A bar serve just stored; anything but a 1D bar is ignored.

        Returns:
            How many new correlations were stored.

        Raises:
            hermes.domain.errors.InfraError: On a storage failure.
        """
        if bar.interval is not KlineInterval.D1 or len(self._pairs) < _MIN_PAIRS:
            return 0
        start = bar.open_time - _ONE_DAY * self._window
        end = bar.open_time + _ONE_DAY
        closes: dict[Pair, dict[datetime, Decimal]] = {}
        for pair in self._pairs:
            series = self._bars.live_bars(pair, KlineInterval.D1, start, end)
            if not series or series[-1].open_time != bar.open_time:
                return 0
            closes[pair] = {b.open_time: b.close for b in series}
        written = 0
        for pair_a, pair_b in combinations(self._pairs, 2):
            returns_a, returns_b = aligned_daily_returns(
                closes[pair_a], closes[pair_b], window_days=self._window
            )
            value = pearson(returns_a, returns_b)
            if value is None:
                continue
            written += self._store.record_pair_correlation(
                PairCorrelation(
                    pair_a=pair_a,
                    pair_b=pair_b,
                    window_days=self._window,
                    as_of=bar.close_time,
                    correlation=value,
                    sample_count=len(returns_a),
                )
            )
        return written
