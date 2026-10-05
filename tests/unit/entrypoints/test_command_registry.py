"""Unit tests for the one-module-per-command registry (#457)."""

from __future__ import annotations

import inspect
import io
import sys
import types
from pathlib import Path

import pytest

from hermes.entrypoints import cli as cli_module
from hermes.entrypoints import commands as commands_package
from hermes.entrypoints.commands import Command, CommandRegistryError, collect, discover

_PACKAGE_DIR = Path(commands_package.__file__).parent


def _module(command: object) -> types.ModuleType:
    module = types.ModuleType("fake")
    module.__dict__["COMMAND"] = command
    return module


def test_every_command_file_is_discovered() -> None:
    """Each non-private module in the package becomes the command of the same name."""
    files = {
        p.stem.replace("_", "-") for p in _PACKAGE_DIR.glob("*.py") if not p.stem.startswith("_")
    }
    assert set(discover()) == files


def test_help_lists_every_discovered_command() -> None:
    """`hermes --help` shows every command with its summary, from the registry alone."""
    out = io.StringIO()
    assert cli_module.main(["--help"], stream=out) == 0
    help_text = out.getvalue()
    width = max(len(name) for name in discover())
    missing = [
        c.name for c in discover().values() if f"  {c.name:{width}s}  {c.summary}" not in help_text
    ]
    assert missing == []


def test_cli_holds_no_command_names() -> None:
    """cli.py names no command, so adding one never edits it (the conflict hotspot)."""
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    named = [name for name in discover() if f'"{name}"' in source]
    assert named == []


def test_dispatch_runs_the_discovered_command() -> None:
    """The token after `hermes` selects the command with that name."""
    out, err = io.StringIO(), io.StringIO()
    assert cli_module.main(["simulate"], stream=out, err=err) == 2
    assert (out.getvalue(), "not yet implemented" in err.getvalue()) == ("", True)


def test_module_without_command_is_rejected() -> None:
    """A command module must define COMMAND."""
    with pytest.raises(CommandRegistryError, match="must define COMMAND"):
        collect([("broken", _module(None))])


def test_underscore_module_declares_hyphenated_command() -> None:
    """`archive_check.py` may declare `archive-check`; the CLI token keeps its hyphen."""
    command = Command("archive-check", "x", lambda rest, *, stream: 0)
    assert list(collect([("archive_check", _module(command))])) == ["archive-check"]


def test_name_must_match_module() -> None:
    """A command named differently from its file is rejected, so names stay unique."""
    command = Command("other", "x", lambda rest, *, stream: 0)
    with pytest.raises(CommandRegistryError, match="name the module after the command"):
        collect([("thing", _module(command))])


def test_json_flag_matches_the_commands_that_parse_json() -> None:
    """A command sets json=True exactly when its code accepts ``--json`` (#654).

    The top-level help lists the ``--json`` commands from this flag, so a command
    that gains ``--json`` without the flag (or keeps the flag after dropping it)
    would make the help lie.
    """
    mismatched: list[str] = []
    for name, command in discover().items():
        module = sys.modules[command.run.__module__]
        sources = [inspect.getsource(module)] + [
            inspect.getsource(value)
            for value in vars(module).values()
            if isinstance(value, types.ModuleType)
            and value.__name__.startswith("hermes.entrypoints.")
            and not value.__name__.startswith("hermes.entrypoints.commands")
        ]
        parses_json = any('"--json"' in source for source in sources)
        if parses_json != command.json:
            mismatched.append(name)
    assert mismatched == []


def test_help_lists_every_json_command() -> None:
    """``hermes --help`` names each command whose json flag is set."""
    stream = io.StringIO()
    cli_module.main(["--help"], stream=stream)
    flat = " ".join(stream.getvalue().split())
    names = sorted(name for name, command in discover().items() if command.json)
    assert (
        f"--json One JSON document on stdout. Commands that accept it: {', '.join(names)}" in flat
    )
