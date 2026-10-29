"""Each shadow spends its own missed-bar budget (issue #1117, ADR-021 §2 Done rule 2).

A shadow is Done only if at most 1% of its bars were missed over 90 days. The
chandelier scenario serves 3 pairs on 4h and 1d bars: 3 x 540 + 3 x 90 = 1890
bars in 90 days, so the budget is 18 bars. Each shadow keeps its own ledger,
so one shadow's outage never spends another's budget.

The simulated clock stops at the first sleep after ``until``; see
``test_downtime_recovery.py`` for how the outages below are placed.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.integration.scenarios import markets
from tests.integration.scenarios.conftest import Run, RunScenario
from tests.support.fake_market import harness
from tests.support.serve_loop import events

pytestmark = pytest.mark.integration

_BUDGET_SOURCE = "serve.missed_bar_budget"
_RESTART_SOURCE = "serve.restart"
_DOWN = datetime(2024, 10, 29, 0, 0, tzinfo=UTC)


def _reasons(db: Path, source: str) -> list[str]:
    rows = [json.loads(r["payload_json"]) for r in events(db, "AnomalyDetected")]
    return [p["reason"] for p in rows if p["source"] == source]


def _missed_rows(db: Path) -> int:
    return len(events(db, "BarMissed"))


def _restart(run_scenario: RunScenario, back: datetime) -> Run:
    return run_scenario(
        markets.chandelier_exit(), live_from=back, until=back + timedelta(minutes=1)
    )


def test_an_outage_spends_only_the_budget_of_the_shadow_that_was_down(
    run_scenario: RunScenario, tmp_path: Path
) -> None:
    """Down 00:00 to 12:00 on 29 Oct: 6 of 18 bars spent; the other shadow's ledger spends none.

    The 4h bars closing at 04:00 and 08:00 are missed on each of the 3 pairs;
    the 12:00 bar closes at the boot instant and is handled live.
    """
    run_scenario(markets.chandelier_exit(), until=_DOWN)
    down = _restart(run_scenario, datetime(2024, 10, 29, 12, 0, 30, tzinfo=UTC))
    other = tmp_path / "other" / "hermes.sqlite3"
    other.parent.mkdir()
    scenario = markets.chandelier_exit()
    harness.serve(scenario.market, other, live_from=scenario.live_from)

    reports = _reasons(down.db, _RESTART_SOURCE)
    assert (
        [r.splitlines()[-1] for r in reports],
        _reasons(down.db, _BUDGET_SOURCE),
        _missed_rows(other),
        _reasons(other, _RESTART_SOURCE),
    ) == (["missed-bar budget: 6 of 18 bars used, 12 left"], [], 0, [])


def test_crossing_the_budget_alerts_once_and_a_later_restart_does_not_repeat_it(
    run_scenario: RunScenario,
) -> None:
    """Down 00:00 on 29 Oct to 12:00 on 30 Oct misses 27 bars, past 18; the alert goes out once.

    A second outage, 16:00 on 30 Oct to 00:00 on 31 Oct, misses the 20:00 bar of
    each pair: 30 spent. Its restart report says so; the crossing alert is not
    sent again.
    """
    run_scenario(markets.chandelier_exit(), until=_DOWN)
    first = _restart(run_scenario, datetime(2024, 10, 30, 12, 0, 30, tzinfo=UTC))
    crossed = _reasons(first.db, _BUDGET_SOURCE)
    run_scenario(
        markets.chandelier_exit(),
        live_from=datetime(2024, 10, 30, 12, 1, tzinfo=UTC),
        until=datetime(2024, 10, 30, 16, 0, tzinfo=UTC),
    )
    second = _restart(run_scenario, datetime(2024, 10, 31, 0, 0, 30, tzinfo=UTC))

    assert (
        crossed,
        _reasons(second.db, _BUDGET_SOURCE),
        [r.splitlines()[-1] for r in _reasons(second.db, _RESTART_SOURCE)],
    ) == (
        [
            "missed-bar budget crossed: 27 bars missed, 18 allowed over 90 days "
            "(ADR-021 §2 Done rule 2); this run can no longer be Done"
        ],
        crossed,
        [
            "missed-bar budget: 27 of 18 bars used, 0 left",
            "missed-bar budget: 30 of 18 bars used, 0 left",
        ],
    )


def test_a_shadow_that_stays_up_through_a_klines_outage_alerts_when_it_crosses(
    tmp_path: Path,
) -> None:
    """Serve keeps running while klines answer 503 from 00:00 on 29 Oct to 12:00 on 30 Oct.

    No restart happens, so no restart report; the bars come in late once klines
    are back, and the crossing alert goes out from the live loop, once, on the
    19th missed bar: the first one past the budget of 18.
    """
    scenario = markets.chandelier_exit()
    db = tmp_path / "hermes.sqlite3"
    harness.serve(
        scenario.market,
        db,
        live_from=scenario.live_from,
        klines_outages=[(_DOWN, datetime(2024, 10, 30, 12, 0, 30, tzinfo=UTC))],
    )
    assert (_reasons(db, _RESTART_SOURCE), _reasons(db, _BUDGET_SOURCE)) == (
        [],
        [
            "missed-bar budget crossed: 19 bars missed, 18 allowed over 90 days "
            "(ADR-021 §2 Done rule 2); this run can no longer be Done"
        ],
    )
