"""Chaos: how Hermes fails when the klines endpoint is down as serve boots (issue #871).

Serve's first network read is the warm-up history. When the klines endpoint
refuses the connection, answers a 5xx error page, or answers an HTML page with
status 200 (a proxy or captive portal), the adapter's own retry policy runs
and then serve exits 1: it has no retry loop of its own. ``Restart=on-failure``
with ``RestartSec=30s`` in ``deploy/systemd/hermes.service`` boots it again.

What must hold:

- serve exits 1, never 0 (systemd would not restart it) and never 2 or 3
  (systemd would leave it stopped), after the adapter's retries only;
- the boot systemd makes once the outage is over trades as if nothing happened.
"""

import functools
from collections.abc import Callable
from datetime import timedelta
from unittest import mock

import httpx
import pytest
from structlog.testing import capture_logs

from tests.integration.scenarios import markets
from tests.integration.scenarios.ledger import outline
from tests.support.fake_market import harness
from tests.support.fake_market.exchange import FakeExchange

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

_KLINES = "/api/v3/klines"
_RESTART_SEC = timedelta(seconds=30)
"""``RestartSec`` in ``deploy/systemd/hermes.service``."""

type KlinesAnswer = Callable[[FakeExchange, dict[str, str]], httpx.Response]


def _refused(_exchange: FakeExchange, _params: dict[str, str]) -> httpx.Response:
    raise httpx.ConnectError("[Errno 111] Connection refused")


def _error_page(_exchange: FakeExchange, _params: dict[str, str]) -> httpx.Response:
    return httpx.Response(
        503,
        text="<html><body><h1>503 Service Temporarily Unavailable</h1></body></html>",
        headers={"content-type": "text/html"},
    )


def _html_with_200(_exchange: FakeExchange, _params: dict[str, str]) -> httpx.Response:
    return httpx.Response(
        200,
        text="<html><body>Please sign in to use this network</body></html>",
        headers={"content-type": "text/html"},
    )


_FAULTS: dict[str, tuple[KlinesAnswer, int]] = {
    "connection-refused": (_refused, 4),
    "503-error-page": (_error_page, 4),
    "200-html-page": (_html_with_200, 1),
}
"""Per fault: what klines answers, and the klines requests a boot makes by hand count.

A transport error or a 5xx is tried once and retried three times (ADR-003 §04);
a 200 whose body is not JSON is a permanent error and is not retried.
"""


@functools.cache
def _scenario() -> markets.Scenario:
    return markets.chandelier_exit()


@pytest.mark.parametrize("fault", sorted(_FAULTS))
def test_klines_down_at_boot_exits_one_after_the_adapter_retries_only(
    tmp_path_factory: pytest.TempPathFactory, fault: str
) -> None:
    """Serve exits 1, for systemd to restart, after the adapter's own tries and no more."""
    answer, klines_requests = _FAULTS[fault]
    scenario = _scenario()
    db = tmp_path_factory.mktemp(f"down-{fault}") / "ledger.sqlite3"
    with capture_logs(), mock.patch.object(FakeExchange, "_klines", answer):
        run = harness.serve(scenario.market, db, live_from=scenario.live_from)
    sent = [path for _host, path, _params in run.exchange.requests if path == _KLINES]
    assert (run.exit_code, len(sent)) == (1, klines_requests)


def test_the_boot_systemd_makes_after_a_klines_outage_trades_as_an_uninterrupted_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Klines answer 503 at boot; 30 s later the outage is over and the restart buys and sells."""
    scenario = _scenario()
    db = tmp_path_factory.mktemp("restart") / "ledger.sqlite3"
    boot = scenario.live_from
    outage = [(boot - timedelta(hours=1), boot + _RESTART_SEC)]
    with capture_logs():
        failed = harness.serve(scenario.market, db, live_from=boot, klines_outages=outage)
        restarted = harness.serve(scenario.market, db, live_from=boot + _RESTART_SEC)
    assert (failed.exit_code, restarted.exit_code, outline(db)["trades"]) == (
        1,
        0,
        [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
    )
