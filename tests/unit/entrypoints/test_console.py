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
    monkeypatch.setattr(
        cli_module, "_dispatch_serve", lambda rest, *, stream: calls.append(rest) or 0
    )
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
