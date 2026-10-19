"""The installed release decides every engine-path scenario exactly as the in-process serve does.

Invariant (issue #974, part 1): on each scripted market of
``tests/integration/scenarios/markets.py``, an installed ``hermes serve`` (its
own process, ``HERMES_ENV=qa``, every endpoint on a fake over real HTTP, the
test clock stepped bar by bar) records the same signals, trades (times,
quantities, prices, fees) and stops as the in-process serve that the scenario
suite holds to the pre-registration rules, and exits cleanly on SIGTERM.

One definition proves both places: the suite's oracle checks the in-process
run against the rules, and this file proves the installed release is that run.
A mismatch is a defect in what ships (packaging, config loading, the qa
wiring or real HTTP timing), not in the strategy.

``time_stop`` is left out: its 93 scripted days are about 560 clock steps,
past what one CI test should take.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from tests.integration.scenarios import markets
from tests.integration.scenarios.oracle import fingerprint
from tests.support.fake_market import harness
from tests.support.installed_release import SETTLE, serve

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(300)]

_SCENARIOS: dict[str, Callable[[], markets.Scenario]] = {
    "breakout_twice_then_gap_down": markets.breakout_twice_then_gap_down,
    "btc_crash_with_entries_during_and_after_the_halt": (
        markets.btc_crash_with_entries_during_and_after_the_halt
    ),
    "btc_daily_crash_then_eth_breakout": markets.btc_daily_crash_then_eth_breakout,
    "chandelier_exit": markets.chandelier_exit,
    "entry_then_exit_then_two_breakouts": markets.entry_then_exit_then_two_breakouts,
    "momentum_gate_blocks": markets.momentum_gate_blocks,
    "regime_break": markets.regime_break,
    "regime_filter_blocks": markets.regime_filter_blocks,
    "thin_exit_bar": markets.thin_exit_bar,
    "three_breakouts": markets.three_breakouts,
    "three_breakouts_within_a_day": markets.three_breakouts_within_a_day,
}


def _in_process(tmp_path: Path, scenario: markets.Scenario) -> Path:
    db = tmp_path / "in-process.sqlite3"
    run = harness.serve(scenario.market, db, live_from=scenario.live_from)
    assert run.exit_code == 0
    return db


def _last_close_handled(scenario: markets.Scenario) -> datetime:
    """Return the instant serve handles the script's last bar."""
    return scenario.market.end + SETTLE


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_the_installed_release_records_what_the_in_process_run_records(
    tmp_path: Path, name: str
) -> None:
    """Same signals, trades and stops, and a clean exit, on the installed release."""
    scenario = _SCENARIOS[name]()
    expected = fingerprint(_in_process(tmp_path, scenario))
    installed = serve(tmp_path, scenario.market, scenario.live_from, _last_close_handled(scenario))

    assert (installed.exit_code, fingerprint(installed.db)) == (0, expected), installed.log
