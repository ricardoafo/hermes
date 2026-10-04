"""``hermes observe``: the 90-day observation window panel.

Invariants:

- it is a console command, so ``--demo`` reaches it;
- without a demo there is no live data yet: a note, exit 0;
- ``--json`` prints parseable JSON; ``--weekly`` prints the blind weekly text;
- bad flags, ``--json`` with ``--weekly`` and an unknown scenario exit 2.
"""

from __future__ import annotations

import io
import json

import pytest

from hermes.entrypoints import cli as cli_module
from hermes.entrypoints.commands.observe import COMMAND, run_observe
from hermes.entrypoints.console import DEMO_ENV_VAR

_NOTE = "The 90-day window starts with the first shadow fill (#353)."


def _run(args: list[str]) -> tuple[int, str]:
    stream = io.StringIO()
    return cli_module.main(args, stream=stream), stream.getvalue()


def test_observe_is_a_console_command() -> None:
    """The panel takes the demo scenario like every console panel (#525)."""
    assert (COMMAND.name, COMMAND.wants_demo) == ("observe", True)


def test_demo_panel_shows_the_day_of_the_window() -> None:
    """The leading --demo scenario is the one the panel reads."""
    rc, out = _run(["--demo", "ok-two-positions", "observe"])
    assert (rc, "DEMO DATA" in out, "day 12 of 90" in out) == (0, True, True)


def test_env_var_feeds_the_panel() -> None:
    """HERMES_CONSOLE_DEMO selects the scenario when no flag is given."""
    stream = io.StringIO()
    rc = run_observe([], stream, env={DEMO_ENV_VAR: "losing-year"})
    assert (rc, "day 40 of 90" in stream.getvalue()) == (0, True)


def test_without_demo_prints_the_no_data_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """No live read model yet: a note that the window starts with #353."""
    monkeypatch.delenv(DEMO_ENV_VAR, raising=False)
    assert _run(["observe"]) == (0, f"No observation data yet.\n{_NOTE}\n")


def test_without_demo_json_is_null_with_the_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """The JSON no-data state is a null observation and the same note."""
    monkeypatch.delenv(DEMO_ENV_VAR, raising=False)
    rc, out = _run(["observe", "--json"])
    assert (rc, json.loads(out)) == (0, {"observation": None, "note": _NOTE})


def test_json_carries_the_drift_verdict() -> None:
    """--json prints the verdict the panel shows."""
    rc, out = _run(["--demo", "late", "observe", "--json"])
    assert (rc, json.loads(out)["drift"]["verdict"]) == (0, "at risk")


def test_operator_json_carries_pnl() -> None:
    """The CLI is an operator surface, so its JSON has the P&L block."""
    rc, out = _run(["--demo", "ok-two-positions", "observe", "--json"])
    assert (rc, json.loads(out)["pnl"]) == (0, {"net_eur": "12.47", "sharpe": "0.82"})


def test_weekly_prints_the_blind_report() -> None:
    """--weekly prints the pushed text, which has no money even on the CLI."""
    rc, out = _run(["--demo", "ok-two-positions", "observe", "--weekly"])
    assert (rc, out.splitlines()[1], "12.47" in out, "€" in out) == (
        0,
        "Hermes weekly · week ending Sat 03 Oct 2026",
        False,
        False,
    )


@pytest.mark.parametrize(
    "args",
    [["--bogus"], ["--json", "--weekly"], ["--json", "--json"], ["ETH"]],
    ids=["unknown", "json-and-weekly", "repeated", "positional"],
)
def test_bad_arguments_exit_2(args: list[str]) -> None:
    """Anything but --json, --weekly or --utc, once each, is a usage error."""
    stream = io.StringIO()
    rc = run_observe(args, stream, demo_flag="ok-two-positions")
    assert (rc, stream.getvalue().startswith("ERROR: bad arguments.")) == (2, True)


def test_unknown_scenario_exits_2() -> None:
    """An unknown scenario names the valid ones and exits 2."""
    rc, out = _run(["--demo", "nope", "observe"])
    assert (rc, "ok-two-positions" in out) == (2, True)


def test_help_prints_usage() -> None:
    """--help prints the usage line and exits 0."""
    stream = io.StringIO()
    rc = run_observe(["--help"], stream)
    assert (rc, stream.getvalue()) == (
        0,
        "Usage: hermes [--demo SCENARIO] observe [--json | --weekly] [--utc]\n",
    )
