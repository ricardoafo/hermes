"""Two lab shadows side by side, each on its own ledger (issue #1076, ADR-021 §2, ADR-024).

The lab on hermes-01 runs each shadow as its own ``hermes serve --strategy``
on its own ledger. These tests run the v1 and v2 strategies on the same
market and prove that neither can read or change the other's ledger or
capital: every row in a ledger carries its own strategy, and a shadow pointed
at another shadow's ledger is refused before it writes anything.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from hermes.entrypoints.serve import SHADOW_STRATEGY_NAME, V2_STRATEGY_NAME, StartupError
from tests.integration.scenarios import markets
from tests.integration.scenarios.ledger import trades
from tests.support.fake_market import harness

pytestmark = pytest.mark.integration


def _strategies_in(db: Path) -> set[str]:
    """Return every strategy named by a capital seed or an event row of the ledger."""
    with closing(sqlite3.connect(db)) as connection:
        seeds = {row[0] for row in connection.execute("SELECT strategy_id FROM capital_seeds")}
        owners = {
            row[0]
            for row in connection.execute(
                "SELECT json_extract(payload_json, '$.strategy_id.value') FROM events "
                "WHERE json_extract(payload_json, '$.strategy_id.value') IS NOT NULL"
            )
        }
    return seeds | owners


def _event_count(db: Path) -> int:
    with closing(sqlite3.connect(db)) as connection:
        (count,) = connection.execute("SELECT COUNT(*) FROM events").fetchone()
    return int(count)


def _run_both(tmp_path: Path) -> tuple[Path, Path]:
    """Run v1 and v2 on the chandelier market, each on its own new ledger."""
    scenario = markets.chandelier_exit()
    v1, v2 = tmp_path / "v1" / "hermes.sqlite3", tmp_path / "v2" / "hermes.sqlite3"
    for db, name in ((v1, SHADOW_STRATEGY_NAME), (v2, V2_STRATEGY_NAME)):
        db.parent.mkdir()
        harness.serve(scenario.market, db, live_from=scenario.live_from, strategy=name)
    return v1, v2


def test_each_shadow_ledger_holds_only_its_own_strategy(tmp_path: Path) -> None:
    """Both shadows trade the same breakout, yet each ledger names one strategy only."""
    v1, v2 = _run_both(tmp_path)

    assert (
        _strategies_in(v1),
        _strategies_in(v2),
        [t.side for t in trades(v1)],
        [t.side for t in trades(v2)],
    ) == (
        {SHADOW_STRATEGY_NAME},
        {V2_STRATEGY_NAME},
        ["BUY", "SELL"],
        ["BUY", "SELL"],
    )


def test_a_shadow_pointed_at_another_shadows_ledger_is_refused_before_writing(
    tmp_path: Path,
) -> None:
    """v2 started on v1's ledger stops at boot; v1's ledger keeps every row and only v1."""
    v1, _ = _run_both(tmp_path)
    before = _event_count(v1)
    scenario = markets.chandelier_exit()

    with pytest.raises(StartupError, match=f"holds the capital of {SHADOW_STRATEGY_NAME}"):
        harness.serve(scenario.market, v1, live_from=scenario.live_from, strategy=V2_STRATEGY_NAME)

    assert (_event_count(v1), _strategies_in(v1)) == (before, {SHADOW_STRATEGY_NAME})


def test_a_shadow_restarted_on_its_own_ledger_is_not_refused(tmp_path: Path) -> None:
    """The guard only stops a stranger: v1 boots again on its own ledger and exits cleanly."""
    v1, _ = _run_both(tmp_path)
    scenario = markets.chandelier_exit()

    again = harness.serve(
        scenario.market, v1, live_from=scenario.market.end, strategy=SHADOW_STRATEGY_NAME
    )

    assert (again.exit_code, _strategies_in(v1)) == (0, {SHADOW_STRATEGY_NAME})
