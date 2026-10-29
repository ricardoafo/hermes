"""Operations the installed release must ride out without changing a trade (issue #975).

Restart or crash with a position open (part 1). Invariant: an installed
``hermes serve`` stopped a day after an entry, by systemd's SIGTERM (every
deploy) or by SIGKILL (a crash, the OOM killer, a power cut), then started
again on the same ledger without ``--new-ledger``, ends with the same
signals, trades and stops as one uninterrupted run: the open position is
rebuilt from the ledger, its exit fires once and nothing is bought twice.
The second run exits cleanly.

The uninterrupted reference is the in-process serve the scenario suite holds
to the pre-registration rules; ``test_installed_scenarios.py`` proves the
installed release matches it without a restart. The kill lands while serve
waits for the next bar; a kill inside a bar's writes is
``tests/integration/scenarios/test_chaos_crash_mid_bar.py``.

Every alert sink down while a signal fires (part 2). Invariant: with
Telegram, Slack and Sentry all failing for the whole run, the installed
release still records the same signals, trades and stops as the in-process
run and exits cleanly. A dead sink costs the message, never the trade or the
run.

A second serve on the ledger in use (part 3). Invariant: a second installed
``hermes serve`` started on the ledger a running one holds, as a duplicate
unit or a hand-run command would, exits with the refusal code and names the
ledger, and the running serve records the same signals, trades and stops as
the in-process run. Two processes never write one ledger (issue #855).
"""

import signal
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hermes.entrypoints.commands.serve import EXIT_LEDGER_IN_USE
from tests.integration.scenarios import markets
from tests.integration.scenarios.oracle import fingerprint
from tests.support.fake_market import harness
from tests.support.fake_notify.server import Outage, Sink
from tests.support.installed_release import HERMES, SETTLE, serve
from tests.support.serve_loop import PROJECT_ROOT

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


_DOWN = (Sink.TELEGRAM, Sink.SLACK, Sink.SENTRY)


def test_every_alert_sink_down_through_a_trade_changes_nothing_the_run_records(
    tmp_path: Path,
) -> None:
    """Telegram, Slack and Sentry failing all run: same trades, a clean exit, nothing delivered."""
    scenario = markets.chandelier_exit()
    expected = fingerprint(_straight(tmp_path, scenario))

    run = serve(
        tmp_path,
        scenario.market,
        scenario.live_from,
        scenario.market.end + SETTLE,
        outages=dict.fromkeys(_DOWN, (Outage.SERVER_ERROR, 1_000)),
    )
    answered = {(call.sink, call.status // 100) for call in run.calls}

    assert (run.exit_code, fingerprint(run.db), answered) == (
        0,
        expected,
        {(sink, 5) for sink in _DOWN},
    ), run.log


def test_a_second_serve_on_the_ledger_in_use_is_refused_and_changes_nothing(
    tmp_path: Path,
) -> None:
    """The second serve exits with the refusal code; the first records what it would alone."""
    scenario = markets.chandelier_exit()
    expected = fingerprint(_straight(tmp_path, scenario))
    second: list[subprocess.CompletedProcess[str]] = []

    def start_a_second_serve(db: Path, env: dict[str, str]) -> None:
        second.append(
            subprocess.run(  # noqa: S603 - this checkout's own entry point
                [
                    str(HERMES),
                    "serve",
                    "--config-dir",
                    str(PROJECT_ROOT / "config"),
                    "--db",
                    str(db),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        )

    first = serve(
        tmp_path,
        scenario.market,
        scenario.live_from,
        scenario.market.end + SETTLE,
        alongside=start_a_second_serve,
    )
    [refused] = second

    assert (
        refused.returncode,
        f"another hermes serve is running on {first.db}" in refused.stderr,
        first.exit_code,
        fingerprint(first.db),
    ) == (EXIT_LEDGER_IN_USE, True, 0, expected), refused.stderr + first.log
