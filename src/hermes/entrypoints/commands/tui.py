"""``hermes tui``: the read-only home screen; see :mod:`hermes.entrypoints.console`."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Final, TextIO

from hermes.adapters.console import UnknownScenarioError
from hermes.application.console import ConsoleReadModel
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.commands import Command
from hermes.entrypoints.console import (
    LiveReadModelUnavailableError,
    recent_journal_lines,
    resolve_read_model,
    roster_exposure_cap,
)
from hermes.entrypoints.tui_settings import (
    DEFAULT_SETTINGS_PATH,
    ConsoleSettings,
    ThemeChoice,
    save_settings,
)
from hermes.entrypoints.version import read_release

if TYPE_CHECKING:
    from hermes.adapters.exchanges.kraken_public import KrakenPublicAdapter

__all__ = ["COMMAND", "live_ticker", "run_tui"]

_TUI_USAGE: Final = "Usage: hermes [--demo SCENARIO] tui [--db PATH] [--theme neon|control|auto]"
_TUI_HELP: Final = (
    f"{_TUI_USAGE}\n"
    "  --db PATH     ledger to read (default: the service ledger)\n"
    "  --theme NAME  neon, control (control room) or auto (night 23:00-08:00);\n"
    "                saved in ~/.config/hermes/console.toml\n"
    "  -h, --help    show this help\n"
    "Exit codes: 0 after the operator quits, 2 usage, unknown scenario or no data source."
)
_DB: Final = "--db"
_THEME: Final = "--theme"


class _BadArgumentsError(ValueError):
    """The arguments are not ``[--db PATH] [--theme NAME]``."""


def run_tui(
    args: list[str],
    stream: TextIO,
    *,
    demo_flag: str | None = None,
    env: Mapping[str, str] | None = None,
    runner: Callable[[ConsoleReadModel], None] | None = None,
    err: TextIO | None = None,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
) -> int:
    """Run ``hermes tui``, the read-only home screen.

    Without a demo scenario the screen reads the ledger at ``--db``, read-only
    (change ``live-home-screen`` D-6). A card the ledger cannot answer yet
    says so on the screen instead of stopping the app.

    Args:
        args: Arguments after ``tui``: ``--db PATH`` and ``--theme NAME``.
        stream: Output stream.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot; defaults to empty.
        runner: Runs the app on the read model; defaults to the Textual home
            screen (imported lazily so the other commands stay fast).
        err: Stream for ERROR lines; defaults to ``sys.stderr``.
        settings_path: Where ``--theme`` is saved; the app reads it from there.

    Returns:
        0 after the operator quits, 2 for a usage error, an unknown scenario or
        no data source (no demo and no ledger file).
    """
    err = resolve_err(err)
    if "-h" in args or "--help" in args:
        stream.write(_TUI_HELP + "\n")
        return 0
    try:
        db_path, theme = _parse(args)
    except _BadArgumentsError:
        err.write(f"ERROR: bad arguments. {_TUI_USAGE}\n")
        return 2
    try:
        model = resolve_read_model(demo_flag=demo_flag, env=env or {}, db_path=db_path)
    except (UnknownScenarioError, LiveReadModelUnavailableError) as exc:
        err.write(f"ERROR: {exc}\n")
        return 2
    if theme is not None:
        try:
            save_settings(settings_path, ConsoleSettings(theme=theme))
        except OSError as exc:
            err.write(f"WARNING: could not save the theme: {exc}\n")
    if runner is None:
        from hermes.entrypoints.tui import run_home  # noqa: PLC0415 - Textual is slow to import

        cap = roster_exposure_cap(env or {})

        def runner(read_model: ConsoleReadModel) -> None:
            ticker = live_ticker(read_model)
            try:
                run_home(
                    read_model,
                    journal=recent_journal_lines,
                    release=_release_name,
                    settings_path=settings_path,
                    exposure_cap=cap,
                    ticker=ticker,
                )
            finally:
                if ticker is not None:
                    ticker.close()

    runner(model)
    return 0


def live_ticker(model: ConsoleReadModel) -> KrakenPublicAdapter | None:
    """Return the live public ticker for the console, or ``None`` for demo data.

    The ticker runs in the console process, so over ``--vps`` (where the
    console runs on the VPS) it runs there too. Demo positions are synthetic,
    so they are never shown at real prices. Creating the adapter opens no
    connection; the console's worker makes the first call.

    Args:
        model: The read model the console shows.

    Returns:
        A Kraken public adapter, or ``None`` when the model is a demo scenario.
    """
    if model.demo_scenario is not None:
        return None
    from hermes.adapters.exchanges.kraken_public import (  # noqa: PLC0415 - httpx only when used
        KrakenPublicAdapter,
    )

    return KrakenPublicAdapter.create()


def _release_name() -> str:
    """Return the running release, read from the link ``hermes status`` reads (#649)."""
    return read_release().name


def _parse(args: list[str]) -> tuple[Path | None, ThemeChoice | None]:
    """Return the ``--db`` path and the ``--theme`` choice, each ``None`` when absent.

    Raises:
        _BadArgumentsError: Anything other than ``--db PATH`` / ``--db=PATH`` and
            ``--theme NAME`` / ``--theme=NAME``, each at most once.
    """
    values: dict[str, str] = {}
    rest = list(args)
    while rest:
        head = rest.pop(0)
        flag, equals, value = head.partition("=")
        if flag not in (_DB, _THEME) or flag in values:
            raise _BadArgumentsError
        if not equals:
            if not rest:
                raise _BadArgumentsError
            value = rest.pop(0)
        if not value or value.startswith("-"):
            raise _BadArgumentsError
        values[flag] = value
    db = values.get(_DB)
    theme = values.get(_THEME)
    try:
        choice = None if theme is None else ThemeChoice(theme)
    except ValueError as exc:
        raise _BadArgumentsError from exc
    return (None if db is None else Path(db)), choice


def _run(argv: list[str], *, stream: TextIO, demo: str | None, err: TextIO | None = None) -> int:
    return run_tui(argv, stream, demo_flag=demo, env=os.environ, err=err)


COMMAND = Command(
    name="tui",
    summary="Read-only home screen: portfolio, positions, activity, shadow test.",
    run=_run,
    wants_demo=True,
)
