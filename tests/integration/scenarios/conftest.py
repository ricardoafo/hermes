"""Fixtures for the scenario suite: one serve run per call, with logs and a time budget."""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from hermes.application.order_router import router as router_module
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import serve as serve_module
from tests.integration.scenarios.markets import Scenario
from tests.support.fake_market import harness

BAR_BUDGET_SECONDS = 0.050
"""Median CPU time serve's event loop may spend on one bar.

Measured on 2026-10-05: 0.6 to 0.8 ms per scenario on a developer machine, and
5 to 22 ms on the loaded CI runners, where the same bars vary that much from
run to run while a fixed pure-Python workload only doubles. The cause of that
spread is not known. The budget sits above twice the slowest CI median, and
below ten times the fastest (5.3 ms), so a tenfold slowdown of the bar path
fails in PR CI before it reaches the deploy gate. On a developer machine,
where a bar takes under 1 ms, only about a seventyfold slowdown fails it. A
pure-Python reference workload was tried as the yardstick instead (issue
#788), but on CI the bars cost 4 to 12 times it.
"""


@dataclass
class Run:
    """One serve run of a scenario.

    Attributes:
        exit_code: Serve's exit code.
        db: The ledger file.
        notified: Every event handed to the Telegram stand-in.
        logs: Every structured log line serve emitted.
        bar_seconds: CPU time of the event loop's thread per handled bar.

    """

    exit_code: int
    db: Path
    notified: list[object]
    logs: list[dict[str, Any]]
    bar_seconds: list[float] = field(default_factory=list)

    def log_events(self, name: str) -> list[dict[str, Any]]:
        """Return the log lines whose event is ``name``, in order."""
        return [line for line in self.logs if line.get("event") == name]


type RunScenario = Callable[..., Run]


@pytest.fixture(autouse=True)
def _uncached_serve_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give serve and the order router loggers ``capture_logs`` can see.

    A test that configured structlog with ``cache_logger_on_first_use`` earlier
    in the session pins serve's module logger to that processor chain, and
    ``capture_logs`` then records nothing.
    """
    for module in (serve_module, router_module):
        monkeypatch.setattr(module, "_log", structlog.get_logger(module.__name__))
    cached = structlog.get_config()["cache_logger_on_first_use"]
    structlog.configure(cache_logger_on_first_use=False)
    yield
    structlog.configure(cache_logger_on_first_use=cached)


@pytest.fixture
def run_scenario(tmp_path: Path) -> Iterator[RunScenario]:
    """Run serve on a scenario; after the test, hold every run to the per-bar budget.

    Calls share one ledger per test, so a second call is a restart.
    """
    db = tmp_path / "ledger.sqlite3"
    runs: list[Run] = []

    def run(
        scenario: Scenario,
        *,
        live_from: datetime | None = None,
        until: datetime | None = None,
        book_outages: list[tuple[datetime, datetime]] | None = None,
        book_step_bps: Decimal = Decimal(1),
        late_bars: list[tuple[Pair, KlineInterval, datetime, datetime]] | None = None,
        corrupt_bars: list[tuple[Pair, KlineInterval, datetime, datetime, datetime]] | None = None,
        fx_down: bool = False,
        notifier_error: Exception | None = None,
    ) -> Run:
        with capture_logs() as logs:
            served = harness.serve(
                scenario.market,
                db,
                live_from=live_from or scenario.live_from,
                until=until,
                book_outages=book_outages,
                book_step_bps=book_step_bps,
                late_bars=late_bars,
                corrupt_bars=corrupt_bars,
                fx_down=fx_down,
                notifier_error=notifier_error,
            )
        outcome = Run(
            exit_code=served.exit_code,
            db=db,
            notified=list(served.channel.seen),
            logs=[dict(line) for line in logs],
            bar_seconds=served.bar_seconds,
        )
        runs.append(outcome)
        return outcome

    yield run
    seconds = [s for r in runs for s in r.bar_seconds]
    if seconds:
        median = statistics.median(seconds)
        assert median <= BAR_BUDGET_SECONDS, (
            f"serve took a median {median * 1000:.1f} ms per bar over {len(seconds)} bars; "
            f"the budget is {BAR_BUDGET_SECONDS * 1000:.0f} ms"
        )
