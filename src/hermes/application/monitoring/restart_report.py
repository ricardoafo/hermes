"""Restart report: what serve met when it came back from downtime (issue #1078).

After a restart, serve evaluates every bar that closed while it was down. Those
bars are missed: their entries are not filled, their exits fill at the price
when serve is back (ADR-024 §7). The operator gets one message saying so, built
here: how long serve was down, how many bars it missed per pair and stream, and
what happened to each exit it checked. No price, size or P&L goes in it
(ADR-012 D-02).
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["RESTART_SOURCE", "restart_report"]

RESTART_SOURCE = "serve.restart"
"""``AnomalyDetected.source`` of the restart report."""


def restart_report(
    *,
    stopped_at: datetime,
    now: datetime,
    missed: Mapping[tuple[Pair, KlineInterval], int],
    exits_checked: Sequence[str],
) -> str:
    """Return the restart report's text.

    Args:
        stopped_at: Close time of the last bar handled before the downtime.
        now: When the catch-up finished, UTC-aware.
        missed: Bars handled after their live window, per pair and interval.
        exits_checked: Outcome of each exit signal on a missed bar, as
            ``"<pair> <outcome>"``.

    Returns:
        One line per fact: the downtime, the missed bars per pair, the exits.
    """
    per_pair: dict[Pair, dict[KlineInterval, int]] = {}
    for (pair, interval), count in missed.items():
        per_pair.setdefault(pair, {})[interval] = count
    lines = [f"serve was down from {_utc(stopped_at)} to {_utc(now)} ({_span(now - stopped_at)})"]
    for pair in sorted(per_pair, key=str):
        streams = per_pair[pair]
        detail = ", ".join(
            f"{streams[interval]} x {interval.value}"
            for interval in sorted(streams, key=lambda i: i.value)
        )
        total = sum(streams.values())
        lines.append(f"missed {total} bar{'' if total == 1 else 's'} on {pair} ({detail})")
    if exits_checked:
        lines.append("exits checked: " + "; ".join(exits_checked))
    else:
        lines.append("exits checked: none due")
    return "\n".join(lines)


def _utc(at: datetime) -> str:
    return at.strftime("%Y-%m-%d %H:%M UTC")


def _span(span: timedelta) -> str:
    minutes = int(span.total_seconds()) // 60
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"
