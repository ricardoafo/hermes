"""The ``--demo`` switch and ``HERMES_CONSOLE_DEMO``.

Invariants:

- ``hermes --demo <scenario>`` prints the overview with a DEMO DATA tag;
- an unknown or missing scenario is a usage error (exit 2);
- ``--demo`` never reaches a non-console command such as ``serve``;
- the environment variable only feeds console commands, and the flag wins;
- without a demo there is no live console source yet;
- the live ticker is the Kraken public adapter for a ledger-backed console and
  absent for demo data (issue #713);
- the Home exposure cap is the roster's pre-registered value, or ``None`` when
  the roster cannot say one cap (#712);
- the Shadow test (issue #718) reads the first enabled strategy's day-0
  record from the ``runs`` directory beside the ledger, and the notebook
  shipped with the release.
"""

from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from hermes.adapters.console import FixtureReadModel, UnknownScenarioError
from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.adapters.console.shadow_records import RunsDirectory
from hermes.adapters.exchanges.kraken_public import KrakenPublicAdapter
from hermes.application.console import ConsoleReadModel
from hermes.application.console.shadow_screen import ShadowSources
from hermes.entrypoints import cli as cli_module
from hermes.entrypoints.commands import Command
from hermes.entrypoints.commands.tui import live_ticker, run_tui
from hermes.entrypoints.console import (
    DEMO_ENV_VAR,
    DemoFlagError,
    LiveReadModelUnavailableError,
    lab_notebook_path,
    resolve_read_model,
    roster_exposure_cap,
    roster_strategy_id,
    shadow_sources,
    split_demo_flag,
)
from tests.support.paths import REPO_ROOT


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
    stream, err = io.StringIO(), io.StringIO()
    rc = cli_module.main(["--demo", "nope"], stream=stream, err=err)
    assert (rc, stream.getvalue(), "ok-two-positions" in err.getvalue()) == (2, "", True)


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
    stream, err = io.StringIO(), io.StringIO()
    rc = run_tui([], stream, env={}, runner=lambda model: None, err=err)
    assert (rc, stream.getvalue(), "use --demo <scenario>" in err.getvalue()) == (2, "", True)


def test_tui_rejects_extra_arguments() -> None:
    """`hermes tui` takes no arguments."""
    stream, err = io.StringIO(), io.StringIO()
    rc = run_tui(["--json"], stream, demo_flag="late", runner=lambda model: None, err=err)
    assert (rc, stream.getvalue(), "Usage: hermes [--demo SCENARIO] tui" in err.getvalue()) == (
        2,
        "",
        True,
    )


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


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_tui_help_prints_usage_and_exit_codes_without_starting_the_app(flag: str) -> None:
    """`tui -h/--help` prints usage and exit codes, exits 0, and never runs the app."""
    stream = io.StringIO()
    started: list[ConsoleReadModel] = []
    rc = run_tui([flag], stream, env={}, runner=started.append)
    out = stream.getvalue()
    assert (
        rc,
        started,
        out.startswith("Usage: hermes [--demo SCENARIO] tui"),
        "Exit codes: 0" in out,
    ) == (
        0,
        [],
        True,
        True,
    )


_STRATEGY = """
  - name: {name}
    enabled: true
    weight: "{weight}"
    capital_budget: "5000"
    quote: USDT
    sizing:
      risk_pct: "0.01"
      max_position_pct: "0.05"
      max_total_exposure_pct: "{cap}"
"""


def _roster(tmp_path: Path, *entries: tuple[str, str, str]) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    body = "strategies:" + "".join(
        _STRATEGY.format(name=name, weight=weight, cap=cap) for name, weight, cap in entries
    )
    (tmp_path / "strategies.yaml").write_text(body, encoding="utf-8")
    return {"HERMES_CONFIG_DIR": str(tmp_path)}


def test_exposure_cap_is_read_from_the_roster(tmp_path: Path) -> None:
    """A roster whose strategy caps exposure at 0.2 gives 0.2: the figure comes from the file."""
    env = _roster(tmp_path, ("swing", "1.0", "0.2"))
    assert roster_exposure_cap(env) == Decimal("0.2")


def test_exposure_cap_of_the_repository_roster() -> None:
    """The shipped roster (pre-registration §5.5) caps exposure at 15%."""
    repo_config = REPO_ROOT / "config"
    assert roster_exposure_cap({"HERMES_CONFIG_DIR": str(repo_config)}) == Decimal("0.15")


