"""Console commands are declared by their own module, not by a shared list (#525)."""

import io
from pathlib import Path

import pytest

from hermes.entrypoints import cli as cli_module
from hermes.entrypoints import console as console_module
from hermes.entrypoints.commands import discover


def test_console_commands_are_the_registered_commands_that_take_demo() -> None:
    """The console set is derived from each module's `wants_demo`, so adding a panel is one file."""
    expected = {name for name, command in discover().items() if command.wants_demo}
    assert console_module.console_commands() == expected


def test_known_panels_are_console_commands() -> None:
    """Guards the derivation itself: the four shipped panels must be in the derived set."""
    assert {"activity", "positions", "status", "tui"} <= console_module.console_commands()


@pytest.mark.parametrize("module", [console_module, cli_module])
def test_shared_modules_name_no_console_command(module: object) -> None:
    """console.py and cli.py never spell a console command, the conflict hotspot of #525."""
    source = Path(module.__file__ or "").read_text(encoding="utf-8")  # type: ignore[attr-defined]
    named = [name for name in console_module.console_commands() if f'"{name}"' in source]
    assert named == []


def test_demo_flag_is_refused_for_a_command_that_does_not_take_it() -> None:
    """`--demo` before a non-console command is a usage error, from the registry alone."""
    stream = io.StringIO()
    code, rest = console_module.apply_demo_flag(["--demo", "late", "serve"], stream)
    assert (code, rest) == (2, [])
