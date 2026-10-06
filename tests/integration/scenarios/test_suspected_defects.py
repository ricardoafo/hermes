"""Suspected serve defects, pinned as strict expected failures (issue #690).

Each test states the behaviour the engine should have and fails today. When a
fix lands the test passes, ``strict=True`` turns that into a failure, and the
marker comes off in the fixing PR. No behaviour here changes without Rick's
decision; the fixes go to the Go-live thread.

Item 2 of issue #690 (a late bar dropped silently) is not a defect through the
real feed: see ``test_market_data_faults.py``, where a late bar either waits or
stops serve loudly and is recovered on restart.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.integration.scenarios import markets
from tests.integration.scenarios.conftest import RunScenario
from tests.integration.scenarios.ledger import outline
from tests.support.serve_loop import events

pytestmark = pytest.mark.integration


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="issue #690 item 1: an exit with no book is declined and never retried",
)
def test_an_exit_on_a_bar_with_no_volume_and_no_live_book_still_closes_the_position(
    run_scenario: RunScenario,
) -> None:
    """The position must not stay open when the exit cannot be priced on its bar.

    Today the strategy resets to flat when it signals the exit, the kernel
    declines the SELL for want of a book (no live depth, and the §6.2 book of a
    zero-volume bar is empty), and nothing retries: the coin is held with no
    exit rule watching it.
    """
    scenario = markets.thin_exit_bar()
    gap = next(b.close_time for b in scenario.market.h4[markets.BTC] if b.volume == 0)
    run = run_scenario(scenario, book_outages=[(gap, scenario.market.end)])
    assert outline(run.db)["trades"] == [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="issue #690 item 3: a refused entry leaves no record in the ledger",
)
def test_an_entry_refused_by_a_risk_halt_is_recorded_in_the_ledger(
    run_scenario: RunScenario,
) -> None:
    """A halted entry must leave a ``TradeRejected`` row, not only a log line.

    Today the RO-07 macro halt refuses the ETH entry, serve logs one line, and
    the entry signal is still sent to Telegram as if it had been taken.
    """
    run = run_scenario(markets.btc_daily_crash_then_eth_breakout())
    assert len(events(run.db, "TradeRejected")) == 1


def test_after_a_refused_entry_the_strategy_is_flat_again(run_scenario: RunScenario) -> None:
    """With the entry refused, no exit may be signalled for a position that does not exist.

    Today the strategy marks itself long when it signals the entry, whatever
    the kernel does with it. After the stale-book refusal it later signals a
    chandelier exit (declined: no position) and, until then, cannot enter BTC
    again however strong the breakout.
    """
    scenario = markets.chandelier_exit()
    breakout = scenario.breakout_at
    run = run_scenario(scenario, book_outages=[(breakout, breakout + timedelta(minutes=10))])
    assert outline(run.db)["signals"] == [("BTC/USDT", "entry", "")]
