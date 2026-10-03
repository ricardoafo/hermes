"""Unit tests for the one-module-per-command registry (#457)."""

from __future__ import annotations

import io
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
    missing = [
        c.name for c in discover().values() if f"  {c.name:10s} {c.summary}" not in help_text
    ]
    assert missing == []


def test_cli_holds_no_command_names() -> None:
    """cli.py names no command, so adding one never edits it (the conflict hotspot)."""
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    named = [name for name in discover() if f'"{name}"' in source]
    assert named == []


def test_dispatch_runs_the_discovered_command() -> None:
    """The token after `hermes` selects the command with that name."""
    out = io.StringIO()
    assert cli_module.main(["simulate"], stream=out) == 2
    assert "not yet implemented" in out.getvalue()


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
