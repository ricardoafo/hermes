"""Restart mid-position, for each exit type.

Serve is restarted by every deploy. The first run stops a day after the entry;
the second boots on the same ledger and must rebuild the open position from
it, so the exit still fires once and nothing is bought twice.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from hermes.domain.events.signal_emitted import SignalEmitted
from tests.integration.scenarios import markets
from tests.integration.scenarios.conftest import RunScenario
from tests.integration.scenarios.ledger import outline

pytestmark = pytest.mark.integration

_NINETY_DAYS = (pytest.mark.slow, pytest.mark.timeout(240))
"""Ninety days of bars on three pairs take about 12 s; a loaded runner needs more than 60."""


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
