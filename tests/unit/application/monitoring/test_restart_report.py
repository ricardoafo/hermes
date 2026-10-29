"""Tests for the restart report's text (issue #1078).

Invariants protected here: the report names the downtime, the missed bars per
pair with their streams, and every exit checked; it says so when no exit was
due; it never carries a price.
"""

from datetime import UTC, datetime

from hermes.application.monitoring.restart_report import restart_report
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_STOPPED = datetime(2024, 10, 29, 0, 0, tzinfo=UTC)
_NOW = datetime(2024, 10, 30, 12, 0, 30, tzinfo=UTC)


def _report(exits: list[str]) -> str:
    return restart_report(
        stopped_at=_STOPPED,
        now=_NOW,
        missed={
            (_BTC, KlineInterval.H4): 8,
            (_BTC, KlineInterval.D1): 1,
            (_ETH, KlineInterval.H4): 8,
        },
        exits_checked=exits,
    )


def test_the_report_names_the_downtime() -> None:
    """Invariant: the first line gives both ends of the outage and its length."""
    assert _report([]).splitlines()[0] == (
        "serve was down from 2024-10-29 00:00 UTC to 2024-10-30 12:00 UTC (36 h 0 min)"
    )


def test_the_report_counts_missed_bars_per_pair_and_stream() -> None:
    """Invariant: one line per pair, its total first, then each stream's count."""
    assert _report([]).splitlines()[1:3] == [
        "missed 9 bars on BTC/USDT (1 x 1d, 8 x 4h)",
        "missed 8 bars on ETH/USDT (8 x 4h)",
    ]


def test_the_report_lists_every_exit_checked() -> None:
    """Invariant: each exit on a missed bar is named with its outcome."""
    report = _report(["BTC/USDT filled", "ETH/USDT declined (RO-04)"])
    assert report.splitlines()[-1] == "exits checked: BTC/USDT filled; ETH/USDT declined (RO-04)"


def test_the_report_says_when_no_exit_was_due() -> None:
    """Invariant: an empty exit list is stated, not left out, so the reader knows it was checked."""
    assert _report([]).splitlines()[-1] == "exits checked: none due"
