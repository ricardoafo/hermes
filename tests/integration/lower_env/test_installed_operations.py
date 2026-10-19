"""The installed release survives a restart or a crash with a position open (issue #975, part 1).

Invariant: an installed ``hermes serve`` stopped a day after an entry, by
systemd's SIGTERM (every deploy) or by SIGKILL (a crash, the OOM killer, a
power cut), then started again on the same ledger without ``--new-ledger``,
ends with the same signals, trades and stops as one uninterrupted run: the
open position is rebuilt from the ledger, its exit fires once and nothing is
bought twice. The second run exits cleanly.

The uninterrupted reference is the in-process serve the scenario suite holds
to the pre-registration rules; ``test_installed_scenarios.py`` proves the
installed release matches it without a restart. The kill lands while serve
waits for the next bar; a kill inside a bar's writes is
``tests/integration/scenarios/test_chaos_crash_mid_bar.py``.
"""

import signal
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.integration.scenarios import markets
from tests.integration.scenarios.oracle import fingerprint
from tests.support.fake_market import harness
from tests.support.installed_release import SETTLE, serve

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(300)]


def _straight(tmp_path: Path, scenario: markets.Scenario) -> Path:
    db = tmp_path / "in-process.sqlite3"
    run = harness.serve(scenario.market, db, live_from=scenario.live_from)
    assert run.exit_code == 0
    return db


def _a_day_after_the_entry(scenario: markets.Scenario) -> datetime:
    """Return the instant serve handles the bar closing a day after the breakout bar."""
    return scenario.breakout_at + timedelta(days=1, milliseconds=1) + SETTLE


@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGKILL], ids=["restart", "crash"])
@pytest.mark.parametrize(
    "build",
    [markets.chandelier_exit, markets.regime_break],
    ids=["chandelier", "regime_break"],
)
def test_a_stop_with_a_position_open_changes_nothing_the_run_records(
    tmp_path: Path, build: Callable[[], markets.Scenario], stop: signal.Signals
) -> None:
    """Same signals, trades and stops as an uninterrupted run, and a clean second exit."""
    scenario = build()
    expected = fingerprint(_straight(tmp_path, scenario))
    stopped_at = _a_day_after_the_entry(scenario)

    first = serve(tmp_path, scenario.market, scenario.live_from, stopped_at, stop=stop)
    open_at_stop = [side for _, side, *_ in fingerprint(first.db)["trades"]]
    second = serve(
        tmp_path, scenario.market, stopped_at, scenario.market.end + SETTLE, restart=True
    )

    assert (first.exit_code, open_at_stop, second.exit_code, fingerprint(second.db)) == (
        0 if stop == signal.SIGTERM else -stop,
        ["BUY"],
        0,
        expected,
    ), second.log
