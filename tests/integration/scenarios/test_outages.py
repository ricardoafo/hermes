"""FX and Telegram outages: trading carries on and nothing is lost.

The EUR values are measurement only (ADR-016): a Frankfurter outage stores the
trades pending and the next boot prices them. A Telegram outage is logged; the
fills are recorded all the same.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from hermes.domain.errors import InfraError
from tests.integration.scenarios import markets
from tests.integration.scenarios.conftest import RunScenario
from tests.integration.scenarios.ledger import outline, trades

pytestmark = pytest.mark.integration


def test_trades_made_while_fx_is_down_are_priced_in_euros_at_the_next_boot(
    run_scenario: RunScenario,
) -> None:
    """Both trades are stored pending while Frankfurter answers 503, then priced on reboot."""
    scenario = markets.chandelier_exit()
    down = run_scenario(scenario, fx_down=True)
    during = [t.eur_status for t in trades(down.db)]
    run_scenario(scenario, live_from=scenario.market.end + timedelta(hours=1))
    after = [t.eur_status for t in trades(down.db)]
    assert (during, after) == (["pending", "pending"], ["ok", "ok"])


def test_a_telegram_outage_does_not_stop_trading(run_scenario: RunScenario) -> None:
    """Every send fails, yet serve keeps running and records the BUY and the SELL."""
    run = run_scenario(markets.chandelier_exit(), notifier_error=InfraError("telegram unreachable"))
    assert (
        run.exit_code,
        outline(run.db)["trades"],
        len(run.log_events("serve.notify.failed")),
    ) == (
        0,
        [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
        2,
    )
