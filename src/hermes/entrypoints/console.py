"""Console composition: the demo switch and the read-model factory.

``hermes --demo <scenario> [command]`` and ``HERMES_CONSOLE_DEMO`` put console
commands on a packaged fixture scenario. The flag is a leading global option
parsed before dispatch. It refuses non-console commands, so a demo can never
reach ``hermes serve``; the environment variable is only read for console
commands, so a stray value on a host never breaks the bar loop. With no
command, the flag prints the overview of the scenario.

A console command is a module under :mod:`hermes.entrypoints.commands` whose
``Command`` sets ``wants_demo``; :func:`console_commands` reads the set from
the registry, so no list here names them (#525). Console commands call
:func:`resolve_read_model`: a demo scenario when one is selected, otherwise the
ledger-backed read model when the ledger file exists (#546).

Reference: openspec/changes/console-read-model/change.md (D-4, D-9);
openspec/changes/ledger-read-model/change.md (D-8, D-9).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

from rich.console import Console

from hermes.adapters.console import FixtureReadModel, UnknownScenarioError, available_scenarios
from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.adapters.systemd_journal import JournalctlReader, JournalUnavailableError
from hermes.application.console import ConsoleReadModel, Surface
from hermes.application.console.logs import LogLine, parse_journal_entry
from hermes.application.console.render import RenderContext, render_overview

__all__ = [
    "DEMO_ENV_VAR",
    "DemoFlagError",
    "DemoSplit",
    "LiveReadModelUnavailableError",
    "apply_demo_flag",
    "console_commands",
    "demo_read_model",
    "recent_journal_lines",
    "resolve_read_model",
    "run_overview",
    "split_demo_flag",
]

DEMO_ENV_VAR: Final = "HERMES_CONSOLE_DEMO"
_FLAG: Final = "--demo"


class DemoFlagError(ValueError):
    """``--demo`` was given without a scenario name."""


class LiveReadModelUnavailableError(RuntimeError):
    """No demo was selected and there is no ledger to read; only ``--demo`` works."""


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


def console_commands() -> frozenset[str]:
    """Return the names of the commands that take ``--demo``, from the registry.

    Imported lazily: the command modules import this module for
    :func:`resolve_read_model`.
    """
    from hermes.entrypoints.commands import discover  # noqa: PLC0415 - commands import this module

    return frozenset(name for name, command in discover().items() if command.wants_demo)


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
    if demo.rest[0] not in console_commands():
        stream.write(f"ERROR: --demo only applies to console commands, not {demo.rest[0]!r}.\n")
        return 2, []
    return None, demo.rest


def demo_read_model(*, demo_flag: str | None, env: Mapping[str, str]) -> FixtureReadModel | None:
    """Return the fixture read model of the selected demo scenario, if any.

    The flag wins over :data:`DEMO_ENV_VAR`; an empty variable counts as unset.
    Commands that read the ledger themselves (``activity``, ``status``) use this
    instead of :func:`resolve_read_model`.

    Args:
        demo_flag: Scenario named by ``--demo``, if any.
        env: Environment snapshot.

    Returns:
        The scenario's read model, or ``None`` when no demo is selected.

    Raises:
        UnknownScenarioError: The scenario does not exist.
    """
    scenario = demo_flag or env.get(DEMO_ENV_VAR, "").strip() or None
    return None if scenario is None else FixtureReadModel.load(scenario)


def resolve_read_model(
    *, demo_flag: str | None, env: Mapping[str, str], db_path: Path | None = None
) -> ConsoleReadModel:
    """Return the read model a console command should use.

    A demo scenario wins; otherwise the ledger is read, read-only.

    Args:
        demo_flag: Scenario named by ``--demo``, if any.
        env: Environment snapshot.
        db_path: Ledger file; defaults to ``hermes serve``'s default path.

    Returns:
        A fixture read model for the chosen scenario, or the ledger read model.

    Raises:
        UnknownScenarioError: The scenario does not exist.
        LiveReadModelUnavailableError: No demo was asked for and there is no
            ledger file at the path.
    """
    demo = demo_read_model(demo_flag=demo_flag, env=env)
    if demo is not None:
        return demo
    if db_path is None:
        from hermes.entrypoints.serve import DEFAULT_DB_PATH  # noqa: PLC0415 - slow import

        db_path = DEFAULT_DB_PATH
    try:
        return LedgerReadModel.open(db_path)
    except FileNotFoundError:
        msg = (
            f"there is no ledger at {db_path} to read; "
            f"use --demo <scenario> ({', '.join(available_scenarios())})"
        )
        raise LiveReadModelUnavailableError(msg) from None


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


def recent_journal_lines(count: int) -> list[LogLine] | None:
    """Return the last ``count`` hermes journal lines, or ``None`` without journalctl.

    The TUI's logs panel calls this; it lives here so the TUI imports no adapter.

    Args:
        count: How many entries to read.

    Returns:
        Parsed lines, oldest first, or ``None`` on a machine with no journal.
    """
    try:
        raw = JournalctlReader().lines(last=count)
        return [line for line in map(parse_journal_entry, raw) if line is not None]
    except JournalUnavailableError:
        return None