def test_exposure_cap_is_unknown_when_caps_differ(tmp_path: Path) -> None:
    """Two enabled strategies with different caps give no cap; with equal caps, that cap."""
    differ = _roster(tmp_path / "differ", ("a", "0.5", "0.15"), ("b", "0.5", "0.10"))
    agree = _roster(tmp_path / "agree", ("a", "0.5", "0.15"), ("b", "0.5", "0.15"))
    assert (roster_exposure_cap(differ), roster_exposure_cap(agree)) == (None, Decimal("0.15"))


def test_exposure_cap_is_unknown_without_a_roster(tmp_path: Path) -> None:
    """No strategies.yaml in the config dir: None, never a guess; adding one gives its cap."""
    absent = roster_exposure_cap({"HERMES_CONFIG_DIR": str(tmp_path)})
    present = roster_exposure_cap(_roster(tmp_path, ("swing", "1.0", "0.12")))
    assert (absent, present) == (None, Decimal("0.12"))


def test_roster_strategy_is_the_first_enabled_one(tmp_path: Path) -> None:
    """The Shadow test reads the day-0 record of the strategy serve trades: the first enabled."""
    two = _roster(tmp_path / "two", ("a", "0.5", "0.15"), ("b", "0.5", "0.15"))
    repo_config = REPO_ROOT / "config"
    assert (
        roster_strategy_id(two),
        roster_strategy_id({"HERMES_CONFIG_DIR": str(repo_config)}),
        roster_strategy_id({"HERMES_CONFIG_DIR": str(tmp_path / "none")}),
    ) == ("a", "swing-donchian-4h", None)


def test_lab_notebook_is_found_up_the_tree_or_none(tmp_path: Path) -> None:
    """The notebook is found from any folder below the release root; elsewhere it is None."""
    notebook = tmp_path / "release" / "docs" / "experiments" / "swing-mvp1" / "lab-notebook.md"
    notebook.parent.mkdir(parents=True)
    notebook.write_text("## Entries\n", encoding="utf-8")
    deep = tmp_path / "release" / "src" / "hermes"
    deep.mkdir(parents=True)
    (tmp_path / "bare").mkdir()
    assert (lab_notebook_path(deep), lab_notebook_path(tmp_path / "bare")) == (
        notebook.resolve(),
        None,
    )


def test_shadow_sources_follow_the_read_model(tmp_path: Path) -> None:
    """A demo serves its own shadow data; a ledger reads runs/ beside it; others read nothing."""
    demo = FixtureReadModel.load("empty")
    ledger_path = tmp_path / "data" / "hermes.sqlite3"
    ledger_path.parent.mkdir()
    ledger_path.write_bytes(b"")
    ledger = LedgerReadModel.open(ledger_path)
    env = _roster(tmp_path / "config", ("swing", "1.0", "0.15"))
    from_demo = shadow_sources(demo, env=env)
    from_ledger = shadow_sources(ledger, env=env, db_path=ledger_path)
    runs = from_ledger.runs
    assert isinstance(runs, RunsDirectory)
    assert (
        (from_demo.evidence, from_demo.runs, from_demo.notebook),
        (from_ledger.evidence, from_ledger.strategy_id),
        runs.directory,
        shadow_sources(cast("ConsoleReadModel", object()), env=env),
    ) == (
        (demo, demo, demo),
        (ledger, "swing"),
        tmp_path / "data" / "runs",
        ShadowSources(),
    )


def test_live_ticker_is_kraken_for_a_ledger_console(tmp_path: Path) -> None:
    """A ledger-backed console gets the Kraken public adapter; creating it opens no connection."""
    ticker = live_ticker(LedgerReadModel(tmp_path / "hermes.sqlite3"))
    try:
        assert (type(ticker), ticker.config.base_url if ticker else None) == (
            KrakenPublicAdapter,
            "https://api.kraken.com",
        )
    finally:
        if ticker is not None:
            ticker.close()


def test_live_ticker_is_absent_for_demo_data() -> None:
    """Demo positions are synthetic, so they never meet real prices."""
    assert [live_ticker(FixtureReadModel.load("ok-two-positions"))] == [None]
