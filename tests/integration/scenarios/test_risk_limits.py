"""Exposure cap and the risk overlay halts, through the real serve (§5.5, SA-9 RO-03, RO-07).

A refused entry leaves no trade. Serve logs which rule refused it; the
``serve.fill.declined`` line is the only record (issue #690 item 3).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.integration.scenarios import expected, markets
from tests.integration.scenarios.conftest import Run, RunScenario
from tests.integration.scenarios.ledger import outline, trades

pytestmark = pytest.mark.integration

_THIN_BOOK_BPS = Decimal(40)
"""Depth levels 40 bps apart: a $250 buy walks three levels, about 70 bps over the mid."""


def _declined(run: Run) -> list[tuple[str, str]]:
    return [(line["pair"], line["rule"]) for line in run.log_events("serve.fill.declined")]


def _halts(run: Run) -> list[str]:
    return [line["rule"] for line in run.log_events("risk_overlay_halt")]


def test_three_breakouts_fill_until_the_exposure_cap(run_scenario: RunScenario) -> None:
    """§5.5: BTC (now ~$280) and ETH ($250) fit under $750; SOL's $250 would not."""
    run = run_scenario(markets.three_breakouts())
    assert (outline(run.db), _declined(run)) == (
        {
            "signals": [
                ("BTC/USDT", "entry", ""),
                ("ETH/USDT", "entry", ""),
                ("SOL/USDT", "entry", ""),
            ],
            "trades": [("BTC/USDT", "BUY"), ("ETH/USDT", "BUY")],
        },
        [("SOL/USDT", "sizing")],
    )


def test_three_positions_one_per_pair_can_be_open_together(run_scenario: RunScenario) -> None:
    """§5.5: with BTC back at its entry price, the three $250 entries make exactly $750."""
    run = run_scenario(markets.three_breakouts(btc_climbs_to=Decimal(156)))
    assert (outline(run.db)["trades"], _declined(run)) == (
        [("BTC/USDT", "BUY"), ("ETH/USDT", "BUY"), ("SOL/USDT", "BUY")],
        [],
    )


def test_the_exposure_cap_is_checked_at_marked_value_not_cost(run_scenario: RunScenario) -> None:
    """At cost the three entries total exactly $750; marked to BTC's 175 they exceed it."""
    run = run_scenario(markets.three_breakouts())
    btc, eth = trades(run.db)
    at_cost = 3 * expected.POSITION_CAP
    marked = btc.qty * Decimal(175) + eth.qty * eth.price + expected.POSITION_CAP
    assert (at_cost <= expected.EXPOSURE_CAP, marked > expected.EXPOSURE_CAP) == (True, True)
    assert _declined(run) == [("SOL/USDT", "sizing")]


def test_a_fill_slipping_over_half_a_percent_halts_later_entries(
    run_scenario: RunScenario,
) -> None:
    """RO-03: after BTC fills ~70 bps over the mid, ETH and SOL later that day are refused."""
    run = run_scenario(markets.three_breakouts_within_a_day(), book_step_bps=_THIN_BOOK_BPS)
    buy = trades(run.db)[0]
    slippage = (buy.price - Decimal(156)) / Decimal(156)
    assert slippage > Decimal("0.005")
    assert (outline(run.db)["trades"], _declined(run), _halts(run)) == (
        [("BTC/USDT", "BUY")],
        [("ETH/USDT", "HALT"), ("SOL/USDT", "HALT")],
        ["RO-03", "RO-03"],
    )


# SEV-1: no rubric row matches; RO-03 must not halt entries for good after one slipping fill
def test_a_fill_slipping_over_half_a_percent_no_longer_halts_entries_four_days_later(
    run_scenario: RunScenario,
) -> None:
    """RO-03 (#857): four days after BTC's slip, ETH fills; SOL, one bar after ETH's slip, halts.

    Before the 24-hour window, BTC's slip halted both for good.
    """
    run = run_scenario(markets.three_breakouts(), book_step_bps=_THIN_BOOK_BPS)
    assert (outline(run.db)["trades"], _declined(run), _halts(run)) == (
        [("BTC/USDT", "BUY"), ("ETH/USDT", "BUY")],
        [("SOL/USDT", "HALT")],
        ["RO-03"],
    )


def test_a_btc_daily_drop_over_ten_percent_halts_entries_on_any_pair(
    run_scenario: RunScenario,
) -> None:
    """RO-07: the day after BTC falls 15%, the ETH breakout is refused."""
    run = run_scenario(markets.btc_daily_crash_then_eth_breakout())
    assert (outline(run.db), _declined(run), _halts(run)) == (
        {"signals": [("ETH/USDT", "entry", "")], "trades": []},
        [("ETH/USDT", "HALT")],
        ["RO-07"],
    )
