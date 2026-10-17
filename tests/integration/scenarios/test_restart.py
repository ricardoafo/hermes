"""Restart mid-position, for each exit type, and restart during a risk halt.

Serve is restarted by every deploy. The first run stops a day after the entry;
the second boots on the same ledger and must rebuild the open position from
it, so the exit still fires once and nothing is bought twice.

A restart during an RO-03 or RO-07 halt (issue #873) must neither lift the
halt nor keep it past the point its own rule releases it. These tests pin
today's behaviour: RO-07 is worked out again from the BTC/USDT 1d bars of the
warm-up, and RO-03 reads the slippage of the last 24 hours of fills back from
the ledger (issue #857).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import timedelta
from decimal import Decimal

import pytest

from hermes.domain.events.signal_emitted import SignalEmitted
from tests.integration.scenarios import markets
from tests.integration.scenarios.conftest import Run, RunScenario
from tests.integration.scenarios.ledger import outline
from tests.support.serve_loop import events

pytestmark = pytest.mark.integration

_NINETY_DAYS = (pytest.mark.slow, pytest.mark.timeout(240))
"""Ninety days of bars on three pairs take about 12 s; a loaded runner needs more than 60."""

_THIN_BOOK_BPS = Decimal(40)
"""Depth levels 40 bps apart: a $250 buy walks three levels, about 70 bps over the mid."""

_RO03_SINGLE_FILL_LIMIT = Decimal("0.005")


def _declined(run: Run) -> list[tuple[str, str]]:
    return [(line["pair"], line["rule"]) for line in run.log_events("serve.fill.declined")]


def _halts(run: Run) -> list[str]:
    return [line["rule"] for line in run.log_events("risk_overlay_halt")]


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (markets.chandelier_exit, "chandelier"),
        pytest.param(markets.time_stop, "time_stop", marks=_NINETY_DAYS),
        (markets.regime_break, "regime_break"),
    ],
)
def test_a_restart_with_a_position_open_keeps_it_until_its_exit(
    run_scenario: RunScenario, build: Callable[[], markets.Scenario], reason: str
) -> None:
    """One BUY before the restart, one exit after it, each notified exactly once."""
    scenario = build()
    restart = scenario.breakout_at + timedelta(days=1)
    first = run_scenario(scenario, until=restart)
    second = run_scenario(scenario, live_from=restart)
    kinds = [
        [e.kind.value for e in run.notified if isinstance(e, SignalEmitted)]
        for run in (first, second)
    ]
    assert (outline(second.db), kinds) == (
        {
            "signals": [("BTC/USDT", "entry", ""), ("BTC/USDT", "exit", reason)],
            "trades": [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
        },
        [["entry"], ["exit"]],
    )


def test_a_restart_during_an_ro07_halt_keeps_it_until_btc_closes_a_steady_day(
    run_scenario: RunScenario,
) -> None:
    """RO-07: a halt in force at the restart refuses entries after it, and lifts by its own rule.

    The first run refuses ETH's 04:00 breakout the day after BTC's 15% drop
    and stops two hours later. The second run must still refuse SOL's 16:00
    breakout that day, then fill ETH's second breakout once BTC has closed a
    flat day: the restart neither lifts the halt nor holds it past its rule.
    """
    scenario = markets.btc_crash_with_entries_during_and_after_the_halt()
    restart = scenario.breakout_at + timedelta(hours=2)
    first = run_scenario(scenario, until=restart)
    second = run_scenario(scenario, live_from=restart)
    assert [(_declined(run), _halts(run)) for run in (first, second)] == [
        ([("ETH/USDT", "HALT")], ["RO-07"]),
        ([("SOL/USDT", "HALT")], ["RO-07"]),
    ]
    assert outline(second.db) == {
        "signals": [
            ("ETH/USDT", "entry", ""),
            ("SOL/USDT", "entry", ""),
            ("ETH/USDT", "entry", ""),
        ],
        "trades": [("ETH/USDT", "BUY")],
    }


def test_a_restart_during_an_ro03_halt_keeps_it_until_a_fill_slips_less(
    run_scenario: RunScenario,
) -> None:
    """RO-03: the slipped fill before a restart halts entries after it, until a later fill.

    The first run fills BTC on a thin book, about 70 bps over the mid, and
    stops. The second run has a deep book, so only the slippage the ledger
    recorded before the restart can refuse SOL's entry. BTC's exit then fills
    under the 0.5% limit and ETH's later entry fills: RO-03 lifts by its own
    rule, the latest fill's slippage, and the restart changes neither.
    """
    scenario = markets.entry_then_exit_then_two_breakouts()
    restart = scenario.breakout_at + timedelta(hours=10)
    first = run_scenario(scenario, until=restart, book_step_bps=_THIN_BOOK_BPS)
    second = run_scenario(scenario, live_from=restart)
    entry_slip, exit_slip, _ = (
        Decimal(json.loads(row["payload_json"])["slippage"])
        for row in events(second.db, "FillQualityRecorded")
    )
    assert (entry_slip > _RO03_SINGLE_FILL_LIMIT, exit_slip <= _RO03_SINGLE_FILL_LIMIT) == (
        True,
        True,
    )
    assert [(_declined(run), _halts(run)) for run in (first, second)] == [
        ([], []),
        ([("SOL/USDT", "HALT")], ["RO-03"]),
    ]
    assert outline(second.db) == {
        "signals": [
            ("BTC/USDT", "entry", ""),
            ("SOL/USDT", "entry", ""),
            ("BTC/USDT", "exit", "chandelier"),
            ("ETH/USDT", "entry", ""),
        ],
        "trades": [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL"), ("ETH/USDT", "BUY")],
    }


def _lose_the_signal_rows(run: Run) -> None:
    """Stand in for a trade that never had a signal: an old, manual or lost one."""
    with closing(sqlite3.connect(run.db)) as conn:
        conn.execute("DELETE FROM events WHERE event_type = 'SignalEmitted'")
        conn.commit()


def test_a_coin_held_without_its_entry_signal_is_watched_after_a_restart(
    run_scenario: RunScenario,
) -> None:
    """A held coin keeps its exit rule after a restart, and is never bought twice (#902)."""
    scenario = markets.breakout_twice_then_gap_down()
    restart = scenario.breakout_at + timedelta(days=1)
    _lose_the_signal_rows(run_scenario(scenario, until=restart))
    second = run_scenario(scenario, live_from=restart)
    assert outline(second.db)["trades"] == [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")]


def test_a_coin_held_without_its_entry_signal_is_logged_with_its_first_buy(
    run_scenario: RunScenario,
) -> None:
    """Serve warns which coin it restored from its fills, and from which buy (#902)."""
    scenario = markets.chandelier_exit()
    restart = scenario.breakout_at + timedelta(days=1)
    first = run_scenario(scenario, until=restart)
    _lose_the_signal_rows(first)
    second = run_scenario(scenario, live_from=restart)
    warned = [
        (line["pair"], line["entry_time"])
        for line in second.log_events("serve.position.no_entry_signal")
    ]
    assert warned == [("BTC/USDT", scenario.breakout_at.isoformat())]
