"""Serve comes back from downtime without back-dated fills (issue #1078, ADR-024 §7).

The rule, decided by Rick on 2026-10-17 at 22:54 UTC:

- A bar that closed while serve was down is recorded as missed. The strategy still
  reads it, so its decisions match the backtest's.
- An entry signal on a missed bar is not filled. New entries wait for a fresh bar.
- An exit signal on a missed bar fills at the price when serve is back, stamped
  with that time, never with the old bar's close.
- The first message after the restart names the downtime, the missed bars and
  the exits checked.

Each test stops serve on the chandelier scenario (breakout bar closing on
2024-10-29 at 04:00 UTC, exit bar on 2024-10-31 at 08:00 UTC), then boots it
again later on the same ledger. The simulated clock stops at the first sleep
after ``until``, so a first run stopped one 4h bar before a bar of interest has
handled that earlier bar and nothing after it.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.scenarios import markets
from tests.integration.scenarios.conftest import Run, RunScenario
from tests.integration.scenarios.ledger import signals, trades
from tests.support.serve_loop import events

pytestmark = pytest.mark.integration

_BREAKOUT_CLOSE = datetime(2024, 10, 29, 4, 0, tzinfo=UTC)
_EXIT_CLOSE = datetime(2024, 10, 31, 8, 0, tzinfo=UTC)
_RESTART_SOURCE = "serve.restart"
_ONE_BAR = timedelta(hours=4)


def _missed(run: Run) -> list[tuple[str, str]]:
    rows = [json.loads(r["payload_json"]) for r in events(run.db, "BarMissed")]
    return sorted((f"{p['pair']['base']}/{p['pair']['quote']}", p["interval"]) for p in rows)


def _restart_reports(run: Run) -> list[str]:
    """Reasons of the restart reports recorded; the same alert goes to the alert channels."""
    rows = [json.loads(r["payload_json"]) for r in events(run.db, "AnomalyDetected")]
    return [p["reason"] for p in rows if p["source"] == _RESTART_SOURCE]


def _halt_rules(run: Run) -> list[str]:
    return [json.loads(r["payload_json"])["rule"] for r in events(run.db, "RiskHaltRecorded")]


def test_an_entry_on_a_bar_that_closed_while_serve_was_down_is_not_filled(
    run_scenario: RunScenario,
) -> None:
    """The breakout bar closes during the outage: its entry is recorded, never bought.

    Before #1078 the catch-up filled it against the old bar's synthetic book or
    halted it as RO-04 (stale book), which also counted towards the kill inputs.
    """
    scenario = markets.chandelier_exit()
    first = run_scenario(scenario, until=_BREAKOUT_CLOSE - _ONE_BAR)
    assert trades(first.db) == []
    back = _BREAKOUT_CLOSE + timedelta(hours=5)
    second = run_scenario(scenario, live_from=back, until=back + timedelta(minutes=1))
    entries = [s for s in signals(second.db) if s.kind == "entry"]
    assert (
        [s.at for s in entries],
        trades(second.db),
        _halt_rules(second),
    ) == ([_BREAKOUT_CLOSE - timedelta(milliseconds=1)], [], [])


def test_an_exit_on_a_bar_that_closed_while_serve_was_down_fills_when_serve_is_back(
    run_scenario: RunScenario,
) -> None:
    """The exit bar closes during the outage: the SELL is stamped at the restart, not 08:00."""
    scenario = markets.chandelier_exit()
    first = run_scenario(scenario, until=_EXIT_CLOSE - _ONE_BAR)
    assert [t.side for t in trades(first.db)] == ["BUY"]
    back = _EXIT_CLOSE + timedelta(hours=2)
    second = run_scenario(scenario, live_from=back, until=back + timedelta(minutes=1))
    sells = [t for t in trades(second.db) if t.side == "SELL"]
    assert [t.at >= back for t in sells] == [True]


def test_bars_missed_over_a_day_are_recorded_per_stream_and_reported_once(
    run_scenario: RunScenario,
) -> None:
    """Down from 00:00 on 29 Oct to 12:00 on 30 Oct: 8 bars of 4h and 1 of 1d per pair.

    The 4h bars closing at 04:00, 08:00, 12:00, 16:00, 20:00 and 24:00 on the
    29th and at 04:00, 08:00 and 12:00 on the 30th are 9, but the 12:00 bar
    closes at the boot instant, inside the live window, so it is handled live.
    """
    scenario = markets.chandelier_exit()
    down = datetime(2024, 10, 29, 0, 0, tzinfo=UTC)
    back = datetime(2024, 10, 30, 12, 0, 30, tzinfo=UTC)
    run_scenario(scenario, until=down)
    second = run_scenario(scenario, live_from=back, until=back + timedelta(minutes=1))
    expected = sorted(
        [(pair, "4h") for pair in ("BTC/USDT", "ETH/USDT", "SOL/USDT") for _ in range(8)]
        + [(pair, "1d") for pair in ("BTC/USDT", "ETH/USDT", "SOL/USDT")]
    )
    reports = _restart_reports(second)
    assert (_missed(second), len(reports)) == (expected, 1)
    assert "missed 9 bars on BTC/USDT" in reports[0]


def test_a_restart_inside_the_live_window_misses_nothing(run_scenario: RunScenario) -> None:
    """Back two minutes after the breakout bar closed: the entry fills as in an unbroken run."""
    scenario = markets.chandelier_exit()
    first = run_scenario(scenario, until=_BREAKOUT_CLOSE - _ONE_BAR)
    assert trades(first.db) == []
    second = run_scenario(scenario, live_from=_BREAKOUT_CLOSE + timedelta(minutes=2))
    assert (
        _missed(second),
        _restart_reports(second),
        [t.side for t in trades(second.db)],
    ) == ([], [], ["BUY", "SELL"])
