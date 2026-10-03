"""Bar schedule — pure time functions for closed-bar delivery.

Decides when a candle is closed and in which order closed candles are handed
to a consumer. Every function takes its time as an argument: the domain never
reads a clock (SA-8 §4.1 S-01), so a live feed, a replay and a test all drive
the same rules.

Rules (live-bar-stream change D-3 and D-5):

- A bar is closed when ``open_time + interval + settle`` is at or before the
  EARLIER of the local clock and the exchange clock. Skew between the two
  clocks can only delay a bar, never release a forming one.
- Closed bars are delivered by ``(close_time, larger interval first, pair)``.
  A 1d bar and the 4h bar that close at the same instant therefore arrive
  1d first, which is the order the swing strategy's buffers assume
  (``close_time <=`` the current bar's).
- ``close_time`` is Binance's inclusive value (``open + interval - 1 ms``),
  the value the Vision archives carry, so live and backtest bars compare
  equal.

Domain purity: stdlib and domain types only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "DeliveryPlan",
    "Stream",
    "close_time_of",
    "delivery_key",
    "interval_duration",
    "is_closed",
    "next_boundary",
    "plan_delivery",
    "previous_boundary",
    "within_catchup",
]

# One followed candle series: a pair at one interval.
Stream = tuple[Pair, KlineInterval]

_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MS: Final[timedelta] = timedelta(milliseconds=1)

# Kept in step with ``BarConsolidator``'s mapping by a drift-guard test
# (the domain may not import adapters).
_DURATIONS: Final[dict[KlineInterval, timedelta]] = {
    KlineInterval.M1: timedelta(minutes=1),
    KlineInterval.M5: timedelta(minutes=5),
    KlineInterval.H1: timedelta(hours=1),
    KlineInterval.H4: timedelta(hours=4),
    KlineInterval.D1: timedelta(days=1),
}


def _require_utc(value: datetime, *, name: str) -> None:
    """Reject naive or non-UTC datetimes at the function boundary.

    Raises:
        ValueError: ``value`` is naive or carries a non-UTC offset.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(None) != UTC.utcoffset(None):
        raise ValueError(f"{name} must be UTC-aware, got {value!r}")


def interval_duration(interval: KlineInterval) -> timedelta:
    """Return the length of one candle at ``interval``.

    Args:
        interval: Candle-duration bucket.

    Returns:
        The candle length as a :class:`~datetime.timedelta`.
    """
    return _DURATIONS[interval]


def close_time_of(open_time: datetime, interval: KlineInterval) -> datetime:
    """Return the inclusive ``close_time`` of the candle that opens at ``open_time``.

    Args:
        open_time: Candle open, UTC-aware.
        interval: Candle-duration bucket.

    Returns:
        ``open_time + interval - 1 ms``, the value Binance reports.

    Raises:
        ValueError: ``open_time`` is not UTC-aware.
    """
    _require_utc(open_time, name="open_time")
    return open_time + _DURATIONS[interval] - _ONE_MS


def next_boundary(now: datetime, interval: KlineInterval) -> datetime:
    """Return the first candle boundary strictly after ``now``.

    Boundaries are aligned to the UTC epoch, which is where Binance opens its
    candles for every interval hermes supports (each divides 24 hours).

    Args:
        now: Reference instant, UTC-aware.
        interval: Candle-duration bucket.

    Returns:
        The earliest boundary after ``now``.

    Raises:
        ValueError: ``now`` is not UTC-aware.
    """
    _require_utc(now, name="now")
    duration = _DURATIONS[interval]
    elapsed_windows = (now - _EPOCH) // duration
    return _EPOCH + (elapsed_windows + 1) * duration


