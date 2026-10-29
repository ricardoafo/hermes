"""Clock-skew watch: alert when serve's clock leaves exchange time, and when it returns.

A host clock far from Binance's time breaks serve quietly: with the clock more
than five minutes ahead, every bar close looks older than the live-book window,
so each entry falls back to the bar-close book and RO-04 halts it as stale.
RO-04 stays as it is (the maintainer, 17 Oct 2026, issue #859 item 2, option A); the
skew itself becomes a P0 alert and turns ``hermes status`` red.

The bar feed already measures the skew at the midpoint of each ``/api/v3/time``
round trip. This module only decides when that measurement changes state: one
``CRITICAL`` anomaly when it crosses the limit, one ``INFO`` anomaly when it
comes back, and nothing while it stays on either side.
"""

from datetime import datetime, timedelta

from hermes.domain.events.anomaly_detected import (
    CLOCK_SKEW_SOURCE,
    AnomalyDetected,
    AnomalySeverity,
)

__all__ = ["clock_is_off", "clock_skew_change"]


def clock_is_off(latest: AnomalyDetected | None) -> bool:
    """Return whether the latest clock-skew anomaly left the clock off.

    Args:
        latest: The newest anomaly from :data:`CLOCK_SKEW_SOURCE`, or ``None``.

    Returns:
        ``True`` when that anomaly is the ``CRITICAL`` one, so no return to
        exchange time has been recorded since.
    """
    return latest is not None and latest.severity is AnomalySeverity.CRITICAL


def clock_skew_change(
    skew_ms: int | None,
    *,
    limit: timedelta,
    was_off: bool,
    now: datetime,
    correlation_id: str,
) -> AnomalyDetected | None:
    """Return the anomaly to record when the skew crosses ``limit``, else ``None``.

    Args:
        skew_ms: Local clock minus exchange clock in milliseconds, or ``None``
            before the feed has compared them.
        limit: The largest skew either way that still counts as on time.
        was_off: Whether the last recorded state is off (see
            :func:`clock_is_off`).
        now: When the change is recorded, UTC-aware.
        correlation_id: Correlation id of the anomaly.

    Returns:
        A ``CRITICAL`` anomaly when the clock just went off, an ``INFO`` one
        when it just came back, and ``None`` when nothing changed.
    """
    if skew_ms is None:
        return None
    off = abs(skew_ms) > limit // timedelta(milliseconds=1)
    if off == was_off:
        return None
    direction = "ahead of" if skew_ms > 0 else "behind"
    limit_ms = limit // timedelta(milliseconds=1)
    if off:
        severity = AnomalySeverity.CRITICAL
        reason = (
            f"the host clock is {abs(skew_ms)} ms {direction} Binance (limit {limit_ms} ms); "
            "while it is more than 5 minutes ahead, RO-04 halts every entry on a stale book"
        )
    else:
        severity = AnomalySeverity.INFO
        reason = f"the host clock is back within {limit_ms} ms of Binance ({skew_ms} ms)"
    return AnomalyDetected(
        occurred_at=now,
        correlation_id=correlation_id,
        source=CLOCK_SKEW_SOURCE,
        severity=severity,
        reason=reason,
    )
