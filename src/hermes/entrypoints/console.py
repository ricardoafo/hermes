"""Console composition: the demo switch and the read-model factory.

``hermes --demo <scenario> [command]`` and ``HERMES_CONSOLE_DEMO`` put console
commands on a packaged fixture scenario. The flag is a leading global option
parsed before dispatch. It refuses non-console commands, so a demo can never
reach ``hermes serve``; the environment variable is only read for console
commands, so a stray value on a host never breaks the bar loop. With no
command, the flag prints the overview of the scenario.

Console commands (#370, #374, #375, #377) join :data:`CONSOLE_COMMANDS` and
call :func:`resolve_read_model`.

Reference: openspec/changes/console-read-model/change.md (D-4, D-9).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC
from typing import Final, TextIO

from rich.console import Console

from hermes.adapters.console import FixtureReadModel, UnknownScenarioError, available_scenarios
from hermes.application.console import ConsoleReadModel, Surface
from hermes.application.console.render import (
    MADRID,
    RenderContext,
    render_overview,
    render_position_detail,
    render_positions,
)
from hermes.application.console.serialize import position_detail_to_json, positions_to_json

__all__ = [
    "CONSOLE_COMMANDS",
    "DEMO_ENV_VAR",
    "DemoFlagError",
    "DemoSplit",
    "LiveReadModelUnavailableError",
    "apply_demo_flag",
    "resolve_read_model",
    "run_overview",
    "run_positions",
    "run_tui",
    "split_demo_flag",
]

DEMO_ENV_VAR: Final = "HERMES_CONSOLE_DEMO"
CONSOLE_COMMANDS: Final[frozenset[str]] = frozenset({"activity", "positions", "status", "tui"})
_FLAG: Final = "--demo"


class DemoFlagError(ValueError):
    """``--demo`` was given without a scenario name."""


class LiveReadModelUnavailableError(RuntimeError):
    """No ledger-backed read model exists yet; only ``--demo`` works."""


@dataclass(frozen=True, slots=True)
class DemoSplit:
    """Result of stripping the leading ``--demo`` option from argv.

    Attributes:
        scenario: The scenario named by the flag, or ``None`` without the flag.
        rest: The remaining arguments, starting with the command if any.
    """

    scenario: str | None
    rest: list[str]


def split_demo_flag(args: list[str]) -> DemoSplit:
    """Strip a leading ``--demo <scenario>`` or ``--demo=<scenario>`` from ``args``.

    Only the first argument is inspected: ``--demo`` after the command belongs
    to that command's own parser.

    Args:
        args: Arguments after the program name.

    Returns:
        The scenario (or ``None``) and the remaining arguments.

    Raises:
        DemoFlagError: ``--demo`` has no scenario name.
    """
    if not args:
        return DemoSplit(None, [])
    head, *tail = args
    if head.startswith(f"{_FLAG}="):
        scenario = head.removeprefix(f"{_FLAG}=")
        if not scenario:
            msg = "--demo needs a scenario name"
            raise DemoFlagError(msg)
        return DemoSplit(scenario, tail)
    if head == _FLAG:
        if not tail or tail[0].startswith("-"):
            msg = "--demo needs a scenario name"
            raise DemoFlagError(msg)
        return DemoSplit(tail[0], tail[1:])
    return DemoSplit(None, list(args))


def apply_demo_flag(args: list[str], stream: TextIO) -> tuple[int | None, list[str]]:
    """Handle a leading ``--demo`` for the top-level dispatcher.

    Args:
        args: Arguments after the program name.
        stream: Output stream for the overview and errors.

    Returns:
        ``(exit_code, rest)``: an exit code when the flag was fully handled
        here (overview printed, or a usage error), else ``None`` and the
        arguments left for normal dispatch.
    """
    try:
        demo = split_demo_flag(args)
    except DemoFlagError as exc:
        stream.write(f"ERROR: {exc}. Scenarios: {', '.join(available_scenarios())}\n")
        return 2, []
    if demo.scenario is None:
        return None, demo.rest
    if not demo.rest:
        return run_overview(demo.scenario, stream), []
    if demo.rest[0] not in CONSOLE_COMMANDS:
        stream.write(f"ERROR: --demo only applies to console commands, not {demo.rest[0]!r}.\n")
        return 2, []
    return None, demo.rest


def resolve_read_model(*, demo_flag: str | None, env: Mapping[str, str]) -> ConsoleReadModel:
    """Return the read model a console command should use.

    The flag wins over :data:`DEMO_ENV_VAR`; an empty variable counts as unset.

    Args:
        demo_flag: Scenario named by ``--demo``, if any.
        env: Environment snapshot.

    Returns:
        A fixture read model for the chosen scenario.

    Raises:
        UnknownScenarioError: The scenario does not exist.
        LiveReadModelUnavailableError: No demo was asked for and the ledger-backed
            read model has not landed yet.
    """
    scenario = demo_flag or env.get(DEMO_ENV_VAR, "").strip() or None
    if scenario is None:
        msg = (
            "the console has no live data source yet; "
            f"use --demo <scenario> ({', '.join(available_scenarios())})"
        )
        raise LiveReadModelUnavailableError(msg)
    return FixtureReadModel.load(scenario)


def run_overview(scenario: str, stream: TextIO) -> int:
    """Print the home screen of ``scenario`` to ``stream`` as static text.

    This is the same screen ``hermes tui`` shows, without the keys.

    Args:
        scenario: Scenario named by ``--demo``.
        stream: Output stream; colour follows Rich's TTY and ``NO_COLOR`` detection.

    Returns:
        0 on success, 2 for an unknown scenario.
    """
    try:
        model = FixtureReadModel.load(scenario)
    except UnknownScenarioError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 2
    console = Console(file=stream, emoji=False, markup=False, highlight=False)
    ctx = RenderContext.for_model(model, surface=Surface.OPERATOR_CLI, width=console.width)
    console.print(render_overview(model, ctx))
    return 0


_NO_FILLS_NOTE: Final = "Fills start with #353."
_POSITIONS_USAGE: Final = "Usage: hermes [--demo SCENARIO] positions [COIN] [--json] [--utc]"


def run_positions(
    args: list[str],
    stream: TextIO,
    *,
    demo_flag: str | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run ``hermes positions [COIN] [--json] [--utc]``.

    Without a demo scenario there is no live read model yet and no position can
    exist before the fill path lands, so the answer is the empty state plus a
    note, not an error (change spec D-1).

    Args:
        args: Arguments after ``positions``.
        stream: Output stream; colour follows Rich's TTY and ``NO_COLOR`` detection.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot; defaults to empty.

    Returns:
        0 on success, 1 when COIN has no open position, 2 for a usage error or
        an unknown scenario.
    """
    flags = {a for a in args if a.startswith("-")}
    coins = [a for a in args if not a.startswith("-")]
    if flags - {"--json", "--utc"} or len(coins) > 1:
        stream.write(f"ERROR: bad arguments. {_POSITIONS_USAGE}\n")
        return 2
    as_json = "--json" in flags
    coin = coins[0].upper() if coins else None
    try:
        model = resolve_read_model(demo_flag=demo_flag, env=env or {})
    except LiveReadModelUnavailableError:
        return _positions_without_data(stream, as_json=as_json)
    except UnknownScenarioError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 2
    console = Console(file=stream, emoji=False, markup=False, highlight=False)
    ctx = RenderContext.for_model(
        model,
        surface=Surface.OPERATOR_CLI,
        tz=UTC if "--utc" in flags else MADRID,
        width=console.width,
    )
    if coin is None:
        rows = model.positions()
        if as_json:
            stream.write(json.dumps(positions_to_json(rows, ctx), indent=2) + "\n")
        else:
            console.print(render_positions(rows, ctx))
        return 0
    return _print_detail(model, coin, ctx, console, as_json=as_json)


