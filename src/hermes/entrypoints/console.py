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
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final, TextIO

from rich.console import Console

from hermes.adapters.console import FixtureReadModel, UnknownScenarioError, available_scenarios
from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.adapters.systemd_journal import JournalctlReader, JournalUnavailableError
from hermes.application.console import ConsoleReadModel, Surface
from hermes.application.console.logs import LogLine, parse_journal_entry
from hermes.application.console.render import RenderContext, render_overview
from hermes.entrypoints._streams import resolve_err

__all__ = [
    "CONFIG_DIR_ENV_VAR",
    "DB_ENV_VAR",
    "DEMO_ENV_VAR",
    "DemoFlagError",
    "DemoSplit",
    "LiveReadModelUnavailableError",
    "apply_demo_flag",
    "console_commands",
    "default_config_dir",
    "default_db_path",
    "demo_read_model",
    "journal_window_lines",
    "recent_journal_lines",
    "resolve_read_model",
    "roster_exposure_cap",
    "run_overview",
    "split_demo_flag",
]

DEMO_ENV_VAR: Final = "HERMES_CONSOLE_DEMO"
DB_ENV_VAR: Final = "HERMES_DB_PATH"
CONFIG_DIR_ENV_VAR: Final = "HERMES_CONFIG_DIR"
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


def default_db_path(env: Mapping[str, str]) -> Path:
    """Return the ledger a console command reads without ``--db``.

    ``HERMES_DB_PATH`` (set by the VPS wrapper to the service ledger) wins; an
    empty value counts as unset; otherwise ``hermes serve``'s default path.
    """
    from hermes.entrypoints.serve import DEFAULT_DB_PATH  # noqa: PLC0415 - slow import

    value = env.get(DB_ENV_VAR, "").strip()
    return Path(value) if value else DEFAULT_DB_PATH


def default_config_dir(env: Mapping[str, str]) -> Path:
    """Return the config directory a console command loads without ``--config-dir``.

    ``HERMES_CONFIG_DIR`` wins; an empty value counts as unset.
    """
    from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR  # noqa: PLC0415

    value = env.get(CONFIG_DIR_ENV_VAR, "").strip()
    return Path(value) if value else DEFAULT_CONFIG_DIR


def apply_demo_flag(
    args: list[str], stream: TextIO, err: TextIO | None = None
) -> tuple[int | None, list[str]]:
    """Handle a leading ``--demo`` for the top-level dispatcher.

    Args:
        args: Arguments after the program name.
        stream: Output stream for the overview.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        ``(exit_code, rest)``: an exit code when the flag was fully handled
        here (overview printed, or a usage error), else ``None`` and the
        arguments left for normal dispatch.
    """
    err = resolve_err(err)
    try:
        demo = split_demo_flag(args)
    except DemoFlagError as exc:
        err.write(f"ERROR: {exc}. Scenarios: {', '.join(available_scenarios())}\n")
        return 2, []
    if demo.scenario is None:
        return None, demo.rest
    if not demo.rest:
        return run_overview(demo.scenario, stream, err), []
    if demo.rest[0] not in console_commands():
        err.write(f"ERROR: --demo only applies to console commands, not {demo.rest[0]!r}.\n")
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
    from hermes.entrypoints.supervisor import desk_briefs  # noqa: PLC0415 - it imports this module

    if db_path is None:
        db_path = default_db_path(env)
    try:
        return LedgerReadModel.open(
            db_path,
            journal=journal_window_lines,
            briefs=desk_briefs(db_path, config_dir=default_config_dir(env)),
        )
    except FileNotFoundError:
        msg = (
            f"there is no ledger at {db_path} to read; "
            f"use --demo <scenario> ({', '.join(available_scenarios())})"
        )
        raise LiveReadModelUnavailableError(msg) from None


def run_overview(scenario: str, stream: TextIO, err: TextIO | None = None) -> int:
    """Print the home screen of ``scenario`` to ``stream`` as static text.

    This is the same screen ``hermes tui`` shows, without the keys.

    Args:
        scenario: Scenario named by ``--demo``.
        stream: Output stream; colour follows Rich's TTY and ``NO_COLOR`` detection.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        0 on success, 2 for an unknown scenario.
    """
    try:
        model = FixtureReadModel.load(scenario)
    except UnknownScenarioError as exc:
        resolve_err(err).write(f"ERROR: {exc}\n")
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


def roster_exposure_cap(env: Mapping[str, str]) -> Decimal | None:
    """Return the pre-registered exposure cap the roster sets, as a share (``0.15``).

    The Home exposure meter measures against it (#712); the console never
    hardcodes the pre-registration's figure. The roster is
    ``strategies.yaml`` in the console's config dir (``HERMES_CONFIG_DIR`` or
    ``./config``), read-only.

    Args:
        env: Environment snapshot (config dir and ``HERMES_STRATEGIES__*`` overrides).

    Returns:
        ``sizing.max_total_exposure_pct`` when every enabled strategy sets the
        same cap; ``None`` when the roster is missing or invalid, no strategy is
        enabled, or the caps differ.
    """
    from hermes.infrastructure.config.errors import ConfigError  # noqa: PLC0415 - slow import
    from hermes.infrastructure.config.loader import load_yaml_model  # noqa: PLC0415
    from hermes.infrastructure.config.models import StrategiesConfig  # noqa: PLC0415

    path = default_config_dir(env) / "strategies.yaml"
    try:
        roster = load_yaml_model(path, StrategiesConfig, env_section="STRATEGIES", env=dict(env))
    except ConfigError:
        return None
    caps = {s.sizing.max_total_exposure_pct for s in roster.strategies if s.enabled}
    return caps.pop() if len(caps) == 1 else None


_JOURNAL_WINDOW_LINES: Final = 20_000


def journal_window_lines(window: timedelta) -> list[LogLine] | None:
    """Return the hermes journal lines of the last ``window``, or ``None`` when unreadable.

    The activity feed reads restarts, warnings and errors from here. Locally,
    and for a user who may not read the unit's journal, there is nothing to
    read and the feed simply omits those rows; over ``--vps`` the same call
    runs on the VPS.

    Args:
        window: How far back to read.

    Returns:
        Parsed lines, oldest first, or ``None`` when journalctl is missing or
        cannot be started.
    """
    try:
        raw = JournalctlReader().lines(
            last=_JOURNAL_WINDOW_LINES, since=f"-{int(window.total_seconds())}s"
        )
        return [line for line in map(parse_journal_entry, raw) if line is not None]
    except JournalUnavailableError, OSError:
        return None
