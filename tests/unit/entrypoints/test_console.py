"""The ``--demo`` switch and ``HERMES_CONSOLE_DEMO``.

Invariants:

- ``hermes --demo <scenario>`` prints the overview with a DEMO DATA tag;
- an unknown or missing scenario is a usage error (exit 2);
- ``--demo`` never reaches a non-console command such as ``serve``;
- the environment variable only feeds console commands, and the flag wins;
- without a demo there is no live console source yet.
"""

from __future__ import annotations

import io

import pytest

from hermes.adapters.console import UnknownScenarioError
from hermes.entrypoints import cli as cli_module
from hermes.entrypoints.commands import Command
from hermes.entrypoints.commands.tui import run_tui
from hermes.entrypoints.console import (
    DEMO_ENV_VAR,
    DemoFlagError,
    LiveReadModelUnavailableError,
    resolve_read_model,
    split_demo_flag,
)


@pytest.mark.parametrize(
    ("args", "scenario", "rest"),
    [
        (["--demo", "late"], "late", []),
        (["--demo=late", "money"], "late", ["money"]),
        (["serve", "--demo", "late"], None, ["serve", "--demo", "late"]),
        ([], None, []),
    ],
)
def test_split_reads_only_a_leading_flag(
    args: list[str], scenario: str | None, rest: list[str]
) -> None:
    """Only a leading --demo is the global option; later ones belong to the command."""
    split = split_demo_flag(args)
    assert (split.scenario, split.rest) == (scenario, rest)


@pytest.mark.parametrize("args", [["--demo"], ["--demo="], ["--demo", "--help"]])
def test_split_requires_a_scenario_name(args: list[str]) -> None:
    """--demo without a name is an error, not a silent no-op."""
    with pytest.raises(DemoFlagError):
        split_demo_flag(args)


def test_demo_without_command_prints_tagged_overview() -> None:
    """Hermes --demo <scenario> prints the overview under a DEMO DATA tag."""
    stream = io.StringIO()
    rc = cli_module.main(["--demo", "ok-two-positions"], stream=stream)
    assert (rc, stream.getvalue().splitlines()[0].strip()) == (
        0,
        "DEMO DATA · scenario ok-two-positions",
    )


def test_unknown_demo_scenario_exits_2() -> None:
    """An unknown scenario is a usage error that lists the valid names."""
    stream = io.StringIO()
    rc = cli_module.main(["--demo", "nope"], stream=stream)
    assert (rc, "ok-two-positions" in stream.getvalue()) == (2, True)


def test_demo_flag_without_name_exits_2() -> None:
    """--demo alone is a usage error."""
    stream = io.StringIO()
    assert cli_module.main(["--demo"], stream=stream) == 2


def test_demo_flag_refuses_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    """A demo can never reach the bar loop: --demo with serve exits 2 before dispatch."""
    calls: list[list[str]] = []
    spy = Command("serve", "spy", lambda rest, *, stream: calls.append(rest) or 0)
    monkeypatch.setattr(cli_module, "discover", lambda: {"serve": spy})
    stream = io.StringIO()
    rc = cli_module.main(["--demo", "late", "serve"], stream=stream)
    assert (rc, calls) == (2, [])


def test_env_demo_does_not_touch_non_console_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stray HERMES_CONSOLE_DEMO on a host leaves `hermes serve --help` working."""
    monkeypatch.setenv(DEMO_ENV_VAR, "late")
    stream = io.StringIO()
    rc = cli_module.main(["serve", "--help"], stream=stream)
    assert (rc, stream.getvalue().startswith("Usage: hermes serve")) == (0, True)


def test_env_demo_does_not_replace_top_level_help(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare `hermes` still prints help when only the environment variable is set."""
    monkeypatch.setenv(DEMO_ENV_VAR, "late")
    stream = io.StringIO()
    cli_module.main([], stream=stream)
    assert "Usage: hermes [--demo SCENARIO] <command> [args]" in stream.getvalue()


def test_env_selects_the_scenario_for_console_commands() -> None:
    """HERMES_CONSOLE_DEMO picks the fixture when no flag is given."""
    model = resolve_read_model(demo_flag=None, env={DEMO_ENV_VAR: "losing-year"})
    assert model.demo_scenario == "losing-year"


def test_flag_wins_over_env() -> None:
    """The flag overrides the environment variable."""
    model = resolve_read_model(demo_flag="empty", env={DEMO_ENV_VAR: "losing-year"})
    assert model.demo_scenario == "empty"


def test_unknown_env_scenario_is_rejected() -> None:
    """A misspelt environment value fails loudly instead of showing real data."""
    with pytest.raises(UnknownScenarioError):
        resolve_read_model(demo_flag=None, env={DEMO_ENV_VAR: "lat"})


@pytest.mark.parametrize("env", [{}, {DEMO_ENV_VAR: "  "}])
def test_no_demo_means_no_console_source_yet(env: dict[str, str]) -> None:
    """Until the ledger read model lands, only demo data can be shown."""
    with pytest.raises(LiveReadModelUnavailableError, match="--demo"):
        resolve_read_model(demo_flag=None, env=env)


def test_tui_without_a_demo_says_there_is_no_live_source() -> None:
    """`hermes tui` with no scenario exits 2 and points at --demo instead of an empty screen."""
    stream = io.StringIO()
    rc = run_tui([], stream, env={}, runner=lambda model: None)
    assert (rc, "use --demo <scenario>" in stream.getvalue()) == (2, True)


def test_tui_rejects_extra_arguments() -> None:
    """`hermes tui` takes no arguments."""
    stream = io.StringIO()
    rc = run_tui(["--json"], stream, demo_flag="late", runner=lambda model: None)
    assert (rc, "Usage: hermes [--demo SCENARIO] tui" in stream.getvalue()) == (2, True)


def test_tui_runs_the_app_on_the_demo_read_model() -> None:
    """With a scenario the runner receives the fixture read model of that scenario."""
    seen: list[str | None] = []
    rc = run_tui(
        [],
        io.StringIO(),
        demo_flag=None,
        env={DEMO_ENV_VAR: "late"},
        runner=lambda model: seen.append(model.demo_scenario),
    )
    assert (rc, seen) == (0, ["late"])


def test_demo_flag_reaches_the_tui_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`hermes --demo X tui` is accepted as a console command and starts the app."""
    seen: list[str | None] = []
    monkeypatch.setattr(
        "hermes.entrypoints.tui.run_home", lambda model, **_: seen.append(model.demo_scenario)
    )
    rc = cli_module.main(["--demo", "ok-two-positions", "tui"], stream=io.StringIO())
    assert (rc, seen) == (0, ["ok-two-positions"])


def test_static_home_screen_has_no_tax_footnote_and_no_all_good_panel() -> None:
    """`hermes --demo X` is the quiet home screen: waterfall numbers only."""
    stream = io.StringIO()
    cli_module.main(["--demo", "ok-two-positions"], stream=stream)
    text = stream.getvalue()
    assert [w in text for w in ("FIFO", "All good", "Your move", "USDT")] == [False] * 4