def previous_boundary(now: datetime, interval: KlineInterval) -> datetime:
    """Return the latest candle boundary at or before ``now``.

    Args:
        now: Reference instant, UTC-aware.
        interval: Candle-duration bucket.

    Returns:
        The latest boundary that is not after ``now``.

    Raises:
        ValueError: ``now`` is not UTC-aware.
    """
    _require_utc(now, name="now")
    duration = _DURATIONS[interval]
    return _EPOCH + ((now - _EPOCH) // duration) * duration


def is_closed(
    open_time: datetime,
    interval: KlineInterval,
    *,
    local_now: datetime,
    exchange_now: datetime,
    settle: timedelta,
) -> bool:
    """Return whether the candle opening at ``open_time`` is closed and settled.

    The candle is closed when ``open_time + interval + settle`` is at or
    before the earlier of the two clocks.

    Args:
        open_time: Candle open, UTC-aware.
        interval: Candle-duration bucket.
        local_now: Local clock reading, UTC-aware.
        exchange_now: Exchange clock reading, UTC-aware.
        settle: Extra wait after the boundary so the exchange has finalised
            the candle.

    Returns:
        ``True`` when the candle may be treated as closed.

    Raises:
        ValueError: Any datetime is not UTC-aware.
    """
    _require_utc(open_time, name="open_time")
    _require_utc(local_now, name="local_now")
    _require_utc(exchange_now, name="exchange_now")
    return open_time + _DURATIONS[interval] + settle <= min(local_now, exchange_now)


def within_catchup(
    now: datetime, occurred_at: datetime, interval: KlineInterval, *, bars: int = 2
) -> bool:
    """Return whether a signal is recent enough to be worth notifying.

    A signal is recent when it is at most ``bars`` candles old. A future
    ``occurred_at`` (clock skew) is not recent: it cannot be a real bar close.

    Args:
        now: Reference instant, UTC-aware.
        occurred_at: Signal time (the bar close), UTC-aware.
        interval: Candle-duration bucket of the signal's stream.
        bars: How many candles old a signal may be. Defaults to 2.

    Returns:
        ``True`` exactly when ``0 <= now - occurred_at <= bars * interval``.

    Raises:
        ValueError: ``now`` or ``occurred_at`` is not UTC-aware.
    """
    _require_utc(now, name="now")
    _require_utc(occurred_at, name="occurred_at")
    age = now - occurred_at
    return timedelta(0) <= age <= bars * _DURATIONS[interval]


def delivery_key(bar: Bar) -> tuple[datetime, float, str]:
    """Return the sort key that gives the delivery order of closed bars.

    Order is ``(close_time, larger interval first, pair)``: equal close times
    put the longer candle first, then pairs sort by their ``BASE/QUOTE`` text.

    Args:
        bar: A closed bar.

    Returns:
        ``(close_time, -interval_seconds, pair)``.
    """
    return (
        bar.close_time,
        -_DURATIONS[bar.interval].total_seconds(),
        str(bar.pair),
    )


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    """Outcome of :func:`plan_delivery`.

    Attributes:
        bars: Bars to hand to the consumer now, in delivery order.
        cursors: Per-stream marker after those bars, the ``open_time`` of the
            newest bar delivered (unchanged for a stream that delivered
            nothing).
        gaps: ``(stream, open_time)`` for every bar the exchange skipped: the
            stream has later bars but none at ``open_time``.
    """

    bars: tuple[Bar, ...]
    cursors: Mapping[Stream, datetime]
    gaps: tuple[tuple[Stream, datetime], ...]


def plan_delivery(
    fetched: Mapping[Stream, Sequence[Bar]],
    cursors: Mapping[Stream, datetime],
) -> DeliveryPlan:
    """Choose which fetched closed bars may be delivered, and in what order.

    Per pair, candle boundaries are walked in ascending order. A boundary is
    delivered only once every followed interval that closes there has a bar
    (or is proven skipped by a later bar), so a 4h bar never precedes the 1d
    bar closing at the same instant. The first boundary that is not complete
    holds back itself and everything after it for that pair; the held bars
    are fetched again on the next poll because their cursor does not move.

    Args:
        fetched: Closed bars seen per stream. Bars at or before the stream's
            cursor are ignored, so overlapping polls cannot deliver a bar
            twice.
        cursors: Every followed stream, mapped to the ``open_time`` of the
            last bar already delivered. Its keys define which streams take
            part in the "complete boundary" check.

    Returns:
        The :class:`DeliveryPlan`, with ``bars`` sorted by :func:`delivery_key`.
    """
    advanced = dict(cursors)
    delivered: list[Bar] = []
    gaps: list[tuple[Stream, datetime]] = []
    for pair in sorted({stream[0] for stream in cursors}, key=str):
        streams = [stream for stream in cursors if stream[0] == pair]
        unseen = {
            stream: {
                bar.open_time: bar
                for bar in fetched.get(stream, ())
                if bar.open_time > cursors[stream]
            }
            for stream in streams
        }
        for boundary in _pending_boundaries(unseen, cursors):
            step = _complete_boundary(boundary, unseen, advanced)
            if step is None:
                break
            bars_at, gaps_at = step
            for bar in bars_at:
                advanced[(bar.pair, bar.interval)] = bar.open_time
            delivered.extend(bars_at)
            gaps.extend(gaps_at)
    delivered.sort(key=delivery_key)
    return DeliveryPlan(bars=tuple(delivered), cursors=advanced, gaps=tuple(gaps))


def _pending_boundaries(
    unseen: Mapping[Stream, Mapping[datetime, Bar]],
    cursors: Mapping[Stream, datetime],
) -> list[datetime]:
    """Return every close instant to examine, ascending.

    For each stream that has unseen bars the range runs from the first bar
    expected after its cursor to its newest bar, so a bar the exchange skipped
    still produces a boundary (and is then reported as a gap).
    """
    boundaries: set[datetime] = set()
    for stream, bars in unseen.items():
        if not bars:
            continue
        interval = stream[1]
        duration = _DURATIONS[interval]
        boundary = next_boundary(cursors[stream], interval) + duration
        newest = max(bars) + duration
        while boundary <= newest:
            boundaries.add(boundary)
            boundary += duration
    return sorted(boundaries)


def _complete_boundary(
    boundary: datetime,
    unseen: Mapping[Stream, Mapping[datetime, Bar]],
    advanced: Mapping[Stream, datetime],
) -> tuple[list[Bar], list[tuple[Stream, datetime]]] | None:
    """Return the bars and gaps at ``boundary``, or ``None`` if it is incomplete.

    Args:
        boundary: A close instant for at least one stream of the pair.
        unseen: The pair's not-yet-delivered bars keyed by ``open_time``, per stream.
        advanced: Delivery cursors so far, per stream.

    Returns:
        ``None`` when an interval that closes at ``boundary`` has no bar yet
        and no later bar either, otherwise the bars to deliver and the gaps.
    """
    bars_at: list[Bar] = []
    gaps_at: list[tuple[Stream, datetime]] = []
    for stream, bars in unseen.items():
        interval = stream[1]
        duration = _DURATIONS[interval]
        open_time = boundary - duration
        if previous_boundary(boundary, interval) != boundary or advanced[stream] >= open_time:
            continue
        bar = bars.get(open_time)
        if bar is not None:
            bars_at.append(bar)
        elif any(later > open_time for later in bars):
            gaps_at.append((stream, open_time))
        else:
            return None
    return bars_at, gaps_at
