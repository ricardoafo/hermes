"""Book outages and late bars, through the real serve and the real adapters.

Decided behaviour (2026-10-05): with a stale or missing book, new entries are
skipped and exits still fill. With the live depth endpoint down, serve falls
back to the pre-registration §6.2 book of the signal bar; that book is stamped
at the bar close, so RO-04 (stale book) refuses an entry, while an exit skips
the risk overlay and fills on it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from tests.integration.scenarios import expected, markets
from tests.integration.scenarios.conftest import Run, RunScenario
from tests.integration.scenarios.ledger import outline, signals, trades
from tests.support.serve_loop import events

pytestmark = pytest.mark.integration

_OUTAGE = timedelta(minutes=10)


def _first_close_at(scenario: markets.Scenario, close: Decimal) -> datetime:
    """Return the close time of the first BTC 4h bar that closes at ``close``."""
    return next(b.close_time for b in scenario.market.h4[markets.BTC] if b.close == close)


def _bar_after(scenario: markets.Scenario, close_time: datetime) -> Bar:
    """Return the BTC 4h bar right after the one closing at ``close_time``: mid-position."""
    return next(b for b in scenario.market.h4[markets.BTC] if b.open_time > close_time)


def _declined(run: Run) -> list[tuple[str, str, str]]:
    return [
        (line["kind"], line["rule"], line["book_source"])
        for line in run.log_events("serve.fill.declined")
    ]


def test_an_entry_while_the_depth_endpoint_is_down_is_skipped(
    run_scenario: RunScenario,
) -> None:
    """The breakout fires while depth answers 503: RO-04 refuses the entry on the §6.2 book."""
    scenario = markets.chandelier_exit()
    breakout = scenario.breakout_at
    run = run_scenario(scenario, book_outages=[(breakout, breakout + _OUTAGE)])
    assert (outline(run.db)["trades"], _declined(run)[0]) == ([], ("entry", "HALT", "synthetic"))


def test_an_exit_while_the_depth_endpoint_is_down_fills_on_the_model_book(
    run_scenario: RunScenario,
) -> None:
    """The gap bar fires the stop while depth answers 503: the SELL fills at stop - 15 bps."""
    scenario = markets.chandelier_exit()
    gap = _first_close_at(scenario, Decimal(120))
    run = run_scenario(scenario, book_outages=[(gap, gap + _OUTAGE)])
    stop = signals(run.db)[1].stop_price
    sell = trades(run.db)[1]
    assert (sell.side, sell.price) == ("SELL", stop * (1 - expected.STOP_SLIPPAGE))


def _btc_4h_handled(run: Run) -> list[datetime]:
    """Return the close time of every BTC 4h bar serve recorded as processed, in order."""
    payloads = [json.loads(row["payload_json"]) for row in events(run.db, "BarProcessed")]
    return [
        datetime.fromisoformat(p["occurred_at"])
        for p in payloads
        if p["interval"] == "4h" and p["pair"]["base"] == "BTC"
    ]


def test_a_bar_published_a_little_late_is_handled_in_order(run_scenario: RunScenario) -> None:
    """A 4h bar shown one hour after its close is waited for, then handled before the next."""
    scenario = markets.chandelier_exit()
    late = _bar_after(scenario, scenario.breakout_at)
    shown = late.close_time + timedelta(hours=1)
    run = run_scenario(scenario, late_bars=[(markets.BTC, KlineInterval.H4, late.open_time, shown)])
    assert (run.exit_code, outline(run.db)["trades"]) == (
        0,
        [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
    )
    assert _btc_4h_handled(run).count(late.close_time) == 1


def test_a_bar_published_after_the_next_one_stops_serve_and_a_restart_handles_it(
    run_scenario: RunScenario,
) -> None:
    """Issue #690 item 2: a bar skipped by the feed fails loudly; the next boot recovers it.

    The feed reports the hole and delivers the next bar, serve refuses it with a
    bar-gap error and exits 1 (systemd restarts it). The restart catches up
    through the late bar. No bar is handled out of order or dropped silently.
    """
    scenario = markets.chandelier_exit()
    late = _bar_after(scenario, scenario.breakout_at)
    shown = late.close_time + timedelta(hours=8)
    first = run_scenario(
        scenario, late_bars=[(markets.BTC, KlineInterval.H4, late.open_time, shown)]
    )
    failure = [repr(line.get("exc_info")) for line in first.log_events("serve.run.failed")]
    second = run_scenario(scenario, live_from=shown + timedelta(hours=1))
    assert (first.exit_code, "BarGapError" in failure[0], second.exit_code) == (1, True, 0)
    assert _btc_4h_handled(second).count(late.close_time) == 1


def test_a_closed_bar_with_a_nan_close_stops_serve_loudly_and_a_restart_handles_it(
    run_scenario: RunScenario,
) -> None:
    """Issue #841: a NaN close on a closed bar is a typed feed failure, never a raw decimal error.

    After the breakout entry, the next BTC 4h bar is served with a ``"NaN"``
    close for eight hours, then corrected. Serve refuses the broken row with
    ExchangeError and exits 1 (systemd restarts it), the same policy as a bar
    gap. The restart after the correction handles that bar once and the
    position still exits on its stop.
    """
    scenario = markets.chandelier_exit()
    broken = _bar_after(scenario, scenario.breakout_at)
    clean_from = broken.close_time + timedelta(hours=8)
    window = (markets.BTC, KlineInterval.H4, broken.open_time, broken.close_time, clean_from)
    first = run_scenario(scenario, corrupt_bars=[window])
    failure = [repr(line.get("exc_info")) for line in first.log_events("serve.run.failed")]
    second = run_scenario(scenario, live_from=clean_from + timedelta(hours=1))
    assert (first.exit_code, "not a finite number: 'NaN'" in failure[0], second.exit_code) == (
        1,
        True,
        0,
    )
    assert _btc_4h_handled(second).count(broken.close_time) == 1
    assert outline(second.db)["trades"] == [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")]


def test_a_nan_in_the_forming_candle_does_not_block_the_closed_bar_before_it(
    run_scenario: RunScenario,
) -> None:
    """The open candle is dropped unread by the closed-bar rule, so its values can't stop serve.

    The candle after the breakout carries a ``"NaN"`` close while it is still
    forming. The breakout bar closed before it and must be delivered and traded.
    """
    scenario = markets.chandelier_exit()
    forming = _bar_after(scenario, scenario.breakout_at)
    window = (
        markets.BTC,
        KlineInterval.H4,
        forming.open_time,
        forming.open_time,
        forming.close_time,
    )
    run = run_scenario(scenario, corrupt_bars=[window])
    assert (run.exit_code, outline(run.db)["trades"]) == (
        0,
        [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
    )