def _print_detail(
    model: ConsoleReadModel, coin: str, ctx: RenderContext, console: Console, *, as_json: bool
) -> int:
    detail = model.position(coin)
    if as_json:
        payload = (
            {"error": f"no open position for {coin}"}
            if detail is None
            else position_detail_to_json(detail, ctx)
        )
        console.file.write(json.dumps(payload, indent=2) + "\n")
    else:
        console.print(render_position_detail(detail, ctx))
    return 0 if detail is not None else 1


def _positions_without_data(stream: TextIO, *, as_json: bool) -> int:
    if as_json:
        stream.write(json.dumps({"positions": [], "note": _NO_FILLS_NOTE}, indent=2) + "\n")
    else:
        stream.write(f"No open shadow positions.\n{_NO_FILLS_NOTE}\n")
    return 0


_TUI_USAGE: Final = "Usage: hermes [--demo SCENARIO] tui"


def run_tui(
    args: list[str],
    stream: TextIO,
    *,
    demo_flag: str | None = None,
    env: Mapping[str, str] | None = None,
    runner: Callable[[ConsoleReadModel], None] | None = None,
) -> int:
    """Run ``hermes tui``, the read-only home screen.

    Without a demo scenario there is no live read model yet, so the command
    says so and exits with a usage error instead of showing an empty screen.

    Args:
        args: Arguments after ``tui``; none are accepted.
        stream: Output stream for errors.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot; defaults to empty.
        runner: Runs the app on the read model; defaults to the Textual home
            screen (imported lazily so the other commands stay fast).

    Returns:
        0 after the operator quits, 2 for a usage error, an unknown scenario or
        no data source.
    """
    if args:
        stream.write(f"ERROR: bad arguments. {_TUI_USAGE}\n")
        return 2
    try:
        model = resolve_read_model(demo_flag=demo_flag, env=env or {})
    except (LiveReadModelUnavailableError, UnknownScenarioError) as exc:
        stream.write(f"ERROR: {exc}\n")
        return 2
    if runner is None:
        from hermes.entrypoints.tui import run_home  # noqa: PLC0415 - Textual is slow to import

        runner = run_home
    runner(model)
    return 0
